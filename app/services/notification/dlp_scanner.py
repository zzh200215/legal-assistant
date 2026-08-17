"""统一 DLP 发送前硬门禁（app/services/dlp_scanner.py）。

在 ``data_protection_service`` 规则扫描器之上提供发送前统一决策：
- 决策：allow / block / review_required；命中规则、风险等级、脱敏命中摘要、扫描器版本/时间。
- 覆盖范围：主题/正文/HTML 正文、收件人/抄送/密送、附件文件名/MIME/文本摘要。
- 强制审批触发：高风险命中、附件、批量收件人（review_required，不自动发送）。
- 未配置真实扫描器（DLP_SCANNER_MODE != enabled）时显式 ``not_configured``，
  **不伪造"扫描通过"**，由调用方按 fail closed 阻断对外发送。
- 扫描异常默认 fail closed（DLP_SCAN_FAILURE_ACTION=block）。
- 日志/审计只保留脱敏摘要，不保存完整敏感命中内容。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from app.core.config import get_settings
from app.services.org.data_protection_service import data_protection_service

DECISION_ALLOW = "allow"
DECISION_BLOCK = "block"
DECISION_REVIEW = "review_required"

STATUS_CLEAN = "clean"
STATUS_WARNING = "warning"
STATUS_BLOCKED = "blocked"
STATUS_REVIEW = "review_required"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_ERROR = "error"

# 批量收件人阈值：超过则强制 review（不自动发送）
REVIEW_RECIPIENT_THRESHOLD = 5


@dataclass(frozen=True)
class DlpScanResult:
    decision: str
    status: str
    findings: list[dict] = field(default_factory=list)
    risk_level: str | None = None
    masked_summary: str = ""
    scanner_version: str = ""
    scanned_at: str = ""
    error_code: str | None = None
    blocked: bool = False

    @property
    def requires_review(self) -> bool:
        return self.decision == DECISION_REVIEW


class DlpScanner:
    """统一 DLP 扫描器：包装规则扫描器，提供发送前硬门禁决策。"""

    def healthy(self) -> bool:
        """是否配置了真实扫描器。未配置时不得伪造"扫描通过"。"""
        return get_settings().DLP_SCANNER_MODE == "enabled"

    def scan(
        self,
        *,
        payloads: Sequence[str | None],
        action: str | None = None,
        recipient_count: int = 1,
        has_attachment: bool = False,
    ) -> DlpScanResult:
        """扫描一组外发载荷并返回决策。

        - ``action``：policy 级 DLP 动作（block / warn）；None 时取
          ``DLP_SCAN_FAILURE_ACTION``（默认 block，fail closed）。
        - ``recipient_count`` / ``has_attachment`` 触发强制 review。
        """
        settings = get_settings()
        action = action or settings.DLP_SCAN_FAILURE_ACTION
        scanned_at = datetime.now(timezone.utc).isoformat()
        source = "\n".join(p for p in payloads if p)

        if not self.healthy():
            # 未配置真实扫描器：不伪造通过。fail closed → 对外默认阻断。
            blocked = settings.DLP_SCAN_FAILURE_ACTION == "block"
            return DlpScanResult(
                decision=DECISION_BLOCK if blocked else DECISION_REVIEW,
                status=STATUS_NOT_CONFIGURED,
                scanner_version=settings.DLP_SCANNER_VERSION,
                scanned_at=scanned_at,
                error_code="DLP_NOT_CONFIGURED",
                blocked=blocked,
                masked_summary="dlp_not_configured",
            )

        try:
            inspection = data_protection_service.inspect(source)
        except Exception as exc:  # noqa: BLE001 - 扫描异常必须 fail closed，不让异常吞掉决策
            blocked = settings.DLP_SCAN_FAILURE_ACTION == "block"
            return DlpScanResult(
                decision=DECISION_BLOCK if blocked else DECISION_REVIEW,
                status=STATUS_ERROR,
                scanner_version=settings.DLP_SCANNER_VERSION,
                scanned_at=scanned_at,
                error_code="DLP_SCAN_ERROR",
                blocked=blocked,
                masked_summary=f"dlp_scan_error:{type(exc).__name__}",
            )

        findings = list(inspection.get("findings") or [])
        risk_level = inspection.get("max_severity")
        force_review = has_attachment or recipient_count > REVIEW_RECIPIENT_THRESHOLD

        high_risk = risk_level in ("high", "critical")
        if action == "block" and high_risk:
            decision = DECISION_BLOCK
            status = STATUS_BLOCKED
        elif high_risk or force_review:
            decision = DECISION_REVIEW
            status = STATUS_REVIEW
        else:
            decision = DECISION_ALLOW
            status = STATUS_BLOCKED if findings else STATUS_CLEAN
            if findings:
                status = STATUS_WARNING

        return DlpScanResult(
            decision=decision,
            status=status,
            findings=findings,
            risk_level=risk_level,
            masked_summary=data_protection_service.audit_summary(inspection),
            scanner_version=settings.DLP_SCANNER_VERSION,
            scanned_at=scanned_at,
            blocked=(decision == DECISION_BLOCK),
        )


dlp_scanner = DlpScanner()
