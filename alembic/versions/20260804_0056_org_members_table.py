"""#40: 补建 organization_members 表（模型已有，生产库缺失的 schema 漂移修复）。

OrganizationMember 模型（app/models/org.py）自引入起就存在，但迁移 0012 只建了
organizations/departments，organization_members 从未有迁移——试点账号供给
（scripts/create_pilot_orgs.py）需要此表。对照模型定义补齐。
"""
from alembic import op
import sqlalchemy as sa

revision = "20260804_0056"
down_revision = "20260803_0055"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "organization_members"):
        return
    op.create_table(
        "organization_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("legal_role", sa.String(32), nullable=False, server_default="client"),
        sa.Column("invited_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("invite_token", sa.String(128), nullable=True, index=True, unique=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_org_member"),
    )


def downgrade() -> None:
    op.drop_table("organization_members")
