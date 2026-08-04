"""add business workflow automation tables

Revision ID: 20260718_0030
Revises: 20260718_0029
"""
from alembic import op
import sqlalchemy as sa

revision = "20260718_0030"
down_revision = "20260718_0029"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("business_workflow_definitions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id")), sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id")), sa.Column("name", sa.String(128), nullable=False), sa.Column("code", sa.String(64), nullable=False), sa.Column("description", sa.Text()), sa.Column("version", sa.Integer(), nullable=False, server_default="1"), sa.Column("status", sa.String(32), nullable=False, server_default="active"), sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("form_schema_json", sa.Text(), nullable=False), sa.Column("steps_json", sa.Text(), nullable=False), sa.Column("config_json", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index("ix_business_workflow_definitions_code", "business_workflow_definitions", ["code"])
    op.create_index("ix_business_workflow_definitions_org", "business_workflow_definitions", ["organization_id"])
    op.create_table("workflow_budget_rules", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id")), sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id")), sa.Column("category", sa.String(64), nullable=False), sa.Column("available_amount", sa.Numeric(14, 2), nullable=False, server_default="0"), sa.Column("reserved_amount", sa.Numeric(14, 2), nullable=False, server_default="0"), sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"), sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("users.id")), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")), sa.UniqueConstraint("organization_id", "department_id", "category", name="uq_workflow_budget_scope_category"))
    op.create_table("business_workflow_instances", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("definition_id", sa.Integer(), sa.ForeignKey("business_workflow_definitions.id"), nullable=False), sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False), sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id")), sa.Column("department_id", sa.Integer(), sa.ForeignKey("departments.id")), sa.Column("title", sa.String(256), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="draft"), sa.Column("form_data_json", sa.Text(), nullable=False), sa.Column("budget_snapshot_json", sa.Text()), sa.Column("approval_chain_json", sa.Text()), sa.Column("approval_cursor", sa.Integer(), nullable=False, server_default="0"), sa.Column("oa_connector_id", sa.Integer(), sa.ForeignKey("external_connectors.id")), sa.Column("oa_reference", sa.String(256)), sa.Column("error_message", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index("ix_business_workflow_instances_status", "business_workflow_instances", ["status"])
    op.create_index("ix_business_workflow_instances_user", "business_workflow_instances", ["user_id"])
    op.create_table("business_workflow_step_executions", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("instance_id", sa.Integer(), sa.ForeignKey("business_workflow_instances.id"), nullable=False), sa.Column("step_key", sa.String(64), nullable=False), sa.Column("step_type", sa.String(32), nullable=False), sa.Column("system_name", sa.String(64)), sa.Column("status", sa.String(32), nullable=False, server_default="pending"), sa.Column("input_json", sa.Text()), sa.Column("output_json", sa.Text()), sa.Column("compensation_json", sa.Text()), sa.Column("error_message", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")))
    op.create_index("ix_business_workflow_step_executions_instance", "business_workflow_step_executions", ["instance_id"])


def downgrade():
    op.drop_table("business_workflow_step_executions")
    op.drop_table("business_workflow_instances")
    op.drop_table("workflow_budget_rules")
    op.drop_table("business_workflow_definitions")
