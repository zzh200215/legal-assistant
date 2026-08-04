from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings

BASELINE_REVISION = "20260620_0001"
ALEMBIC_INI_PATH = "alembic.ini"


def _ensure_mysql_database_exists(database_url: str) -> None:
    url = make_url(database_url)
    database_name = url.database
    if not database_name or not url.drivername.startswith("mysql"):
        return

    server_engine = create_engine(url.set(database=""))
    try:
        quoted_name = database_name.replace("`", "``")
        with server_engine.begin() as connection:
            connection.execute(
                text(f"CREATE DATABASE IF NOT EXISTS `{quoted_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            )
    finally:
        server_engine.dispose()


def run_migrations() -> None:
    settings = get_settings()
    _ensure_mysql_database_exists(settings.DATABASE_URL)

    config = Config(ALEMBIC_INI_PATH)
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

    engine_kwargs = {}
    if not settings.DATABASE_URL.startswith("sqlite"):
        engine_kwargs["pool_pre_ping"] = True
    else:
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(settings.DATABASE_URL, **engine_kwargs)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
    finally:
        engine.dispose()

    has_version_table = "alembic_version" in table_names
    business_tables = table_names - {"alembic_version"}

    if business_tables and not has_version_table:
        command.stamp(config, BASELINE_REVISION)

    command.upgrade(config, "head")
