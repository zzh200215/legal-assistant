"""文档处理流水线：parse / chunk / index 三个可独立重试、幂等、版本守卫的阶段。

设计：
- 每个阶段是独立函数（run_parse / run_chunk / run_index），既被 Celery 任务单独调用，
  也可被同步上传快路径按序调用（注入对应的纯函数引用，保持对既有测试的兼容）。
- 幂等：以 (document_id, version_number, task_type, input_hash) 为幂等键复用
  idempotency_service（DB 唯一约束 + replay 快照）；产物按版本存档在
  document_parse_artifacts，依据 content_hash / parser_version / ocr_version /
  chunker_version / index_version 判断可复用。
- 版本守卫：阶段启动校验 document.version_number 与入参一致，老版本任务直接中止，
  不得覆盖新版本文档结果。
- 索引幂等：索引前检查 indexed_chunks_hash（当前 chunks 指纹）一致则跳过重复写入；
  向量库本身按确定性 embedding_id upsert，不产生重复向量。
- 并发：阶段开始时先按幂等键抢占（in_progress 冲突即跳过），随后用
  Document.version 乐观锁 CAS 写状态，避免两个 worker 同时处理或状态回退。
"""

from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.error_codes import IDEMPOTENCY_KEY_IN_PROGRESS
from app.models.document import Document, DocumentChunk, DocumentParseArtifact
from app.services.document_parsing import (
    DocumentParsePermanentError,
    _extract_segments,
    _prepare_chunks_for_indexing,
    _split_text,
)
from app.services.document_security import (
    DocumentSecurityError,
    _ZIP_BASED_EXTS,
    inspect_zip_safety,
)
from app.services.document_state import (
    DOCUMENT_STATUS_FAILED,
    DOCUMENT_STATUS_INDEXED,
    DOCUMENT_STATUS_INDEXING,
    DOCUMENT_STATUS_PARSED,
    DOCUMENT_STATUS_PARSING,
    DOCUMENT_STATUS_RETRYING,
    DocumentStateTransitionError,
    transition_document,
    update_stage,
)
from app.services.idempotency_service import IdempotencyConflictError, idempotency_service
from app.services.storage_service import storage_service

settings = get_settings()

# 解析器/索引器版本（与配置联动：切分器版本 = RAG_CHUNK_SIZE-OVERLAP）
PARSER_VERSION = "1"
INDEX_VERSION = "1"
_IDEMPOTENCY_SCOPE = "document_pipeline"

_OCR_VERSION_CACHE: dict[str, str] = {}


def ocr_version() -> str:
    """OCR 版本：未使用 OCR（pytesseract 缺失）返回空字符串，否则取 tesseract 版本。"""
    if "v" in _OCR_VERSION_CACHE:
        return _OCR_VERSION_CACHE["v"]
    value = ""
    try:
        import pytesseract  # type: ignore

        try:
            value = str(pytesseract.get_tesseract_version() or "") or "tesseract-unknown"
        except Exception:  # noqa: BLE001
            value = "tesseract-unknown"
    except Exception:  # noqa: BLE001
        value = ""
    _OCR_VERSION_CACHE["v"] = value
    return value


def chunker_version() -> str:
    return f"rcs{settings.RAG_CHUNK_SIZE}-o{settings.RAG_CHUNK_OVERLAP}"


def build_stage_key(document: Document, task_type: str, input_hash: str) -> str:
    return f"doc:{document.id}:v{document.version_number}:{task_type}:{(input_hash or '')[:16]}"


def parse_input_hash(content_hash: str) -> str:
    """parse 阶段幂等输入指纹：内容 + 解析器/OCR 版本（任一变化 → 触发重解析）。"""
    return hashlib.sha256(
        f"{content_hash}:{PARSER_VERSION}:{ocr_version()}".encode("utf-8")
    ).hexdigest()


