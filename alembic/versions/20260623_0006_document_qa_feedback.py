"""add document qa feedback fields

Revision ID: 20260623_0006
Revises: 20260621_0005
Create Date: 2026-06-23 20:40:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260623_0006"
down_revision: Union[str, None] = "20260621_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("document_qa_records")}

    if "feedback_value" not in columns:
        op.add_column("document_qa_records", sa.Column("feedback_value", sa.String(length=16), nullable=True))
    if "feedback_reason" not in columns:
        op.add_column("document_qa_records", sa.Column("feedback_reason", sa.String(length=64), nullable=True))
    if "feedback_note" not in columns:
        op.add_column("document_qa_records", sa.Column("feedback_note", sa.Text(), nullable=True))
    if "feedback_status" not in columns:
        op.add_column("document_qa_records", sa.Column("feedback_status", sa.String(length=16), nullable=True))
    if "feedback_created_at" not in columns:
        op.add_column("document_qa_records", sa.Column("feedback_created_at", sa.DateTime(timezone=True), nullable=True))
    if "feedback_resolved_at" not in columns:
        op.add_column("document_qa_records", sa.Column("feedback_resolved_at", sa.DateTime(timezone=True), nullable=True))
    if "feedback_resolution_note" not in columns:
        op.add_column("document_qa_records", sa.Column("feedback_resolution_note", sa.Text(), nullable=True))
    if "feedback_resolved_by" not in columns:
        op.add_column("document_qa_records", sa.Column("feedback_resolved_by", sa.Integer(), nullable=True))

    indexes = {item["name"] for item in inspector.get_indexes("document_qa_records")}
    feedback_value_index = op.f("ix_document_qa_records_feedback_value")
    if feedback_value_index not in indexes:
        op.create_index(feedback_value_index, "document_qa_records", ["feedback_value"], unique=False)
    feedback_status_index = op.f("ix_document_qa_records_feedback_status")
    if feedback_status_index not in indexes:
        op.create_index(feedback_status_index, "document_qa_records", ["feedback_status"], unique=False)
    feedback_created_at_index = op.f("ix_document_qa_records_feedback_created_at")
    if feedback_created_at_index not in indexes:
        op.create_index(feedback_created_at_index, "document_qa_records", ["feedback_created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_document_qa_records_feedback_created_at"), table_name="document_qa_records")
    op.drop_index(op.f("ix_document_qa_records_feedback_status"), table_name="document_qa_records")
    op.drop_index(op.f("ix_document_qa_records_feedback_value"), table_name="document_qa_records")
    op.drop_column("document_qa_records", "feedback_resolved_by")
    op.drop_column("document_qa_records", "feedback_resolution_note")
    op.drop_column("document_qa_records", "feedback_resolved_at")
    op.drop_column("document_qa_records", "feedback_created_at")
    op.drop_column("document_qa_records", "feedback_status")
    op.drop_column("document_qa_records", "feedback_note")
    op.drop_column("document_qa_records", "feedback_reason")
    op.drop_column("document_qa_records", "feedback_value")
