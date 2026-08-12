"""P1 法律业务统一模型相关配置。"""

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config.base import ENV_FILE_CONFIG


class LegalSettings(BaseSettings):
    """法律领域：结构化模型持久化与审核门禁开关。"""

    model_config = ENV_FILE_CONFIG

    # 是否把工作台输出（咨询/审查/草稿）旁路写入结构化表（facts/claims/evidences/references/risk_items）。
    # 关闭时仅写既有 JSON 列，兼容旧行为；审核/发布门禁在无结构化数据时退化为宽松判断。
    LEGAL_DOMAIN_PERSIST_ENABLED: bool = True
    # 需强制进入审核队列的严重度（逗号分隔）。风险项命中该集合时初始状态为 needs_review。
    LEGAL_RISK_REVIEW_SEVERITIES: str = "high,critical"

    @property
    def legal_review_severity_set(self) -> frozenset[str]:
        return frozenset(
            part.strip() for part in self.LEGAL_RISK_REVIEW_SEVERITIES.split(",") if part.strip()
        )
