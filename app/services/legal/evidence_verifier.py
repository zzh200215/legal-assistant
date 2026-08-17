"""EvidenceVerifier：对需要事实依据的结论校验引用、检索来源、证据关联与可信度。

确定性校验，不生成新的业务结论——未证实内容绝不标记为已验证。证据不足时返回
明确状态（passed=False + failed_claims），由工作流阻断写/最终答复。
"""

from __future__ import annotations

from typing import Any

from app.models.agent import ToolCallLog
from app.services.agent.agent_json import json_loads_dict as _json_loads_dict
from app.services.agent.agent_prompts import EVIDENCE_SOURCE_TOOLS


class EvidenceVerifier:
    @staticmethod
    def has_evidence_source_logs(logs: list[ToolCallLog]) -> bool:
        return any(log.tool_name in EVIDENCE_SOURCE_TOOLS and log.status == "success" for log in logs)

    def verify(self, logs: list[ToolCallLog]) -> dict[str, Any]:
        """返回结构化校验结果；证据不足时 passed=False，绝不伪造通过。"""
        checks: list[dict[str, Any]] = []

        def verify_claims(tool_name: str, claim_type: str, claims: Any) -> None:
            if not isinstance(claims, list):
                return
            for index, claim in enumerate(claims, start=1):
                if not isinstance(claim, dict):
                    checks.append({"tool_name": tool_name, "claim_type": claim_type, "index": index, "passed": False})
                    continue
                has_evidence = bool(str(claim.get("evidence") or claim.get("source_text") or "").strip())
                checks.append(
                    {"tool_name": tool_name, "claim_type": claim_type, "index": index, "passed": has_evidence}
                )

        for log in logs:
            if log.status != "success" or log.tool_name not in EVIDENCE_SOURCE_TOOLS:
                continue
            observation = _json_loads_dict(log.observation)
            data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
            if log.tool_name == "document_search_tool":
                chunks = data.get("chunks")
                if isinstance(chunks, list):
                    for index, chunk in enumerate(chunks, start=1):
                        metadata = chunk.get("metadata") if isinstance(chunk, dict) else {}
                        has_locator = bool(
                            isinstance(chunk, dict)
                            and str(chunk.get("content") or "").strip()
                            and isinstance(metadata, dict)
                            and (metadata.get("page_number") is not None or metadata.get("section_title") or metadata.get("chunk_id"))
                        )
                        checks.append(
                            {"tool_name": log.tool_name, "claim_type": "retrieval_chunk", "index": index, "passed": has_locator}
                        )
            elif log.tool_name == "document_risk_tool":
                verify_claims(log.tool_name, "risk", data.get("risks"))
            elif log.tool_name == "document_conflict_tool":
                conflicts = data.get("conflicts")
                if isinstance(conflicts, list):
                    for index, conflict in enumerate(conflicts, start=1):
                        sources = [
                            conflict.get("source_a") if isinstance(conflict, dict) else None,
                            conflict.get("source_b") if isinstance(conflict, dict) else None,
                        ]
                        for source_index, source in enumerate(sources, start=1):
                            has_locator = isinstance(source, dict) and bool(
                                str(source.get("source_text") or "").strip()
                                and (
                                    source.get("chunk_id") is not None
                                    or source.get("page_number") is not None
                                    or source.get("section_title")
                                )
                            )
                            checks.append(
                                {
                                    "tool_name": log.tool_name,
                                    "claim_type": "cross_document_conflict",
                                    "index": index,
                                    "source_index": source_index,
                                    "passed": has_locator,
                                }
                            )
            else:
                verify_claims(log.tool_name, "decision", data.get("decisions"))
                verify_claims(log.tool_name, "action_item", data.get("action_items"))
                verify_claims(log.tool_name, "risk", data.get("risks"))

        failed = [item for item in checks if not item["passed"]]
        return {
            "applicable": bool(checks),
            "passed": not failed,
            "checked_claims": len(checks),
            "failed_claims": len(failed),
            "issues": failed[:20],
        }


evidence_verifier = EvidenceVerifier()
