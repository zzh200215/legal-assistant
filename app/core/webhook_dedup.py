"""Webhook nonce 去重（P1-C）：持久化/共享存储，多实例部署下仍有效。

- ``claim_nonce`` 以 INSERT + 唯一约束实现原子去重：并发重放同一 nonce 仅一个
  成功（跨实例安全，不依赖单机内存）。
- 非ce 过期按 ``WEBHOOK_REPLAY_TTL_SECONDS`` 惰性清理（写入前顺带删除本命名空间
  过期行，不阻塞请求路径）。
- 无 nonce（None/空）视为不可去重返回 True，由调用方按方案的 nonce 要求决策。
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.time import utc_now


def claim_nonce(db: Session, *, namespace: str, nonce: str | None, ttl_seconds: int) -> bool:
    """尝试登记 nonce；返回 True=首次使用，False=重复（重放）。

    调用方应仅在验签通过后调用（先验签后去重），避免暴露 nonce 探测面。
    """
    if not nonce:
        return True
    from app.models.webhook_nonce import WebhookNonce

    now = utc_now()
    # 惰性清理过期行（仅本命名空间，尽力而为）
    try:
        db.query(WebhookNonce).filter(
            WebhookNonce.namespace == namespace,
            WebhookNonce.expires_at < now,
        ).delete(synchronize_session=False)
    except Exception:  # noqa: BLE001 - 清理失败不影响去重主路径
        db.rollback()
    db.add(WebhookNonce(
        namespace=namespace,
        nonce=nonce,
        expires_at=now + timedelta(seconds=ttl_seconds),
    ))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False