"""Matter activity and product-facing workflow tracking.

Revision ID: 20261003_0089
Revises: 20261219_0088
"""

import sqlalchemy as sa

from alembic import op

revision = "20261003_0089"
down_revision = "20261219_0088"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return _inspector().has_table(name)


def _has_index(table: str, name: str) -> bool:
    return _has_table(table) and any(i.get("name") == name for i in _inspector().get_indexes(table))


def upgrade() -> None:
    if not _has_table("matter_activities"):
        op.create_table(
            "matter_activities",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("case_id", sa.Integer(), sa.ForeignKey("legal_cases.id", ondelete="CASCADE"), nullable=False),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("target_type", sa.String(length=32), nullable=True),
            sa.Column("target_id", sa.Integer(), nullable=True),
            sa.Column("title", sa.String(length=256), nullable=False),
            sa.Column("summary", sa.String(length=1000), nullable=True),
            sa.Column("visibility", sa.String(length=16), nullable=False, server_default="internal"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    for name, column in (
        ("ix_matter_activities_organization_id", "organization_id"),
        ("ix_matter_activities_case_id", "case_id"),
        ("ix_matter_activities_actor_id", "actor_id"),
        ("ix_matter_activities_event_type", "event_type"),
        ("ix_matter_activities_visibility", "visibility"),
        ("ix_matter_activities_created_at", "created_at"),
    ):
        if not _has_index("matter_activities", name):
            op.create_index(name, "matter_activities", [column])

    if not _has_table("workflow_runs"):
        op.create_table(
            "workflow_runs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("task_id", sa.String(length=128), nullable=True),
            sa.Column("workflow_type", sa.String(length=64), nullable=False),
            sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("case_id", sa.Integer(), sa.ForeignKey("legal_cases.id"), nullable=True),
            sa.Column("business_key", sa.String(length=256), nullable=True),
            sa.Column("idempotency_key", sa.String(length=128), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
            sa.Column("current_step", sa.String(length=64), nullable=True),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.String(length=1000), nullable=True),
            sa.Column("checkpoint_json", sa.Text(), nullable=True),
            sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancel_reason", sa.String(length=256), nullable=True),
            sa.Column("cancel_requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("request_id", sa.String(length=64), nullable=True),
            sa.Column("queued_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("task_id", name="uq_workflow_runs_task_id"),
        )
    else:
        inspector = _inspector()
        existing = {item["name"] for item in inspector.get_columns("workflow_runs")}
        for column in (
            sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancel_reason", sa.String(length=256), nullable=True),
            sa.Column("cancel_requested_by", sa.Integer(), nullable=True),
        ):
            if column.name not in existing:
                op.add_column("workflow_runs", column)
    for name, column in (
        ("task_id", "task_id"), ("workflow_type", "workflow_type"),
        ("organization_id", "organization_id"), ("user_id", "user_id"),
        ("case_id", "case_id"), ("business_key", "business_key"),
        ("idempotency_key", "idempotency_key"), ("status", "status"),
        ("trace_id", "trace_id"), ("request_id", "request_id"),
    ):
        index = f"ix_workflow_runs_{name}"
        if not _has_index("workflow_runs", index):
            op.create_index(index, "workflow_runs", [column])

    if not _has_table("workflow_events"):
        op.create_table(
            "workflow_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("workflow_run_id", sa.Integer(), sa.ForeignKey("workflow_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("from_status", sa.String(length=32), nullable=True),
            sa.Column("to_status", sa.String(length=32), nullable=True),
            sa.Column("step", sa.String(length=64), nullable=True),
            sa.Column("progress", sa.Integer(), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column("actor_type", sa.String(length=24), nullable=False, server_default="system"),
            sa.Column("actor_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    for name, column in (("workflow_run_id", "workflow_run_id"), ("event_type", "event_type")):
        index = f"ix_workflow_events_{name}"
        if not _has_index("workflow_events", index):
            op.create_index(index, "workflow_events", [column])

    if not _has_table("workflow_outbox_events"):
        op.create_table(
            "workflow_outbox_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("workflow_event_id", sa.Integer(), sa.ForeignKey("workflow_events.id", ondelete="CASCADE"), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("claim_owner", sa.String(length=64), nullable=True),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.UniqueConstraint("workflow_event_id", name="uq_workflow_outbox_event_id"),
        )
    for name, column in (("event_type", "event_type"), ("status", "status"), ("available_at", "available_at")):
        index = f"ix_workflow_outbox_events_{name}"
        if not _has_index("workflow_outbox_events", index):
            op.create_index(index, "workflow_outbox_events", [column])


def downgrade() -> None:
    op.drop_table("workflow_outbox_events")
    op.drop_table("workflow_events")
    op.drop_table("workflow_runs")
    op.drop_table("matter_activities")
