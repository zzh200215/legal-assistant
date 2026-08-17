"""文档处理任务：parse → chunk → index 流水线 + 摘要/分析 + 回收。

从 ``app.tasks.__init__`` 拆出（P3 上帝文件拆分），保持任务名与行为不变。
共享 helper（_retry_task / _lease_refresher / _job_summary_chunks）仅文档任务使用，随迁。
"""

import asyncio

from app.core.celery_app import celery_app
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.obs_context import enqueue_headers as obs_enqueue_headers
from app.core.observability import log_async_task_event
from app.core.observability_sanitizer import sanitize_background_error_message
from app.core.time import utc_now
from app.models.document import Document
from app.services.documents.analysis_service import analysis_service
from app.services.documents.document_job_service import document_job_service
from app.services.documents.document_parsing import DocumentParsePermanentError
from app.services.documents.document_pipeline import run_chunk, run_index, run_parse
from app.services.documents.document_security import DocumentSecurityError
from app.services.documents.document_state import (
    DOCUMENT_STATUS_RETRYING,
    DocumentStateTransitionError,
    transition_document,
)
from app.tasks.runtime import (
    acquire_document_lock as _acquire_document_lock,
    background_error_detail as _background_error_detail,
    beat_lock as _beat_lock,
    record_beat_heartbeat as _record_beat_heartbeat,
    release_document_lock as _release_document_lock,
)
from app.tasks.task_retry import retry_task as _retry_task_impl


def _retry_task(self, exc: Exception, **kwargs):
    return _retry_task_impl(
        self,
        exc,
        log_event=log_async_task_event,
        session_factory=SessionLocal,
        document_jobs=document_job_service,
        **kwargs,
    )


def _lease_refresher(job_id: int, owner: str):
    """心跳：用独立短会话续约，避免把主事务的未提交变更一起 flush。"""
    settings = get_settings()

    def refresh() -> None:
        try:
            db = SessionLocal()
            try:
                document_job_service.renew_lease(job_id, owner, settings.DOCUMENT_JOB_LEASE_TTL_SECONDS, db)
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - 续约失败不阻断处理（TTL 内由回收兜底）
            pass

    return refresh


def _job_summary_chunks(result: dict) -> int | None:
    for key in ("chunks", "segments", "indexed"):
        value = result.get(key)
        if value is not None:
            return int(value)
    return None


