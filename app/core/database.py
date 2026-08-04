from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import get_settings

settings = get_settings()


def _ensure_sqlite_dir(database_url: str) -> None:
    """sqlite 本地文件需保证父目录存在（data/ 在新环境可能尚未创建）"""
    if database_url.startswith("sqlite:///"):
        db_path = database_url.removeprefix("sqlite:///")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_dir(settings.DATABASE_URL)


def get_engine_kwargs(database_url: str) -> dict:
    engine_kwargs = {
        "echo": bool(settings.DATABASE_ECHO),
    }
    if settings.DATABASE_POOL_PRE_PING and not database_url.startswith("sqlite"):
        engine_kwargs["pool_pre_ping"] = True

    if database_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # E-7：默认池 5+10 在 LLM 长调用并发下耗尽连接，显式放宽并由环境变量可调。
        engine_kwargs["pool_size"] = settings.DATABASE_POOL_SIZE
        engine_kwargs["max_overflow"] = settings.DATABASE_POOL_MAX_OVERFLOW
    return engine_kwargs


engine = create_engine(settings.DATABASE_URL, **get_engine_kwargs(settings.DATABASE_URL))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
