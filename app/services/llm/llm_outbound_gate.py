"""统一 LLM 出站数据保护网关（P0）。

所有出站 LLM 请求（chat / generate / structured_generate /
generate_with_images / chat_stream / embed）在构建供应商载荷之前必须先经过
本网关。流程：

1. **数据分级**：action 基础等级（内置安全默认 + ``LLM_OUTBOUND_ACTION_DATA_LEVEL_JSON``
   覆盖）+ PII 内容升级（命中任何规则 → 至少 sensitive；high/critical 严重度 →
   highly_sensitive）。
2. **PII 检测/脱敏**：命中规则默认脱敏后才允许发送（发往供应商的是脱敏文本）。
3. **极敏感拦截**：highly_sensitive 默认禁止发送；仅 ``LLM_OUTBOUND_HIGHLY_SENSITIVE_ACTIONS_JSON``
   显式放行名单内的 action 允许，且仍先脱敏。
4. **检测故障 fail closed**：规则检测异常时默认阻断全部出站请求并记录原因
   （``LLM_OUTBOUND_DLP_FAILURE_ACTION=block``，可配 ``warn`` 逃生）。

本网关为纯判定/文本变换（无 DB/IO）；审计落库与阻断抛错由调用方
``ModelGateway._apply_outbound_gate`` 统一负责，保证审计字段一致且不落原始 PII。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

from app.core.config import get_settings
from app.core.data_levels import DataLevel, base_level_for_action, level_rank, parse_level
from app.services.org.data_protection_service import data_protection_service

BLOCK_CODE = "LLM_OUTBOUND_DATA_BLOCKED"
DETECTION_FAILED_REASON = "dlp_detection_failed"

# 命中即把内容升级为 highly_sensitive 的规则严重度（与 data_protection_service 一致）。
_HIGHLY_SENSITIVE_SEVERITIES = frozenset({"high", "critical"})


@dataclass(frozen=True)
class OutboundGateResult:
    """一次出站网关判定的结构化结果（审计用；禁止把原始文本放进审计字段）。"""

    data_level: DataLevel
    pii_hit_codes: tuple[str, ...] = ()
    pii_hit_count: int = 0
    redacted_count: int = 0
    blocked: bool = False
    blocked_reason: str | None = None
    detector_error: bool = False


class LLMOutboundGate:
    """统一出站数据保护网关（规则引擎之上的一层决策与文本变换）。"""

    def _allowed_highly_sensitive_actions(self) -> set[str]:
        """受控放行名单：仅显式配置的 action 可发送 highly_sensitive 内容。"""
        try:
            raw = json.loads(get_settings().LLM_OUTBOUND_HIGHLY_SENSITIVE_ACTIONS_JSON or "[]")
        except Exception:
            return set()
        if not isinstance(raw, list):
            return set()
        return {str(item).strip() for item in raw if str(item).strip()}

    def _base_level(self, action: str) -> DataLevel:
        """action 基础等级：配置覆盖优先，未配置走内置安全默认。"""
        settings = get_settings()
        try:
            raw = json.loads(settings.LLM_OUTBOUND_ACTION_DATA_LEVEL_JSON or "{}")
        except Exception:
            raw = {}
        if isinstance(raw, dict):
            configured = raw.get(action)
            parsed = parse_level(configured)
            if parsed is not None:
                return parsed
        return base_level_for_action(action)

    @staticmethod
    def _escalate(base: DataLevel, inspection: dict) -> DataLevel:
        """内容升级：任何 PII 命中 → 至少 sensitive；high/critical 严重度 → highly_sensitive。"""
        findings = inspection.get("findings") or []
        if not findings:
            return base
        level = max(base, DataLevel.SENSITIVE, key=level_rank)
        if any(item.get("severity") in _HIGHLY_SENSITIVE_SEVERITIES for item in findings):
            level = max(level, DataLevel.HIGHLY_SENSITIVE, key=level_rank)
        return level

    @staticmethod
    def _hit_codes(inspection: dict) -> tuple[str, ...]:
        return tuple(sorted({str(item.get("code") or "unknown") for item in inspection.get("findings") or []}))

    def guard(
        self,
        *,
        pieces: Sequence[str | None],
        action: str,
    ) -> tuple[list[str], OutboundGateResult]:
        """对一组文本片段执行 分级→检测→脱敏→决策。

        返回 ``(safe_pieces, result)``：``safe_pieces`` 与传入 ``pieces`` 等长同序
        （命中时已脱敏；未命中/未启用时原样返回，保证行为不变）；``result.blocked``
        为 True 时调用方必须阻断请求并审计，不得发送 ``safe_pieces``。
        """
        normalized = [str(piece or "") for piece in pieces]
        base = self._base_level(action)
        settings = get_settings()

        if not settings.LLM_OUTBOUND_DLP_ENABLED:
            # PII 检测/脱敏开关关闭：原样放行，仅记录基础等级。
            return list(normalized), OutboundGateResult(data_level=base)

        source = "\n".join(piece for piece in normalized if piece)
        try:
            inspection = data_protection_service.inspect(source)
        except Exception:
            # 检测异常：不伪造“通过”。默认 fail closed 阻断全部出站并记录原因。
            if settings.LLM_OUTBOUND_DLP_FAILURE_ACTION != "warn":
                return list(normalized), OutboundGateResult(
                    data_level=base,
                    blocked=True,
                    blocked_reason=DETECTION_FAILED_REASON,
                    detector_error=True,
                )
            return list(normalized), OutboundGateResult(
                data_level=base,
                detector_error=True,
            )

        level = self._escalate(base, inspection)

        if level == DataLevel.HIGHLY_SENSITIVE and action not in self._allowed_highly_sensitive_actions():
            # 极敏感数据默认禁止发送；仅受控放行名单内的 action 可放行。
            return list(normalized), OutboundGateResult(
                data_level=level,
                pii_hit_codes=self._hit_codes(inspection),
                pii_hit_count=int(inspection.get("total_count") or 0),
                redacted_count=0,
                blocked=True,
                blocked_reason=f"highly_sensitive_not_allowed:{action}",
            )

        # 放行：逐片段脱敏，保证发往供应商的是脱敏文本（PII 不进入外部请求）。
        safe_pieces: list[str] = []
        hit_counts: dict[str, int] = {}
        total_hits = 0
        redacted_pieces = 0
        for piece in normalized:
            if not piece:
                safe_pieces.append(piece)
                continue
            redacted = data_protection_service.redact(piece)
            for finding in redacted.get("findings") or []:
                code = str(finding.get("code") or "unknown")
                hit_counts[code] = hit_counts.get(code, 0) + int(finding.get("count") or 0)
            total_hits += int(redacted.get("total_count") or 0)
            if redacted.get("redacted"):
                redacted_pieces += 1
            safe_pieces.append(redacted.get("text") or piece)

        return safe_pieces, OutboundGateResult(
            data_level=level,
            pii_hit_codes=tuple(sorted(hit_counts)),
            pii_hit_count=total_hits,
            redacted_count=redacted_pieces,
            blocked=False,
        )


outbound_gate = LLMOutboundGate()