@celery_app.task(bind=True, name="parse_document")
def parse_document_task(self, document_id: int, version_number: int, file_type: str, snapshot_id: str | None = None):
    """异步文档流水线编排器：parse → chunk → index（各阶段幂等、版本守卫、租约刷新）。

    任一阶段抛出可重试异常时，文档置 retrying 并按配置重试；永久错误（不可解析/
    安全校验失败）记录失败不重试。
    """
    db = SessionLocal()
    owner = self.request.id
    job = None
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return {"status": "error", "message": "Document not found"}

        # 长流程权限快照：硬撤销（禁用/强制退出/授权撤销）立即终止。
        if snapshot_id:
            from app.services.org.authorization_service import authorization_service

            authorization_service.assert_snapshot(db, snapshot_id, user_id=doc.user_id)

        if not _acquire_document_lock(document_id, get_settings().DOCUMENT_JOB_LEASE_TTL_SECONDS):
            return {"status": "skipped", "reason": "document_locked"}

        job = document_job_service.find_or_create_job(
            document_id=document_id,
            user_id=doc.user_id,
            job_type="document_parse",
            db=db,
            task_id=owner,
        )
        document_job_service.claim_job(job.id, owner, get_settings().DOCUMENT_JOB_LEASE_TTL_SECONDS, db)
        log_async_task_event(
            user_id=doc.user_id,
            module="async_task",
            action="document_parse_started",
            target_type="document",
            target_id=document_id,
            detail=f"task_id={owner}; version={version_number}",
        )
        refresh = _lease_refresher(job.id, owner)

        # 阶段 1：parse（文本提取 → 产物存档）
        parse_result = run_parse(db, document_id, expected_version=version_number, user_id=doc.user_id, lease_refresh=refresh)
        if parse_result["status"] == "skipped":
            return {"status": "skipped", "reason": parse_result.get("reason"), "document_id": document_id}
        refresh()

        # 阶段 2：chunk（产物 → 切分 → 写 DocumentChunk）
        chunk_result = run_chunk(db, document_id, expected_version=version_number, lease_refresh=refresh)
        if chunk_result["status"] == "skipped":
            return {"status": "skipped", "reason": chunk_result.get("reason"), "document_id": document_id}
        refresh()

        # 阶段 3：index（切分结果 → 向量索引；失败降级为 parsed）
        index_result = run_index(
            db,
            document_id,
            expected_version=version_number,
            user_id=doc.user_id,
            knowledge_base_id=doc.knowledge_base_id,
            lease_refresh=refresh,
        )
        final = index_result["status"]

        chunk_count = _job_summary_chunks(chunk_result) or _job_summary_chunks(parse_result)
        degraded = final == "degraded"
        document_job_service.mark_succeeded(
            owner,
            db,
            message="文档解析完成，索引已降级" if degraded else "文档解析完成",
            result_summary=(
                f"共切分 {chunk_count or 0} 个文档片段，但索引失败：任务执行失败，请查看系统日志"
                if degraded
                else f"共切分 {chunk_count or 0} 个文档片段并完成索引"
            ),
        )
        log_async_task_event(
            user_id=doc.user_id,
            module="async_task",
            action="document_parse_succeeded",
            target_type="document",
            target_id=document_id,
            detail=f"task_id={owner}; chunks={chunk_count or 0}; indexed={not degraded}",
        )
        return {
            "status": "success",
            "document_id": document_id,
            "chunks": chunk_count or 0,
            "indexed": not degraded,
        }
    except (DocumentParsePermanentError, DocumentSecurityError) as e:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            log_async_task_event(
                user_id=doc.user_id,
                module="async_task",
                action="document_parse_failed",
                target_type="document",
                target_id=document_id,
                detail=_background_error_detail(owner),
            )
            document_job_service.mark_failed(
                owner,
                db,
                error_message=sanitize_background_error_message(str(e)),
                message="文档解析失败",
                retry_count=int(getattr(self.request, "retries", 0) or 0),
            )
        return {"status": "error", "document_id": document_id, "message": sanitize_background_error_message(str(e))}
    except Exception as e:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            # 置 retrying：重跑时从对应失败阶段恢复（阶段函数幂等）。
            try:
                transition_document(doc, DOCUMENT_STATUS_RETRYING, stage="retrying")
                db.commit()
            except DocumentStateTransitionError:
                db.rollback()
            _retry_task(
                self,
                e,
                user_id=doc.user_id,
                target_type="document",
                target_id=document_id,
                action_prefix="document_parse",
            )
        raise
    finally:
        if job:
            document_job_service.release_lease(job.id, owner, db)
        _release_document_lock(document_id)
        db.close()


@celery_app.task(bind=True, name="document_chunk")
def document_chunk_task(self, document_id: int, version_number: int, snapshot_id: str | None = None):
    """独立切分任务：可单独重试；成功后链式推进 index。"""
    db = SessionLocal()
    owner = self.request.id
    job = None
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return {"status": "error", "message": "Document not found"}
        if snapshot_id:
            from app.services.org.authorization_service import authorization_service

            authorization_service.assert_snapshot(db, snapshot_id, user_id=doc.user_id)
        if not _acquire_document_lock(document_id, get_settings().DOCUMENT_JOB_LEASE_TTL_SECONDS):
            return {"status": "skipped", "reason": "document_locked"}
        job = document_job_service.find_or_create_job(
            document_id=document_id, user_id=doc.user_id, job_type="document_chunk", db=db, task_id=owner
        )
        document_job_service.claim_job(job.id, owner, get_settings().DOCUMENT_JOB_LEASE_TTL_SECONDS, db)
        refresh = _lease_refresher(job.id, owner)
        result = run_chunk(db, document_id, expected_version=version_number, lease_refresh=refresh)
        if result["status"] in ("success", "replayed"):
            document_index_task.delay(document_id, version_number, headers=obs_enqueue_headers())
            document_job_service.mark_succeeded(owner, db, message="文档切分完成", result_summary=f"共切分 {result.get('chunks', 0)} 个片段")
            result["status"] = "success"
        return result
    except Exception as e:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            try:
                transition_document(doc, DOCUMENT_STATUS_RETRYING, stage="retrying")
                db.commit()
            except DocumentStateTransitionError:
                db.rollback()
            _retry_task(
                self, e, user_id=doc.user_id, target_type="document", target_id=document_id, action_prefix="document_chunk"
            )
        raise
    finally:
        if job:
            document_job_service.release_lease(job.id, owner, db)
        _release_document_lock(document_id)
        db.close()


