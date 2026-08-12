"""文档处理与文件生命周期（P0）。

- documents 增加 object_key/mime_type/size（存储抽象，只存 object key 与内容元数据，
  不再依赖本地绝对路径）、current_stage/failure_stage/error_code/error_message/retry_count
  （状态机）、parser_version/ocr_version/chunker_version/index_version/embedding_model/
  last_processed_at（可重建性）。
- 新表 document_parse_artifacts：按 (document_id, version_number) 唯一，记录每版本
  解析/切分/索引产物指纹，作为版本守卫与旧产物失效判断依据。
- document_parse_jobs 增加 lease_owner/lease_expires_at（租约：worker 崩溃/超时可回收）。
- document_chunks 增加唯一约束 (document_id, chunk_index)，保证切分任务幂等不重复。

使用 batch_alter_table 跨方言（MySQL 原生 ALTER；SQLite copy-and-move 亦可用）。
历史 file_path 列改为可空：新文档只写 object_key。

Revision ID: 20260812_0072
Revises: 20260810_0071
Create Date: 2026-08-12
"""
from alembic import op
import sqlalchemy as sa

revision = "20260812_0072"
down_revision = "20260810_0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── documents：存储抽象 + 状态机 + 可重建性 ──────────────────────────────
    with op.batch_alter_table("documents") as batch:
        batch.alter_column("file_path", existing_type=sa.String(512), nullable=True)
        batch.add_column(sa.Column("object_key", sa.String(512), nullable=True))
        batch.add_column(sa.Column("mime_type", sa.String(128), nullable=True))
        batch.add_column(sa.Column("size", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("current_stage", sa.String(64), server_default="uploaded", nullable=False)
        )
        batch.add_column(sa.Column("failure_stage", sa.String(64), nullable=True))
        batch.add_column(sa.Column("error_code", sa.String(64), nullable=True))
        batch.add_column(sa.Column("error_message", sa.String(512), nullable=True))
        batch.add_column(
            sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False)
        )
        batch.add_column(sa.Column("parser_version", sa.String(64), nullable=True))
        batch.add_column(sa.Column("ocr_version", sa.String(64), nullable=True))
        batch.add_column(sa.Column("chunker_version", sa.String(64), nullable=True))
        batch.add_column(sa.Column("index_version", sa.String(64), nullable=True))
        batch.add_column(sa.Column("embedding_model", sa.String(128), nullable=True))
        batch.add_column(sa.Column("last_processed_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_documents_object_key", ["object_key"])

    # 存量行回填：pending/processing 映射到等价状态，避免新状态机读到历史残留。
    op.execute(
        sa.text(
            "UPDATE documents SET status = CASE "
            "WHEN status = 'pending' THEN 'uploaded' "
            "WHEN status = 'processing' THEN 'parsing' "
            "ELSE status END "
            "WHERE status IN ('pending', 'processing')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE documents SET current_stage = status "
            "WHERE current_stage = 'uploaded'"
        )
    )

    # ── document_parse_jobs：租约列 ───────────────────────────────────────────
    with op.batch_alter_table("document_parse_jobs") as batch:
        batch.add_column(sa.Column("lease_owner", sa.String(128), nullable=True))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))

    # ── 新表 document_parse_artifacts ──────────────────────────────────────────
    op.create_table(
        "document_parse_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(128), nullable=True),
        sa.Column("object_key", sa.String(512), nullable=True),
        sa.Column("parser_version", sa.String(64), nullable=True),
        sa.Column("ocr_version", sa.String(64), nullable=True),
        sa.Column("chunker_version", sa.String(64), nullable=True),
        sa.Column("index_version", sa.String(64), nullable=True),
        sa.Column("embedding_model", sa.String(128), nullable=True),
        sa.Column("artifact_object_key", sa.String(512), nullable=True),
        sa.Column("artifact_hash", sa.String(64), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=True),
        sa.Column("indexed_chunks_hash", sa.String(64), nullable=True),
        sa.Column("processing_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_summary", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_parse_artifacts_doc_version"),
    )
    op.create_index(
        "ix_document_parse_artifacts_document_id",
        "document_parse_artifacts",
        ["document_id"],
    )

    # ── document_chunks：切分幂等唯一约束（先清理重复行，仅保留每 (doc, index) 的最小 id） ──
    op.execute(
        sa.text(
            "DELETE FROM document_chunks WHERE id NOT IN ("
            "  SELECT keep_id FROM ("
            "    SELECT MIN(c2.id) AS keep_id FROM document_chunks c2 "
            "    GROUP BY c2.document_id, c2.chunk_index"
            "  ) AS keep"
            ")"
        )
    )
    with op.batch_alter_table("document_chunks") as batch:
        batch.create_unique_constraint(
            "uq_document_chunks_document_index", ["document_id", "chunk_index"]
        )


def downgrade() -> None:
    with op.batch_alter_table("document_chunks") as batch:
        batch.drop_constraint("uq_document_chunks_document_index", type_="unique")
    op.drop_index("ix_document_parse_artifacts_document_id", table_name="document_parse_artifacts")
    op.drop_table("document_parse_artifacts")
    with op.batch_alter_table("document_parse_jobs") as batch:
        batch.drop_column("lease_expires_at")
        batch.drop_column("lease_owner")
    with op.batch_alter_table("documents") as batch:
        batch.drop_index("ix_documents_object_key")
        batch.drop_column("last_processed_at")
        batch.drop_column("embedding_model")
        batch.drop_column("index_version")
        batch.drop_column("chunker_version")
        batch.drop_column("ocr_version")
        batch.drop_column("parser_version")
        batch.drop_column("retry_count")
        batch.drop_column("error_message")
        batch.drop_column("error_code")
        batch.drop_column("failure_stage")
        batch.drop_column("current_stage")
        batch.drop_column("size")
        batch.drop_column("mime_type")
        batch.drop_column("object_key")
        batch.alter_column("file_path", existing_type=sa.String(512), nullable=False)
