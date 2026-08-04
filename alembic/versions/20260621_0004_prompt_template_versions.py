"""add prompt template versions

Revision ID: 20260621_0004
Revises: 20260620_0003
Create Date: 2026-06-21 00:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260621_0004"
down_revision: Union[str, None] = "20260620_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    existing_tables = set(inspector.get_table_names())

    if "prompt_template_versions" not in existing_tables:
        op.create_table(
            "prompt_template_versions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("template_id", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("template", sa.Text(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("change_note", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=True),
            sa.ForeignKeyConstraint(["template_id"], ["prompt_templates.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    version_indexes = {item["name"] for item in inspector.get_indexes("prompt_template_versions")}
    version_id_index = op.f("ix_prompt_template_versions_id")
    if version_id_index not in version_indexes:
        op.create_index(version_id_index, "prompt_template_versions", ["id"], unique=False)
    version_template_index = op.f("ix_prompt_template_versions_template_id")
    if version_template_index not in version_indexes:
        op.create_index(version_template_index, "prompt_template_versions", ["template_id"], unique=False)

    prompt_template_columns = {column["name"] for column in inspector.get_columns("prompt_templates")}
    if "active_version_id" not in prompt_template_columns:
        op.add_column("prompt_templates", sa.Column("active_version_id", sa.Integer(), nullable=True))

    prompt_template_fks = {fk["name"] for fk in inspector.get_foreign_keys("prompt_templates")}
    if "fk_prompt_templates_active_version_id" not in prompt_template_fks:
        op.create_foreign_key(
            "fk_prompt_templates_active_version_id",
            "prompt_templates",
            "prompt_template_versions",
            ["active_version_id"],
            ["id"],
        )

    prompt_templates = sa.table(
        "prompt_templates",
        sa.column("id", sa.Integer()),
        sa.column("template", sa.Text()),
        sa.column("active_version_id", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    prompt_template_versions = sa.table(
        "prompt_template_versions",
        sa.column("id", sa.Integer()),
        sa.column("template_id", sa.Integer()),
        sa.column("version", sa.Integer()),
        sa.column("template", sa.Text()),
        sa.column("is_active", sa.Boolean()),
        sa.column("change_note", sa.String(length=512)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    rows = connection.execute(sa.select(
        prompt_templates.c.id,
        prompt_templates.c.template,
        prompt_templates.c.active_version_id,
        prompt_templates.c.created_at,
        prompt_templates.c.updated_at,
    )).fetchall()

    for row in rows:
        version_id = connection.execute(
            sa.select(prompt_template_versions.c.id).where(
                prompt_template_versions.c.template_id == row.id,
                prompt_template_versions.c.version == 1,
            )
        ).scalar()
        if version_id is None:
            result = connection.execute(
                prompt_template_versions.insert().values(
                    template_id=row.id,
                    version=1,
                    template=row.template,
                    is_active=True,
                    change_note="迁移初始化版本",
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )
            version_id = result.inserted_primary_key[0] if result.inserted_primary_key else None
            if version_id is None:
                version_id = connection.execute(
                    sa.select(prompt_template_versions.c.id).where(
                        prompt_template_versions.c.template_id == row.id,
                        prompt_template_versions.c.version == 1,
                    )
                ).scalar()
        if row.active_version_id == version_id:
            continue
        connection.execute(
            prompt_templates.update()
            .where(prompt_templates.c.id == row.id)
            .values(active_version_id=version_id)
        )

    prompt_template_columns = {column["name"] for column in sa.inspect(connection).get_columns("prompt_templates")}
    if "template" in prompt_template_columns:
        op.drop_column("prompt_templates", "template")


def downgrade() -> None:
    op.add_column("prompt_templates", sa.Column("template", sa.Text(), nullable=True))

    connection = op.get_bind()
    prompt_templates = sa.table(
        "prompt_templates",
        sa.column("id", sa.Integer()),
        sa.column("active_version_id", sa.Integer()),
        sa.column("template", sa.Text()),
    )
    prompt_template_versions = sa.table(
        "prompt_template_versions",
        sa.column("id", sa.Integer()),
        sa.column("template_id", sa.Integer()),
        sa.column("template", sa.Text()),
        sa.column("is_active", sa.Boolean()),
    )

    rows = connection.execute(sa.select(
        prompt_templates.c.id,
        prompt_templates.c.active_version_id,
    )).fetchall()

    for row in rows:
        version = connection.execute(
            sa.select(prompt_template_versions.c.template).where(prompt_template_versions.c.id == row.active_version_id)
        ).fetchone()
        if version:
            connection.execute(
                prompt_templates.update()
                .where(prompt_templates.c.id == row.id)
                .values(template=version.template)
            )

    op.drop_constraint("fk_prompt_templates_active_version_id", "prompt_templates", type_="foreignkey")
    op.drop_column("prompt_templates", "active_version_id")

    op.drop_index(op.f("ix_prompt_template_versions_template_id"), table_name="prompt_template_versions")
    op.drop_index(op.f("ix_prompt_template_versions_id"), table_name="prompt_template_versions")
    op.drop_table("prompt_template_versions")