@celery_app.task(bind=True, name="document_index")
def document_index_task(self, document_id: int, version_number: int):
    """独立索引任务：可单独重试；索引失败降级为 parsed（不抛异常）。"""
    db = SessionLocal()
    owner = self.request.id
    job = None
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return {"status": "error", "message": "Document not found"}
        if not _acquire_document_lock(document_id, get_settings().DOCUMENT_JOB_LEASE_TTL_SECONDS):
            return {"status": "skipped", "reason": "document_locked"}
        job = document_job_service.find_or_create_job(
            document_id=document_id, user_id=doc.user_id, job_type="document_index", db=db, task_id=owner
        )
        document_job_service.claim_job(job.id, owner, get_settings().DOCUMENT_JOB_LEASE_TTL_SECONDS, db)
        refresh = _lease_refresher(job.id, owner)
        result = run_index(
            db,
            document_id,
            expected_version=version_number,
            user_id=doc.user_id,
            knowledge_base_id=doc.knowledge_base_id,
            lease_refresh=refresh,
        )
        if result["status"] == "success":
            document_job_service.mark_succeeded(owner, db, message="文档索引完成", result_summary=f"已索引 {result.get('indexed', 0)} 个片段")
        elif result["status"] == "degraded":
            document_job_service.mark_succeeded(owner, db, message="文档索引已降级", result_summary="索引失败，文档保持已解析状态")
        return result
    except Exception as e:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if doc:
            try:
                transition_document(doc, DOCUMENT_STATUS_RETRYING, stage="retrying")
                db.commit()
            except DocumentStateTransitionError:
                db.rollback()
            _retry_task(
                self, e, user_id=doc.user_id, target_type="document", target_id=document_id, action_prefix="document_index"
            )
        raise
    finally:
        if job:
            document_job_service.release_lease(job.id, owner, db)
        _release_document_lock(document_id)
        db.close()


@celery_app.task(name="document_export")
def document_export_task(document_id: int, export_type: str = "archive", user_id: int | None = None):
    """Fail explicitly until an actual document export implementation is available."""
    log_async_task_event(
        user_id=user_id,
        module="async_task",
        action="document_export_unavailable",
        target_type="document",
        target_id=document_id,
        detail=f"export_type={export_type}",
    )
    raise NotImplementedError("文档导出功能尚未实现，任务未受理")


@celery_app.task(name="recover_stale_document_jobs")
@_beat_lock(task_name="recover_stale_document_jobs", ttl_seconds=600)
def recover_stale_document_jobs_task():
    """Beat 任务：回收租约过期/worker 崩溃残留的文档处理任务并重新入队。

    按 job_type 重新投递对应阶段任务（parse/chunk/index 均幂等、版本守卫），
    进程崩溃、租约过期、网络异常后的任务可安全恢复。决策逻辑见
    document_job_service.plan_recovery（独立可测）。
    """
    _record_beat_heartbeat()
    from datetime import timedelta

    db = SessionLocal()
    try:
        settings = get_settings()
        stale_before = utc_now() - timedelta(seconds=settings.DOCUMENT_JOB_LEASE_TTL_SECONDS)
        plans = document_job_service.plan_recovery(db, stale_before=stale_before, limit=50)
        recovered = 0
        for job, task_name in plans:
            doc = db.query(Document).filter(Document.id == job.document_id).first()
            if task_name == "chunk":
                new_task = document_chunk_task.delay(doc.id, doc.version_number, headers=obs_enqueue_headers())
            elif task_name == "index":
                new_task = document_index_task.delay(doc.id, doc.version_number, headers=obs_enqueue_headers())
            else:
                new_task = parse_document_task.delay(
                    doc.id, doc.version_number, doc.file_type, headers=obs_enqueue_headers(),
                )
            # 复用同一 job 记录：绑定新 task_id，下次领取即新租约。
            job.task_id = new_task.id
            db.add(job)
            db.commit()
            recovered += 1
        return {"recovered": recovered}
    finally:
        db.close()


