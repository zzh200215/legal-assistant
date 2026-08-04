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

## Alert Webhook

Configure a WeCom or DingTalk robot-compatible Markdown webhook in the deployment environment:

```dotenv
ALERT_WEBHOOK_URL=https://example.invalid/robot/send?key=replace-me
ALERT_WEBHOOK_MIN_SEVERITY=high
ALERT_WEBHOOK_TIMEOUT_SECONDS=5
```

Leave `ALERT_WEBHOOK_URL` empty to keep alert delivery disabled. The worker checks every five minutes and sends only a redacted operational summary: severity, source, category, and internal target ID. It never sends legal document content, case details, passwords, error details, or agent inputs. Each alert is deduplicated for one hour.

The same task also checks model routing health. Once the configured minimum request sample is reached, excessive primary-model failures or failed fallback attempts create a `model_routing_degraded` alert. Check `GET /api/analytics/llm-routing/health` as an administrator before changing model credentials, routing rules, or retry thresholds.

## Legal Document Data Governance

`LEGALDOC_RETENTION_DAYS` defaults to `90`. Beat runs the retention task daily for active legal data connectors. It deletes only local legal document copies older than the threshold, retains documents linked to a review or audit record, and never changes the source legal database.

Users can preview and manually run the same retention policy from the Legal Workbench. Each purge is written to the operation log.

## Connector Revocation

Owners can rotate credentials or disable a legal data connector from Legal Workbench or Review Control. Disabling immediately clears the encrypted credentials and sync cursor, changes the connector status to `disabled`, and prevents later data sync or external delivery through that connector. Reconnect with a new connector and fresh authorization to resume use.

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