def chunk_input_hash(artifact_hash: str) -> str:
    """chunk 阶段幂等输入指纹：解析产物 + 切分器版本（配置变化 → 重新切分）。"""
    return hashlib.sha256(f"{artifact_hash}:{chunker_version()}".encode("utf-8")).hexdigest()


def index_input_hash(chunks_hash: str) -> str:
    """index 阶段幂等输入指纹：切分指纹 + 索引器版本 + 嵌入模型（任一变化 → 重新索引）。"""
    return hashlib.sha256(
        f"{chunks_hash}:{INDEX_VERSION}:{settings.EMBEDDING_MODEL}".encode("utf-8")
    ).hexdigest()


def version_matches(document: Document, expected_version: int | None) -> bool:
    if expected_version is None:
        return True
    return document.version_number == expected_version


def _begin_stage(db: Session, document: Document, task_type: str, input_hash: str) -> dict:
    key = build_stage_key(document, task_type, input_hash)
    try:
        result = idempotency_service.begin(db, scope=_IDEMPOTENCY_SCOPE, key=key, request_hash=input_hash or "")
    except IdempotencyConflictError as exc:
        if exc.code == IDEMPOTENCY_KEY_IN_PROGRESS:
            return {"replay": False, "skip": True, "key": key}
        # 同 key 不同请求载荷：应不可能（input_hash 即请求载荷指纹），防御性视为跳过。
        return {"replay": False, "skip": True, "key": key}
    return {"replay": bool(result["replay"]), "skip": False, "snapshot": result.get("response_snapshot"), "key": key}


def _complete_stage(db: Session, key: str, snapshot: Any) -> None:
    try:
        idempotency_service.complete(db, scope=_IDEMPOTENCY_SCOPE, key=key, response_snapshot=snapshot)
    except Exception:  # noqa: BLE001 - 幂等完成失败不阻断业务
        db.rollback()


def _fail_stage(db: Session, key: str) -> None:
    try:
        idempotency_service.fail(db, scope=_IDEMPOTENCY_SCOPE, key=key)
    except Exception:  # noqa: BLE001
        db.rollback()


def get_or_create_artifact(db: Session, document: Document) -> DocumentParseArtifact:
    artifact = (
        db.query(DocumentParseArtifact)
        .filter(
            DocumentParseArtifact.document_id == document.id,
            DocumentParseArtifact.version_number == document.version_number,
        )
        .first()
    )
    if artifact is None:
        artifact = DocumentParseArtifact(
            document_id=document.id,
            version_number=document.version_number,
            content_hash=document.content_hash,
            object_key=document.object_key,
        )
        db.add(artifact)
        db.flush()
    return artifact


def resolve_local_path(document: Document) -> Path:
    if document.object_key:
        return storage_service.materialize_to_local(document.object_key)
    if document.file_path:
        return Path(document.file_path)
    raise ValueError("文档存储不可用：缺少 object_key 与 file_path")


def _artifact_object_key(document: Document, artifact_hash: str) -> str:
    return (
        f"users/{document.user_id}/docs/{document.id}/v{document.version_number}"
        f"/parse-{artifact_hash[:16]}.json"
    )


def _read_artifact_segments(artifact: DocumentParseArtifact) -> list[dict]:
    if not artifact.artifact_object_key:
        raise ValueError("解析产物缺失，请先执行 parse 阶段")
    with storage_service.get_stream(artifact.artifact_object_key) as stream:
        parts = []
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            parts.append(chunk)
    return json.loads(b"".join(parts).decode("utf-8"))


def _chunks_hash_from_dicts(chunk_dicts: list[dict]) -> str:
    hasher = hashlib.sha256()
    for chunk in chunk_dicts:
        content_hash = hashlib.sha256((chunk.get("content") or "").encode("utf-8")).hexdigest()
        hasher.update(f"{chunk.get('chunk_index')}:{chunk.get('embedding_id')}:{content_hash}\n".encode("utf-8"))
    return hasher.hexdigest()


