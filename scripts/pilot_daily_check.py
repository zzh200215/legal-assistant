"""#45 试点每日巡检：健康/备份/错误日志/当日成本/回流游标，失败可告警。

用法（Windows 计划任务或手动）：
    python -B scripts/pilot_daily_check.py --url http://127.0.0.1:8001
可选：--alert（失败时调用 ALERT_WEBHOOK_URL，从 .env 读取）
输出：data/pilot-daily-check-YYYYMMDD.json + 控制台摘要；退出码 0=健康 1=有失败项
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read_env(key: str) -> str:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return ""
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    return ""


def _get_json(url: str, timeout: int = 10):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _db_rows(database_url: str, sql: str):
    from decimal import Decimal

    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            return [dict((k, float(v) if isinstance(v, Decimal) else v) for k, v in r._mapping.items()) for r in conn.execute(text(sql))]
    finally:
        engine.dispose()


def _tail(path: Path, n: int = 50) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:]


def run_check(*, url: str, database_url: str, uvicorn_err: Path, alert: bool) -> dict:
    now = datetime.now(timezone.utc)
    checks: dict[str, dict] = {}
    findings: list[str] = []

    try:
        ready = _get_json(f"{url}/api/health/ready", timeout=15)
        data = ready.get("data") if isinstance(ready, dict) else None
        checks_inner = (data or {}).get("checks", {}) if data else {}
        ok = bool(checks_inner.get("database") == "ok" and checks_inner.get("redis") == "ok")
        checks["backend_ready"] = {"ok": ok, "detail": ready}
        if not ok:
            findings.append(f"backend_ready: {ready}")
    except Exception as exc:  # noqa: BLE001
        checks["backend_ready"] = {"ok": False, "detail": str(exc)}
        findings.append(f"backend_ready: {exc}")

    backups = sorted((ROOT / "data/backups").glob("pilot-backup-*"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest = backups[0] if backups else None
    backup_ok = latest is not None and (now - datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)) < timedelta(hours=26)
    checks["backup"] = {
        "ok": backup_ok,
        "latest": latest.name if latest else None,
        "age_hours": round((now - datetime.fromtimestamp(latest.stat().st_mtime, tz=timezone.utc)).total_seconds() / 3600, 1) if latest else None,
    }
    if not backup_ok:
        findings.append(f"backup: no backup within 26h (latest={latest.name if latest else None})")

    err_lines = _tail(uvicorn_err, n=200)
    errors = [line for line in err_lines if "ERROR" in line or "Traceback" in line or "Exception" in line]
    errors = errors[-10:]
    recent_error = False
    log_age_hours = None
    if uvicorn_err.exists():
        log_age_hours = round((now - datetime.fromtimestamp(uvicorn_err.stat().st_mtime, tz=timezone.utc)).total_seconds() / 3600, 1)
        recent_error = bool(errors) and log_age_hours is not None and log_age_hours <= 2
    checks["uvicorn_errors"] = {
        "ok": not recent_error,
        "count_tail": len(errors),
        "log_last_written_hours_ago": log_age_hours,
        "sample": errors[-3:],
        "warn_only": True,
    }

    cost = 0.0
    cost_ok = True
    try:
        rows = _db_rows(database_url, """
            SELECT model, COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens
            FROM token_usage
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL 1 DAY)
            GROUP BY model
        """)
        pricing = {"qwen-plus": (0.004, 0.012), "qwen-turbo": (0.001, 0.003), "text-embedding-v3": (0.0005, 0.0)}
        for item in rows:
            rate = pricing.get(item["model"], (0.004, 0.012))
            cost += float(item["prompt_tokens"] or 0) / 1000 * rate[0] + float(item["completion_tokens"] or 0) / 1000 * rate[1]
        checks["cost_24h"] = {"ok": True, "cost_cny": round(cost, 4), "by_model": rows}
    except Exception as exc:  # noqa: BLE001
        checks["cost_24h"] = {"ok": False, "detail": str(exc)}
        cost_ok = False
        findings.append(f"cost_24h: {exc}")

    state_file = ROOT / "scripts/review_feedback_state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else {}
    except Exception:  # noqa: BLE001
        state = {}
    checks["feedback_cursor"] = {"ok": True, "last_action_id": state.get("last_action_id", 0)}

    result = {
        "captured_at": now.isoformat(),
        "url": url,
        "checks": checks,
        "healthy": not findings,
        "findings": findings,
    }
    output = ROOT / f"data/pilot-daily-check-{now.strftime('%Y%m%d')}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if findings and alert:
        webhook = _read_env("ALERT_WEBHOOK_URL")
        if webhook:
            try:
                payload = json.dumps({"text": f"[pilot-daily-check] FAIL\n" + "\n".join(findings)}, ensure_ascii=False).encode("utf-8")
                req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10)
                result["alert_sent"] = True
            except Exception as exc:  # noqa: BLE001
                result["alert_sent"] = False
                result["alert_error"] = str(exc)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Pilot daily check")
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    parser.add_argument("--uvicorn-err", default=str(Path(os.environ.get("TEMP", "C:/Users/TX/AppData/Local/Temp")) / "opencode/uvicorn_err.log"))
    parser.add_argument("--alert", action="store_true")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "").strip() or _read_env("DATABASE_URL")
    if not database_url:
        print(json.dumps({"status": "error", "message": "DATABASE_URL is not set (env or .env)"}))
        return 2
    result = run_check(url=args.url, database_url=database_url, uvicorn_err=Path(args.uvicorn_err), alert=args.alert)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
