# Database Migrations

This project now uses Alembic for schema migrations.

Common commands:

```bash
python scripts/db_migrate.py
alembic upgrade head
alembic revision -m "describe change"
```

Notes:

- `app/main.py` no longer auto-creates tables on startup.
- `scripts/db_migrate.py`（由原 `init_db.py`/`migrate_db.py` 合并）转发到 `alembic upgrade head`。
- `alembic/env.py` reads `DATABASE_URL` from `.env` via `app.core.config`.