def _mark_failed(db: Session, document: Document, *, stage: str, error_code: str, error_message: str) -> None:
    try:
        transition_document(
            document,
            DOCUMENT_STATUS_FAILED,
            failure_stage=stage,
            error_code=error_code,
            error_message=error_message,
        )
        db.commit()
    except DocumentStateTransitionError:
        db.rollback()
        # 已在 failed 或不可迁移状态：仅补记失败信息（状态不变）。
        document.failure_stage = stage
        document.error_code = error_code
        document.error_message = (error_message or "")[:1000]
        document.current_stage = DOCUMENT_STATUS_FAILED
        try:
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()


# ── 阶段 1：parse（文本提取 → 产物存档） ─────────────────────────────────────
def run_parse(
    db: Session,
    document_id: int,
    *,
    expected_version: int | None = None,
    user_id: int | None = None,
    extract_segments: Callable[..., list[dict]] | None = None,
    return_segments: bool = False,
    lease_refresh: Callable[[], None] | None = None,
) -> dict:
    extract_segments = extract_segments or _extract_segments
    document = db.query(Document).filter(Document.id == document_id).first()
    if document is None:
        return {"status": "skipped", "reason": "document_not_found"}
    if not version_matches(document, expected_version):
        return {"status": "skipped", "reason": "version_changed"}

    content_hash = document.content_hash or ""
    begin = _begin_stage(db, document, "parse", parse_input_hash(content_hash))
    if begin["replay"]:
        return {"status": "replayed", "document_id": document.id, "snapshot": begin["snapshot"]}
    if begin["skip"]:
        return {"status": "skipped", "reason": "already_running"}

    document = db.query(Document).filter(Document.id == document_id).first()
    try:
        transition_document(document, DOCUMENT_STATUS_PARSING, stage=DOCUMENT_STATUS_PARSING)
        db.commit()
    except (StaleDataError, DocumentStateTransitionError):
        db.rollback()
        return {"status": "skipped", "reason": "concurrent"}

    local_path = resolve_local_path(document)
    started = time.time()
    try:
        if (document.file_type or "").lower() in _ZIP_BASED_EXTS:
            inspect_zip_safety(local_path)
        if lease_refresh:
            lease_refresh()
        segments = extract_segments(str(local_path), document.file_type)
        artifact_bytes = json.dumps(segments, ensure_ascii=False, default=str).encode("utf-8")
        artifact_hash = hashlib.sha256(artifact_bytes).hexdigest()
        artifact_key = _artifact_object_key(document, artifact_hash)
        storage_service.put_stream(artifact_key, io.BytesIO(artifact_bytes), content_type="application/json")

        artifact = get_or_create_artifact(db, document)
        artifact.artifact_object_key = artifact_key
        artifact.artifact_hash = artifact_hash
        artifact.content_hash = content_hash
        artifact.object_key = document.object_key
        artifact.parser_version = PARSER_VERSION
        artifact.ocr_version = ocr_version()
        artifact.chunker_version = chunker_version()
        artifact.error_code = None
        artifact.error_summary = None
        artifact.processing_ms = int((time.time() - started) * 1000)
        document.parser_version = PARSER_VERSION
        document.ocr_version = ocr_version()
        document.chunker_version = chunker_version()

        transition_document(document, DOCUMENT_STATUS_PARSED, stage=DOCUMENT_STATUS_PARSED)
        db.commit()
        _complete_stage(db, begin["key"], {"segments": len(segments)})
        result = {
            "status": "success",
            "document_id": document.id,
            "segments": len(segments),
            "artifact_hash": artifact_hash,
        }
        if return_segments:
            result["segments_payload"] = segments
        return result
    except (DocumentParsePermanentError, DocumentSecurityError) as exc:
        _mark_failed(db, document, stage="parsing", error_code="PARSE_FAILED", error_message=str(exc))
        _fail_stage(db, begin["key"])
        raise
    except Exception:
        _mark_failed(db, document, stage="parsing", error_code="PARSE_ERROR", error_message="解析异常")
        _fail_stage(db, begin["key"])
        raise
    finally:
        storage_service.discard_temp_path(local_path)


