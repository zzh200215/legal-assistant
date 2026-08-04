"""#83/平台收款（对公转账）: 建 platform_payments 表

企业客户对公转账 → 凭证提交(pending) → 管理员确认(confirmed, 激活订阅+开票信息快照) / 驳回(rejected)。
平台收款为独立轨道，不复用 legal_invoices（该表为律所→客户方向，case_id/invoice_id 硬性 NOT NULL）。
"""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0059"
down_revision = "20260901_0058"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "platform_payments"):
        op.create_table(
            "platform_payments",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False, index=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True, comment="提交人"),
            sa.Column("plan_tier", sa.String(16), nullable=False, comment="pro / team"),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending", index=True,
                      comment="pending / confirmed / rejected"),
            sa.Column("voucher_no", sa.String(128), nullable=True, comment="转账流水号/凭证号"),
            sa.Column("voucher_document_id", sa.Integer(), sa.ForeignKey("documents.id"), nullable=True, comment="付款凭证文件"),
            sa.Column("invoice_snapshot_json", sa.Text(), nullable=True, comment="开票信息快照（抬头/税号/金额/期间）"),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("confirmed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "platform_payments"):
        op.drop_table("platform_payments")
