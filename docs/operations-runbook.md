# Operations Runbook

## Deployment Health

Run the complete stack with `docker compose up --build -d`. The API is healthy only when MySQL and Redis are ready. The Worker responds to a Celery ping, and Beat refreshes a Redis heartbeat every minute. A Beat heartbeat older than three minutes marks the service unhealthy.

Useful checks:

```powershell
docker compose ps
docker compose logs -f api celery_worker celery_beat
curl http://localhost:8001/api/health/ready
```

`/api/health/live` checks only the API process. `/api/health/ready` checks MySQL and Redis. `/api/health` remains the detailed diagnostic endpoint and can report a degraded external model provider without restarting the API container.

## Celery Task Queues & Workers

Tasks route across **five dedicated queues** (`llm / document / connector / notification / billing`) plus a non-default fallback (`connector`), so no task silently lands in the stock `celery` queue. Routing, per-queue time limits, and the global default queue are declared in `app/core/celery_app.py`; disable the whole scheme with `TASK_QUEUE_ROUTING_ENABLED=false`.

| Queue | Example tasks | time_limit / soft |
|---|---|---|
| `document` | parse_document, document_chunk, document_index, document_export, recover_stale_document_jobs, parse_contract_versions | 600 / 540 |
| `llm` | summarize_document, analyze_document, process_open_contract_review | 300 / 270 |
| `connector` | connector_sync_task, retry_failed_webhook_deliveries, dispatch_feishu_reminders, dispatch_operational_alerts, run_database_archive, create_pilot_backup | 240 / 210 |
| `notification` | dispatch_notification_events, check_legal_deadline_reminders, scan_expired_portal_links, scan_contract_expiry_alerts, check_legal_approval_timeouts, confirm_account_deletions | 120 / 100 |
| `billing` | scan_overdue_invoices, scan_expired_subscriptions | 300 / 270 |

Recommended deployment is **two workers** so CPU-heavy parsing does not block LLM/network-bound work:

```powershell
# CPU / IO 重任务 + 外发
docker compose up -d celery_worker celery_worker_network celery_beat
# 等价单 worker 兜底（本地低配）：-Q llm,document,connector,notification,billing
```

**Priority is intentionally NOT used.** Redis-broker priority applies only to messages that are not yet prefetched, and a single worker blocks on the head of its queue anyway. Equal isolation is achieved with separate queues + per-queue workers and concurrency (`document,notification,billing,connector` at 4; `llm,connector` at 2).

All beat tasks run under a **distributed lock** (`aibg:tasklock:{task}:{scope}:{window}`, token CAS release). A lost lock only logs and skips; Redis unavailability falls through to DB idempotency/state guards. Check a queue is being consumed with:

```powershell
celery -A app.core.celery_app.celery_app inspect active_queues
```

`connector_sync_task` is registered only when `CONNECTOR_SYNC_ENABLED=true` (mock mode by default; sync runs are ledger-backed with cursor/checkpoint breakpoint recovery).

## External-Call Resilience

Outbound calls (SMTP, Feishu, alert webhooks, webhook deliveries) go through `app/core/external_resilience.py`: retryable vs non-retryable classification, exponential backoff + full jitter, `Retry-After` respect (capped at `EXTERNAL_RETRY_AFTER_MAX_SECONDS`), and a per-service circuit breaker (`external:{service}|{connector}|{op}`). External **writes** (POST/PUT/PATCH/DELETE) that time out are classified `AMBIGUOUS_SIDE_EFFECT` and are **never blindly retried** — the caller confirms via idempotency ledger / `provider_message_id` instead. Tune with the `EXTERNAL_*` settings; only retryable failures count toward the breaker (rate-limits do not).

## Alert Webhook

Configure a WeCom or DingTalk robot-compatible Markdown webhook in the deployment environment:

```dotenv
ALERT_WEBHOOK_URL=https://example.invalid/robot/send?key=replace-me
ALERT_WEBHOOK_MIN_SEVERITY=high
ALERT_WEBHOOK_TIMEOUT_SECONDS=5
```

Leave `ALERT_WEBHOOK_URL` empty to keep alert delivery disabled. The worker checks every five minutes and sends only a redacted operational summary: severity, source, category, and internal target ID. It never sends legal document content, case details, passwords, error details, or agent inputs. Each alert is deduplicated for one hour.

The same task also checks model routing health. Once the configured minimum request sample is reached, excessive primary-model failures or failed fallback attempts create a `model_routing_degraded` alert. Check `GET /api/analytics/llm-routing/health` as an administrator before changing model credentials, routing rules, or retry thresholds.

## Error Reporting & Tracing (E-5)

Sentry captures frontend and API errors; OpenTelemetry (OTel) exports request traces to a collector. Both are opt-in: they skip initialization when unconfigured, so local/dev runs carry no extra dependency or overhead. Backend wiring lives in `app/core/telemetry.py`, called from `app/main.py`.

```dotenv
# backend
SENTRY_DSN=https://<key>@<org>.ingest.sentry.io/<project>
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317

# frontend (Vite reads this; same DSN value as SENTRY_DSN)
VITE_SENTRY_DSN=https://<key>@<org>.ingest.sentry.io/<project>
```

- With empty `SENTRY_DSN` and `OTEL_ENABLED=false` (the defaults), initialization is a no-op and the extra packages are never imported.
- Sentry uses the FastAPI and SQLAlchemy integrations with `traces_sample_rate=0.1`, tagging the environment from `ENVIRONMENT`. Unhandled exceptions already route through `unhandled_exception_handler`; the SDK hooks the same path.
- OTel instruments FastAPI requests and the SQLAlchemy engine. Point the OTLP/gRPC exporter at a collector (e.g. Jaeger, Tempo, Datadog Agent). Installing the `opentelemetry-*` lines in `requirements.txt` is required only when enabling it.
- The frontend initializes `@sentry/vue` in `main.js` only when `VITE_SENTRY_DSN` is set. Replay / screen recording is intentionally NOT enabled because legal document content and case data are sensitive. The local dev error panel remains as a fallback for unconfigured environments.