# ── 阶段 2：chunk（解析产物 → 切分 → 写 DocumentChunk） ─────────────────────
def run_chunk(
    db: Session,
    document_id: int,
    *,
    expected_version: int | None = None,
    segments: list[dict] | None = None,
    split_text: Callable[..., list[dict]] | None = None,
    lease_refresh: Callable[[], None] | None = None,
) -> dict:
    split_text = split_text or _split_text
    document = db.query(Document).filter(Document.id == document_id).first()
    if document is None:
        return {"status": "skipped", "reason": "document_not_found"}
    if not version_matches(document, expected_version):
        return {"status": "skipped", "reason": "version_changed"}

    artifact = get_or_create_artifact(db, document)
    if segments is None:
        if not artifact.artifact_object_key:
            return {"status": "skipped", "reason": "parse_required"}
        segments = _read_artifact_segments(artifact)
    input_hash = chunk_input_hash(artifact.artifact_hash or "")

    begin = _begin_stage(db, document, "chunk", input_hash)
    if begin["replay"]:
        return {"status": "replayed", "document_id": document.id, "snapshot": begin["snapshot"]}
    if begin["skip"]:
        return {"status": "skipped", "reason": "already_running"}

    document = db.query(Document).filter(Document.id == document_id).first()
    try:
        update_stage(document, "chunking")
        db.commit()
    except StaleDataError:
        db.rollback()
        return {"status": "skipped", "reason": "concurrent"}

    try:
        if lease_refresh:
            lease_refresh()
        chunks = split_text(segments)
        # 清旧再插（(document_id, chunk_index) 唯一约束兜底）：重复执行不产生重复切片。
        db.query(DocumentChunk).filter(DocumentChunk.document_id == document_id).delete()
        db.add_all(_build_chunk_rows(document_id, chunks))
        artifact.chunk_count = len(chunks)
        artifact.chunker_version = chunker_version()
        artifact.error_code = None
        artifact.error_summary = None
        if document.status == DOCUMENT_STATUS_RETRYING:
            transition_document(document, DOCUMENT_STATUS_PARSED, stage=DOCUMENT_STATUS_PARSED)
        else:
            update_stage(document, DOCUMENT_STATUS_PARSED)
        db.commit()
        _complete_stage(db, begin["key"], {"chunks": len(chunks)})
        return {"status": "success", "document_id": document.id, "chunks": len(chunks)}
    except Exception:
        _mark_failed(db, document, stage="chunking", error_code="CHUNK_FAILED", error_message="切分失败")
        _fail_stage(db, begin["key"])
        raise


def _build_chunk_rows(document_id: int, chunks: list[dict]) -> list[DocumentChunk]:
    return [
        DocumentChunk(
            document_id=document_id,
            chunk_index=chunk["chunk_index"],
            content=chunk["content"],
            page_number=chunk.get("page_number"),
            section_title=chunk.get("section_title"),
            section_path=" > ".join(chunk.get("section_path") or []),
            segment_type=chunk.get("segment_type"),
            table_like=bool(chunk.get("table_like")),
            visual_tags=" ".join(chunk.get("visual_tags") or []),
            ocr_quality=chunk.get("ocr_quality"),
            embedding_id=chunk.get("embedding_id") or f"doc{document_id}_chunk{chunk['chunk_index']}",
        )
        for chunk in chunks
    ]


