"""Evidence verification mixin (compat layer over EvidenceVerifier)."""

from __future__ import annotations

from typing import Any

from app.models.agent import ToolCallLog
from app.services.agent_json import json_loads_dict as _json_loads_dict
from app.services.evidence_verifier import evidence_verifier


class EvidenceVerificationMixin:
    """确定性证据校验（实现已抽到 evidence_verifier，本 mixin 保持兼容）。"""

    @staticmethod
    def _has_evidence_source_logs(logs: list[ToolCallLog]) -> bool:
        return evidence_verifier.has_evidence_source_logs(logs)

    def _verify_evidence(self, logs: list[ToolCallLog]) -> dict[str, Any]:
        return evidence_verifier.verify(logs)

    def _latest_evidence_verification(self, logs: list[ToolCallLog]) -> dict[str, Any] | None:
        for log in reversed(logs):
            if log.tool_name != "evidence_verifier" or not log.observation:
                continue
            observation = _json_loads_dict(log.observation)
            data = observation.get("data")
            if isinstance(data, dict):
                return data
        return None
