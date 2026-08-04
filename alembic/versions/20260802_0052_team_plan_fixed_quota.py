"""M-1: 团队版"无限"配额改为合同化固定上限（5000 咨询 / 2000 审查 / 2000 文书）。

依据真实 LLM 计费核算（单次咨询≈0.012元、单次审查全流程≈0.10-0.15元），
上限按最坏成本 ≈450元/月，仍远低于团队版 999元/月售价，保证毛利为正。
数据迁移：更新存量 subscription_plans 中 tier='team' 的配额行。
subscription 系列表由应用启动时 create_all 创建而非 alembic 管理，因此表不存在时
跳过本迁移（应用会用 PLAN_QUOTAS 的新有限值建表/seed）。

Revision ID: 20260802_0052
Revises: 20260730_0051
"""

from alembic import op
import sqlalchemy as sa

revision = "20260802_0052"
down_revision = "20260730_0051"
branch_labels = None
depends_on = None

NEW_DESCRIPTION = "每月咨询5000次、合同审查2000次、文书生成2000次，团队协作功能全开"


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "subscription_plans"):
        return
    op.execute(
        sa.text(
            "UPDATE subscription_plans "
            "SET quota_consultation = 5000, quota_review = 2000, quota_draft = 2000, "
            "description = :desc WHERE tier = 'team'"
        ).bindparams(desc=NEW_DESCRIPTION)
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "subscription_plans"):
        return
    op.execute(
        sa.text(
            "UPDATE subscription_plans "
            "SET quota_consultation = -1, quota_review = -1, quota_draft = -1, "
            "description = '无限次数，团队协作功能全开' WHERE tier = 'team'"
        )
    )
