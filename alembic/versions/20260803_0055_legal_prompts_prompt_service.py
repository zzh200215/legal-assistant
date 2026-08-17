"""AI-3: 法律 Prompt 迁入 prompt_service 版本化模板基线。

legal_service.py 的五类 LLM 调用改为经 prompt_service 渲染（按 user_id 灰度/A-B）。
本迁移把存量 DB 中的 3 个旧法律模板（legal_consultation / legal_contract_review /
legal_draft_generation）同步为规范内容（单大括号 JSON、{disclaimer} 变量、
consumer_dispute 分类），并补建 legal_followup / legal_contract_compare 两个新模板，
使灰度在存量库上生效。模板内容以 app/services/prompt_defaults.py 为唯一基线来源。

Revision ID: 20260803_0055
Revises: 20260802_0054
"""

from alembic import op
import sqlalchemy as sa

revision = "20260803_0055"
down_revision = "20260802_0054"
branch_labels = None
depends_on = None

LEGAL_TEMPLATE_NAMES = {
    "legal_consultation",
    "legal_contract_review",
    "legal_draft_generation",
    "legal_followup",
    "legal_contract_compare",
}


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "prompt_templates") or not _table_exists(bind, "prompt_template_versions"):
        return
    from app.services.llm.prompt_defaults import DEFAULT_PROMPT_TEMPLATES

    templates = sa.table(
        "prompt_templates",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("variables", sa.String),
        sa.column("active_version_id", sa.Integer),
        sa.column("rollout_percentage", sa.Integer),
    )
    versions = sa.table(
        "prompt_template_versions",
        sa.column("id", sa.Integer),
        sa.column("template_id", sa.Integer),
        sa.column("version", sa.Integer),
        sa.column("template", sa.Text),
        sa.column("is_active", sa.Boolean),
        sa.column("change_note", sa.String),
    )

    for data in DEFAULT_PROMPT_TEMPLATES:
        name = data["name"]
        if name not in LEGAL_TEMPLATE_NAMES:
            continue
        existing = list(
            bind.execute(
                sa.select(templates.c.id, templates.c.active_version_id).where(templates.c.name == name)
            ).mappings()
        )
        if not existing:
            bind.execute(
                templates.insert().values(
                    name=name, description=data["description"], variables=data["variables"],
                    active_version_id=None, rollout_percentage=0,
                )
            )
            tmpl_id = bind.execute(sa.select(templates.c.id).where(templates.c.name == name)).scalar_one()
            bind.execute(
                versions.insert().values(
                    template_id=tmpl_id, version=1, template=data["template"], is_active=True,
                    change_note="AI-3 迁移：初始版本",
                )
            )
            ver_id = bind.execute(
                sa.select(versions.c.id).where(versions.c.template_id == tmpl_id, versions.c.version == 1)
            ).scalar_one()
            bind.execute(templates.update().where(templates.c.id == tmpl_id).values(active_version_id=ver_id))
            continue

        tmpl_id = existing[0]["id"]
        active = list(
            bind.execute(
                sa.select(versions.c.id, versions.c.template).where(
                    versions.c.template_id == tmpl_id, versions.c.is_active.is_(True)
                )
            ).mappings()
        )
        if active and active[0]["template"] == data["template"]:
            continue
        max_version = bind.execute(
            sa.select(sa.func.max(versions.c.version)).where(versions.c.template_id == tmpl_id)
        ).scalar() or 0
        bind.execute(versions.update().where(versions.c.template_id == tmpl_id).values(is_active=False))
        bind.execute(
            versions.insert().values(
                template_id=tmpl_id, version=max_version + 1, template=data["template"], is_active=True,
                change_note=f"AI-3 迁移：同步为 v{max_version + 1} 规范基线",
            )
        )
        ver_id = bind.execute(
            sa.select(versions.c.id).where(
                versions.c.template_id == tmpl_id, versions.c.version == max_version + 1
            )
        ).scalar_one()
        bind.execute(
            templates.update().where(templates.c.id == tmpl_id).values(
                active_version_id=ver_id,
                description=data["description"],
                variables=data["variables"],
            )
        )


def downgrade() -> None:
    # 仅同步模板数据；downgrade 不回退（模板内容由 prompt_defaults 与后续版本管理掌控）。
    pass
