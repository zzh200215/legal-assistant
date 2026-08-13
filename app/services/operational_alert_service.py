from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import redis
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.external_resilience import external_resilience
from app.core.time import utc_now
from app.services.analytics_service import analytics_service
from app.services.oplog_service import oplog_service


class OperationalAlertService:
    """Dispatches a small, redacted operational summary to a robot webhook."""

    _severity_rank = {"low": 1, "medium": 2, "high": 3}

    def _eligible(self, alerts: list[dict], minimum: str) -> list[dict]:
        threshold = self._severity_rank.get(minimum.lower(), 3)
        since = utc_now() - timedelta(minutes=10)
        return [
            item for item in alerts
            if self._severity_rank.get(str(item.get("severity") or "").lower(), 0) >= threshold
            and item.get("created_at") and item["created_at"].replace(tzinfo=None) >= since
        ]

    @staticmethod
    def _format_message(alerts: list[dict]) -> str:
        lines = [f"### 律智检运营告警\n近 10 分钟检测到 {len(alerts)} 项风险："]
        for alert in alerts[:20]:
            lines.append(
                f"- [{alert.get('severity', 'unknown').upper()}] "
                f"{alert.get('source_label') or alert.get('source')} / "
                f"{alert.get('category')} / {alert.get('target_type')} #{alert.get('target_id') or '-'}"
            )
        if len(alerts) > 20:
            lines.append(f"- 其余 {len(alerts) - 20} 项请在系统中心查看")
        return "\n".join(lines)

    def dispatch(self, *, db: Session) -> dict:
        settings = get_settings()
        webhook_url = settings.ALERT_WEBHOOK_URL.strip()
        if not webhook_url:
            return {"status": "disabled", "sent_count": 0}

        alerts = analytics_service.list_alerts(
            db=db, include_all_users=True, days=1, limit=1000,
        )
        routing_health = analytics_service.get_llm_routing_health(db)
        if routing_health["warnings"]:
            alerts.append(
                {
                    "source": "llm_routing",
                    "source_label": "模型路由",
                    "category": "model_routing_degraded",
                    "severity": "high",
                    "target_type": "routing_health",
                    "target_id": None,
                    "created_at": utc_now(),
                }
            )
        eligible = self._eligible(alerts, settings.ALERT_WEBHOOK_MIN_SEVERITY)
        if not eligible:
            return {"status": "no_alerts", "sent_count": 0}

        # At-most-once delivery per alert per hour prevents repeated Beat scans from spamming a robot.
        cache = redis.from_url(settings.REDIS_URL)
        hour = utc_now().strftime("%Y%m%d%H")
        unique = []
        for item in eligible:
            key = f"aibg:operational-alert:{hour}:{item.get('source')}:{item.get('category')}:{item.get('target_type')}:{item.get('target_id')}"
            if cache.get(key):
                continue
            unique.append((key, item))
        if not unique:
            return {"status": "deduplicated", "sent_count": 0}

        payload = {"msgtype": "markdown", "markdown": {"content": self._format_message([item for _, item in unique])}}

        def _post() -> None:
            response = httpx.post(webhook_url, json=payload, timeout=settings.ALERT_WEBHOOK_TIMEOUT_SECONDS)
            response.raise_for_status()

        # 韧性层包裹：5xx/连接类可重试，写超时降级 AMBIGUOUS 不盲目重试；
        # Redis 小时去重键仅在成功后写入，失败由下一 beat tick 自然重试。
        external_resilience.call(_post, service="operational_alert", op="dispatch", method="POST")
        for key, _ in unique:
            cache.set(key, "1", ex=3600)
        oplog_service.log(
            module="operations", action="operational_alert_dispatched", db=db,
            target_type="webhook", detail=f"count={len(unique)}; sensitive_values=redacted",
        )
        return {"status": "sent", "sent_count": len(unique)}


operational_alert_service = OperationalAlertService()
