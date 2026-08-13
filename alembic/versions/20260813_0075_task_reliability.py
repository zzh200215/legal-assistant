"""P0/P1 任务与连接器可靠性：队列台账 / 同步游标 / 任务运行记录。

- connector_sync_jobs 补齐同步可靠性列：organization_id / cursor_json / checkpoint_json /
  source_version / counts / error_code / attempt / next_retry_at / idempotency_key /
  lease_owner / lease_expires_at（断点恢复 + 崩溃回收）。
- 新表 task_runs：每个关键异步任务的失败/重试上下文（错误码/attempt/checkpoint/next_retry_at）。
- 新表 connector_sync_items：外部唯一 ID + version/hash 的 DB 级增量去重。
- 幂等回填：既有 connector_sync_jobs 行补 legacy idempotency_key 与默认计数（不臆造数据）。

Revision ID: 20260813_0075
Revises: 20260812_0074
Create Date: 2026-08-13
"""
from alembic import op
import sqlalchemy as sa

revision = "20260813_0075"
down_revision = "20260812_0074"
branch_labels = None
depends_on = None


# ── connector_sync_jobs 扩展列 ────────────────────────────────────────────────

def _extend_connector_sync_jobs() -> None:
    with op.batch_alter_table("connector_sync_jobs") as batch_op:
        batch_op.add_column(sa.Column("organization_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("cursor_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("checkpoint_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("source_version", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("processed", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("succeeded", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("failed", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("error_code", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("idempotency_key", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("lease_owner", sa.String(128), nullable=True))
        batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_connector_sync_jobs_user_id_status", ["user_id", "status"])
        batch_op.create_index("ix_connector_sync_jobs_idempotency_key", ["idempotency_key"])
        batch_op.create_index("ix_connector_sync_jobs_lease_expires_at", ["lease_expires_at"])


# ── task_runs ─────────────────────────────────────────────────────────────────

def _create_task_runs() -> None:
    op.create_table(
        "task_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("task_name", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False, server_default="task"),
        sa.Column("queue", sa.String(32), nullable=True),
        sa.Column("business_key", sa.String(256), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=True),
        sa.Column("checkpoint_json", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index("ix_task_runs_task_id", "task_runs", ["task_id"])
    op.create_index("ix_task_runs_task_name", "task_runs", ["task_name"])
    op.create_index("ix_task_runs_business_key", "task_runs", ["business_key"])
    op.create_index("ix_task_runs_idempotency_key", "task_runs", ["idempotency_key"])
    op.create_index("ix_task_runs_tenant_id", "task_runs", ["tenant_id"])
    op.create_index("ix_task_runs_status", "task_runs", ["status"])
    op.create_index("ix_task_runs_next_retry_at", "task_runs", ["next_retry_at"])
    op.create_index("ix_task_runs_created_at", "task_runs", ["created_at"])
    op.create_index(
        "ix_task_runs_name_key_created", "task_runs", ["task_name", "idempotency_key", "created_at"],
    )
    op.create_index("ix_task_runs_status_tenant", "task_runs", ["status", "tenant_id"])


# ── connector_sync_items ──────────────────────────────────────────────────────

def _create_connector_sync_items() -> None:
    op.create_table(
        "connector_sync_items",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("connector_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("version_hash", sa.String(128), nullable=False),
        sa.Column("deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sync_run_id", sa.Integer(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["connector_id"], ["external_connectors.id"]),
        sa.UniqueConstraint("connector_id", "external_id", name="uq_connector_sync_items_connector_external"),
    )
    op.create_index("ix_connector_sync_items_connector_id", "connector_sync_items", ["connector_id"])
    op.create_index("ix_connector_sync_items_sync_run_id", "connector_sync_items", ["sync_run_id"])
    op.create_index(
        "ix_connector_sync_items_connector_ts", "connector_sync_items", ["connector_id", "last_synced_at"],
    )


# ── 幂等回填 ─────────────────────────────────────────────────────────────────

def _backfill(bind) -> None:
    """既有 connector_sync_jobs 行补齐 legacy 幂等键与默认计数（幂等）。

    只补 idempotency_key 为空的旧行；不臆造 cursor/checkpoint/source_version。
    ``bind`` 可能是 engine（测试直调）或 Connection（``op.get_bind()`` 迁移期）。

    注意：传入 Connection 时**不 commit**——那是 alembic 正在管理的连接，
    提前 commit 会让随后的 version stamp 落在无人跟踪的新事务上、随连接关闭回滚。
    """
    from sqlalchemy import Connection, text

    def _run(conn) -> None:
        # func.concat 跨方言：MySQL 编译为 CONCAT，SQLite 编译为 ||（VARCHAR 仅 SQL Server 有，勿用）
        sync_jobs = sa.table(
            "connector_sync_jobs",
            sa.column("id", sa.Integer),
            sa.column("idempotency_key", sa.String(128)),
        )
        conn.execute(
            sa.update(sync_jobs)
            .where(sync_jobs.c.idempotency_key.is_(None))
            .values(idempotency_key=sa.func.concat("legacy:", sync_jobs.c.id))
        )
        conn.execute(text(
            "UPDATE connector_sync_jobs SET processed = 0, succeeded = 0, failed = 0, attempt = 0"
            " WHERE processed IS NULL OR succeeded IS NULL OR failed IS NULL OR attempt IS NULL"
        ))

    if isinstance(bind, Connection):
        # alembic 迁移期：仅执行，事务交给迁移框架统一提交/回滚
        _run(bind)
    else:
        # 测试直调（engine）：自开连接并提交
        with bind.connect() as conn:
            _run(conn)
            conn.commit()


def upgrade() -> None:
    _extend_connector_sync_jobs()
    _create_task_runs()
    _create_connector_sync_items()
    _backfill(op.get_bind())


def downgrade() -> None:
    op.drop_table("connector_sync_items")
    op.drop_table("task_runs")
    with op.batch_alter_table("connector_sync_jobs") as batch_op:
        batch_op.drop_index("ix_connector_sync_jobs_lease_expires_at")
        batch_op.drop_index("ix_connector_sync_jobs_idempotency_key")
        batch_op.drop_index("ix_connector_sync_jobs_user_id_status")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("lease_owner")
        batch_op.drop_column("idempotency_key")
        batch_op.drop_column("next_retry_at")
        batch_op.drop_column("attempt")
        batch_op.drop_column("error_code")
        batch_op.drop_column("completed_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("failed")
        batch_op.drop_column("succeeded")
        batch_op.drop_column("processed")
        batch_op.drop_column("source_version")
        batch_op.drop_column("checkpoint_json")
        batch_op.drop_column("cursor_json")
        batch_op.drop_column("organization_id")
