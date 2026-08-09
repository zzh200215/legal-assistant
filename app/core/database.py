from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import get_settings
from app.core.db_monitor import install_db_monitor

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
        # E-7：默认池 20+40 在 LLM 长调用并发下仍可能耗尽，连接池参数全部由环境变量可调。
        # pool_recycle 在 MySQL wait_timeout（默认 8h）前回收，避免 stale 连接；
        # pool_timeout 控制 checkout 等待上限，超时抛异常而非无限阻塞。
        engine_kwargs["pool_size"] = settings.DATABASE_POOL_SIZE
        engine_kwargs["max_overflow"] = settings.DATABASE_POOL_MAX_OVERFLOW
        engine_kwargs["pool_recycle"] = settings.DATABASE_POOL_RECYCLE
        engine_kwargs["pool_timeout"] = settings.DATABASE_POOL_TIMEOUT
    return engine_kwargs


engine = create_engine(settings.DATABASE_URL, **get_engine_kwargs(settings.DATABASE_URL))
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


@contextmanager
def session_scope():
    """统一事务上下文：业务操作成功自动 commit，异常自动 rollback，退出时关闭 session。

    新的 service / 后台任务应优先使用本上下文，事务边界清晰、只提交一次。
    既有 service 的内联 commit 保持不动，按风险逐步迁移。
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        # 端点异常时显式回滚（等价于 close() 的隐式回滚，但更明确；
        # 已 commit 的事务不受影响），避免把未提交变更和锁带到下一请求。
        db.rollback()
        raise
    finally:
        db.close()


# 数据库监控：慢 SQL / 事务计数 / 连接池指标（DATABASE_MONITOR_ENABLED 开启时挂载）。
# 放在引擎创建后立即挂载；对 SQLite/测试用独立引擎不生效（各测试自行安装）。
if settings.DATABASE_MONITOR_ENABLED:
    install_db_monitor(engine)