@celery_app.task(bind=True, name="summarize_document")
def summarize_document_task(self, document_id: int, user_id: int, max_length: int = 500, snapshot_id: str | None = None):
    db = SessionLocal()
    try:
        # 长流程权限快照：硬撤销立即终止。
        if snapshot_id:
            from app.services.org.authorization_service import authorization_service
            authorization_service.assert_snapshot(db, snapshot_id, user_id=user_id)

        log_async_task_event(
            user_id=user_id,
            module="async_task",
            action="document_summary_started",
            target_type="document",
            target_id=document_id,
            detail=f"task_id={self.request.id}; max_length={max_length}",
        )
        document_job_service.mark_started(
            self.request.id,
            db,
            current_step="loading_document",
            message="正在加载文档内容",
            progress=15,
        )
        self.update_state(state="PROCESSING", meta={"step": "loading_document"})
        from app.services.documents.document_service import document_service

        raw_text = document_service.summarize(document_id, db, user_id=user_id)

        self.update_state(state="PROCESSING", meta={"step": "summarizing"})
        document_job_service.update_progress(
            self.request.id,
            db,
            current_step="summarizing",
            message="正在生成文档摘要",
            progress=65,
        )
        summary = asyncio.run(analysis_service.summarize_document(raw_text, max_length=max_length))

        doc = document_service.get(document_id, db, user_id=user_id)
        if doc:
            doc.summary = summary
            db.commit()

        log_async_task_event(
            user_id=user_id,
            module="async_task",
            action="document_summary_succeeded",
            target_type="document",
            target_id=document_id,
            detail=f"task_id={self.request.id}",
        )
        document_job_service.mark_succeeded(
            self.request.id,
            db,
            message="文档摘要完成",
            result_summary=(summary or "")[:500],
        )
        return {"document_id": document_id, "summary": summary}
    except Exception as e:
        _retry_task(
            self,
            e,
            user_id=user_id,
            target_type="document",
            target_id=document_id,
            action_prefix="document_summary",
        )
    finally:
        db.close()


@celery_app.task(bind=True, name="analyze_document")
def analyze_document_task(self, document_id: int, user_id: int, max_length: int = 500, snapshot_id: str | None = None):
    db = SessionLocal()
    try:
        # 长流程权限快照：硬撤销立即终止。
        if snapshot_id:
            from app.services.org.authorization_service import authorization_service
            authorization_service.assert_snapshot(db, snapshot_id, user_id=user_id)

        log_async_task_event(
            user_id=user_id,
            module="async_task",
            action="document_analysis_started",
            target_type="document",
            target_id=document_id,
            detail=f"task_id={self.request.id}; max_length={max_length}",
        )
        document_job_service.mark_started(
            self.request.id,
            db,
            current_step="loading_document",
            message="正在加载文档内容",
            progress=15,
        )
        self.update_state(state="PROCESSING", meta={"step": "loading_document"})
        from app.services.documents.document_service import document_service

        document_job_service.update_progress(
            self.request.id,
            db,
            current_step="analyzing",
            message="正在分析摘要、风险、待办和条款",
            progress=60,
        )
        result = asyncio.run(
            document_service.analyze(
                document_id=document_id,
                db=db,
                user_id=user_id,
                max_length=max_length,
            )
        )
        log_async_task_event(
            user_id=user_id,
            module="async_task",
            action="document_analysis_succeeded",
            target_type="document",
            target_id=document_id,
            detail=f"task_id={self.request.id}; analysis_status={(result or {}).get('analysis_status', 'success')}",
        )
        result_summary = (result.get("summary") or "")[:500] if isinstance(result, dict) else None
        document_job_service.mark_succeeded(
            self.request.id,
            db,
            message="文档分析完成（部分降级）" if isinstance(result, dict) and result.get("analysis_status") == "partial" else "文档分析完成",
            result_summary=result_summary,
        )
        return result
    except Exception as e:
        _retry_task(
            self,
            e,
            user_id=user_id,
            target_type="document",
            target_id=document_id,
            action_prefix="document_analysis",
        )
    finally:
        db.close()
