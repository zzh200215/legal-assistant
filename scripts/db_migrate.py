"""数据库迁移入口：转发到 alembic upgrade head（由原 init_db.py 与 migrate_db.py 合并）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.migration import run_migrations


def main() -> None:
    run_migrations()
    print("Database migrated to latest revision.")


if __name__ == "__main__":
    main()