# ── 阶段 3：index（切分结果 → 向量索引） ─────────────────────────────────────
def _index_chunks_default(
    document_id: int,
    chunks: list[dict],
    *,
    user_id: int | None = None,
    knowledge_base_id: int | None = None,
) -> Exception | None:
    from app.services.rag_service import rag_service

    try:
        rag_service.index_document(
            document_id,
            _prepare_chunks_for_indexing(document_id, chunks),
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            document_status=DOCUMENT_STATUS_INDEXED,
        )
        return None
    except Exception as exc:  # noqa: BLE001 - 索引失败降级返回
        return exc


def run_index(
    db: Session,
    document_id: int,
    *,
    expected_version: int | None = None,
    user_id: int | None = None,
    knowledge_base_id: int | None = None,
    segments: list[dict] | None = None,
    index_chunks: Callable[..., Exception | None] | None = None,
    lease_refresh: Callable[[], None] | None = None,
) -> dict:
    index_chunks = index_chunks or _index_chunks_default
    splitter = _split_text
    document = db.query(Document).filter(Document.id == document_id).first()
    if document is None:
        return {"status": "skipped", "reason": "document_not_found"}
    if not version_matches(document, expected_version):
        return {"status": "skipped", "reason": "version_changed"}

    artifact = get_or_create_artifact(db, document)
    if segments is None:
        if not artifact.artifact_object_key:
            return {"status": "skipped", "reason": "parse_required"}
        segments = _read_artifact_segments(artifact)
    chunk_dicts = splitter(segments)
    chunks_hash = _chunks_hash_from_dicts(chunk_dicts)
    index_hash = index_input_hash(chunks_hash)

    begin = _begin_stage(db, document, "index", index_hash)
    if begin["replay"]:
        return {"status": "replayed", "document_id": document.id, "snapshot": begin["snapshot"]}
    if begin["skip"]:
        return {"status": "skipped", "reason": "already_running"}

    document = db.query(Document).filter(Document.id == document_id).first()
    # 已索引判定：包含切分指纹 + 索引器版本 + 嵌入模型（任一变化 → 重新索引）。
    if artifact.indexed_chunks_hash == index_hash and document.status == DOCUMENT_STATUS_INDEXED:
        _complete_stage(db, begin["key"], {"indexed": "already"})
        return {"status": "skipped", "reason": "already_indexed"}

    try:
        transition_document(document, DOCUMENT_STATUS_INDEXING, stage=DOCUMENT_STATUS_INDEXING)
        db.commit()
    except (StaleDataError, DocumentStateTransitionError):
        db.rollback()
        return {"status": "skipped", "reason": "concurrent"}

    try:
        if lease_refresh:
            lease_refresh()
        error = index_chunks(document.id, chunk_dicts, user_id=user_id, knowledge_base_id=knowledge_base_id)
        if error is not None:
            # 索引失败：降级到 parsed（解析/切分已成功），记录失败阶段。
            transition_document(
                document,
                DOCUMENT_STATUS_PARSED,
                failure_stage="indexing",
                error_code="INDEX_FAILED",
                error_message=str(error),
            )
            artifact.error_code = "INDEX_FAILED"
            artifact.error_summary = (str(error) or "")[:500]
            db.commit()
            _fail_stage(db, begin["key"])
            return {"status": "degraded", "document_id": document.id, "error": str(error)}
        artifact.indexed_chunks_hash = index_hash
        artifact.index_version = INDEX_VERSION
        artifact.embedding_model = settings.EMBEDDING_MODEL
        artifact.error_code = None
        artifact.error_summary = None
        document.index_version = INDEX_VERSION
        document.embedding_model = settings.EMBEDDING_MODEL
        transition_document(document, DOCUMENT_STATUS_INDEXED, stage=DOCUMENT_STATUS_INDEXED)
        db.commit()
        _complete_stage(db, begin["key"], {"indexed": len(chunk_dicts)})
        return {"status": "success", "document_id": document.id, "indexed": len(chunk_dicts)}
    except Exception:
        _mark_failed(db, document, stage="indexing", error_code="INDEX_ERROR", error_message="索引异常")
        _fail_stage(db, begin["key"])
        raise
