"""密钥轮换审计（P1-A）。

- 复用 security_audit_service.write_event（哈希链、追加式、只存
  sanitized_metadata + detail_json_hash）。
- 契约：**审计事件绝不记录密钥原文**——调用方只允许传入版本号/统计等元数据，
  任何密钥材料写入本函数即视为调用方错误。
"""

from __future__ import annotations

import json
from typing import Any

AUDIT_EVENT_TYPE = "key_rotation"


def write_key_rotation_audit(
    *,
    action: str,
    result: str,
    target_version: str,
    sanitized_metadata: dict[str, Any] | None = None,
    reason_code: str | None = None,
    actor_id: str = "key_management",
) -> None:
    """写一条密钥轮换审计事件（追加式，不落密钥原文）。

    ``sanitized_metadata`` 只允许版本号/行数/动作等元数据；禁止传入密钥明文。
    审计写失败按 security_audit_service 的降级策略处理（本函数不抛错阻断轮换，
    但会在 stderr 记录警告，保证"绝不静默吞错"）。
    """
    from app.services.org.security_audit_service import write_event

    if sanitized_metadata is None:
        sanitized_metadata = {}
    try:
        write_event(
            event_type=AUDIT_EVENT_TYPE,
            actor_type="system",
            actor_id=actor_id,
            result=result,
            target_type="data_encryption_key",
            target_id=target_version,
            action=action,
            reason_code=reason_code,
            sanitized_metadata=json.dumps(sanitized_metadata, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001 - 审计失败不阻断轮换主流程，但必须显式告警
        import sys

        print(
            f"[audit-warning] key_rotation 审计写入失败（{action}/{result}@{target_version}）："
            f"{type(exc).__name__}",
            file=sys.stderr,
        )
