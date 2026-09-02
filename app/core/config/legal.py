"""P1 法律业务统一模型相关配置。"""

from pydantic import field_validator
from pydantic_settings import BaseSettings

from app.core.config.base import ENV_FILE_CONFIG

# 已知严重度：与 app/services/legal/legal_domain_service.SEVERITIES 一致。
# 此处重复字面量而不 import 服务层，避免 config -> service 反向依赖。
_KNOWN_SEVERITIES = frozenset({"low", "medium", "high", "critical"})


class LegalSettings(BaseSettings):
    """法律领域：风险项审核门禁。"""

    model_config = ENV_FILE_CONFIG

    # 需强制进入审核队列的严重度（逗号分隔）：风险项命中该集合时初始状态为
    # needs_review，并附带 risk_warning claim；未处理完不可发布
    # （legal_domain_service.persist_review_artifacts / assert_publishable 消费）。
    LEGAL_RISK_REVIEW_SEVERITIES: str = "high,critical"

    @field_validator("LEGAL_RISK_REVIEW_SEVERITIES")
    @classmethod
    def _validate_review_severities(cls, value: str) -> str:
        """拒绝空值与未知严重度：拼错会静默关闭审核门禁，必须启动期失败。"""
        items = {part.strip() for part in value.split(",") if part.strip()}
        if not items:
            raise ValueError("LEGAL_RISK_REVIEW_SEVERITIES 不能为空（会关闭风险项审核门禁）")
        unknown = sorted(items - _KNOWN_SEVERITIES)
        if unknown:
            raise ValueError(
                f"LEGAL_RISK_REVIEW_SEVERITIES 含未知严重度 {unknown}；"
                f"可选值：{sorted(_KNOWN_SEVERITIES)}"
            )
        return value

    @property
    def legal_review_severity_set(self) -> frozenset[str]:
        return frozenset(
            part.strip() for part in self.LEGAL_RISK_REVIEW_SEVERITIES.split(",") if part.strip()
        )
