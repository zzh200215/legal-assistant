import io
import json
import logging

from sqlalchemy.orm import Session

from app.core.obs_context import enqueue_headers as obs_enqueue_headers
from app.models.document import Document, DocumentChunk
from app.models.user import User
from app.services.documents.document_governance_service import document_governance_service
from app.services.documents.document_job_service import document_job_service
from app.services.documents.document_parsing import (
    _normalize_text,
    _sha256_bytes,
)
from app.services.documents.document_security import (
    _ZIP_BASED_EXTS,
    DocumentSecurityError,
    build_virus_scanner,
    detect_mime,
    inspect_zip_safety,
    inspect_zip_safety_bytes,
    spool_upload_to_temp,
)
from app.services.storage.storage_service import storage_service

logger = logging.getLogger(__name__)


class IngestMixin:
    def import_file_document(
        self,
        *,
        db: Session,
        user_id: int,
        title: str,
        file_bytes: bytes,
        file_type: str,
        knowledge_base_name: str | None = None,
        knowledge_base_category: str | None = None,
        classification: str | None = None,
        tags: list[str] | None = None,
        permission_scope: str = "private",
        sensitivity_level: str = "internal",
        permission_users: list[str] | None = None,
        permission_roles: list[str] | None = None,
        metadata: dict | None = None,
    ) -> tuple[Document, bool]:
        file_ext = file_type.lstrip(".") if file_type else "txt"
        content_hash = _sha256_bytes(file_bytes)
        size = len(file_bytes)
        # 真实 MIME 校验（文本导入同样校验，避免伪造扩展名）+ zip-bomb 防护
        head = file_bytes[:512]
        ext, mime_type = detect_mime(head, f"document.{file_ext}")
        if ext in _ZIP_BASED_EXTS:
            inspect_zip_safety_bytes(file_bytes)
        current_user = db.query(User).filter(User.id == user_id).first()
        knowledge_base = self._resolve_knowledge_base(
            db=db,
            user_id=user_id,
            current_user=current_user,
            knowledge_base_name=knowledge_base_name,
            knowledge_base_category=knowledge_base_category,
            permission_scope=permission_scope,
        )
        doc, created = self._persist_document_record(
            db=db,
            user_id=user_id,
            current_user=current_user,
            title=title,
            file_type=ext,
            content_hash=content_hash,
            knowledge_base=knowledge_base,
            classification=classification,
            tags=tags,
            permission_scope=permission_scope,
            sensitivity_level=sensitivity_level,
            permission_users=permission_users,
            permission_roles=permission_roles,
            metadata=metadata,
            mime_type=mime_type,
            size=size,
            status="uploaded",
        )
        if not created:
            return doc, False

        # 存储抽象：只写 object_key，不存本地路径/二进制。
        doc.object_key = storage_service.build_object_key(
            user_id=user_id, document_id=doc.id, version_number=doc.version_number, file_ext=ext
        )
        with io.BytesIO(file_bytes) as stream:
            storage_service.put_stream(doc.object_key, stream, content_type=mime_type)
        db.add(doc)
        db.commit()

        try:
            self._run_sync_pipeline(db, doc, user_id=user_id, knowledge_base_id=doc.knowledge_base_id)
        except Exception:
            # 解析失败：删除已落库的孤儿行与存储对象，避免内容哈希去重导致重试永远"已存在"
            self._cleanup_failed_document(db, doc)
            raise
        return doc, True

    @staticmethod
    def _parse_metadata_json(raw: str | None) -> dict:
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except (TypeError, ValueError):
            return {}

    @staticmethod
    def _cleanup_failed_document(db: Session, doc: Document) -> None:
        """解析失败清理：删除存储对象 + 切片 + 文档记录（不吞异常，由调用方继续抛出）。"""
        try:
            if doc.object_key:
                storage_service.delete(doc.object_key)
        except Exception:
            logger.exception(
                "清理失败文档的存储对象时出错 document_id=%s object_key=%s",
                doc.id, doc.object_key,
            )
        db.query(DocumentChunk).filter(DocumentChunk.document_id == doc.id).delete()
        db.delete(doc)
        db.commit()

    def _resolve_knowledge_base(
        self,
        *,
        db: Session,
        user_id: int,
        current_user: User | None,
        knowledge_base_name: str | None,
        knowledge_base_category: str | None,
        permission_scope: str,
    ):
        if not knowledge_base_name:
            return None
        return document_governance_service.get_or_create_knowledge_base(
            db=db,
            user_id=user_id,
            name=knowledge_base_name,
            organization_id=current_user.organization_id if current_user else None,
            department_id=current_user.department_id if current_user else None,
            category=knowledge_base_category,
            permission_scope=permission_scope,
        )

    def _persist_document_record(
        self,
        *,
        db: Session,
        user_id: int,
        current_user: User | None,
        title: str,
        file_type: str,
        content_hash: str,
        knowledge_base,
        classification: str | None,
        tags: list[str] | None,
        permission_scope: str,
        sensitivity_level: str,
        permission_users: list[str] | None,
        permission_roles: list[str] | None,
        metadata: dict | None,
        status: str,
        file_path: str | None = None,
        object_key: str | None = None,
        mime_type: str | None = None,
        size: int | None = None,
    ) -> tuple[Document, bool]:
        latest_version = document_governance_service.find_latest_version(
            db=db,
            user_id=user_id,
            title=title,
            content_hash=content_hash,
        )
        if latest_version:
            return latest_version, False

        latest_by_title = document_governance_service.find_latest_version(
            db=db,
            user_id=user_id,
            title=title,
            content_hash=None,
        )
        parent_document_id = (
            latest_by_title.parent_document_id if latest_by_title and latest_by_title.parent_document_id else None
        )
        if latest_by_title and not parent_document_id:
            parent_document_id = latest_by_title.id

        doc = Document(
            user_id=user_id,
            organization_id=current_user.organization_id if current_user else None,
            department_id=current_user.department_id if current_user else None,
            knowledge_base_id=knowledge_base.id if knowledge_base else None,
            parent_document_id=parent_document_id,
            version_number=(latest_by_title.version_number + 1) if latest_by_title else 1,
            title=title,
            file_path=file_path,
            object_key=object_key,
            mime_type=mime_type,
            size=size,
            file_type=file_type,
            content_hash=content_hash,
            classification=classification,
            tags=json.dumps(tags or [], ensure_ascii=False),
            permission_scope=permission_scope,
            sensitivity_level=sensitivity_level or "internal",
            permission_users=json.dumps(permission_users or [], ensure_ascii=False),
            permission_roles=json.dumps(permission_roles or [], ensure_ascii=False),
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            status=status,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        document_governance_service.assign_document_access_rules(
            db=db,
            document_id=doc.id,
            users=permission_users or [],
            roles=permission_roles or [],
        )
        return doc, True

    def import_text_document(
        self,
        *,
        db: Session,
        user_id: int,
        title: str,
        content: str,
        file_type: str = "md",
        knowledge_base_name: str | None = None,
        knowledge_base_category: str | None = None,
        classification: str | None = None,
        tags: list[str] | None = None,
        permission_scope: str = "private",
        sensitivity_level: str = "internal",
        permission_users: list[str] | None = None,
        permission_roles: list[str] | None = None,
        metadata: dict | None = None,
    ) -> tuple[Document, bool]:
        normalized_content = _normalize_text(content or "")
        file_ext = file_type.lstrip(".") if file_type else "md"
        return self.import_file_document(
            db=db,
            user_id=user_id,
            title=title,
            file_bytes=normalized_content.encode("utf-8"),
            file_type=file_ext,
            knowledge_base_name=knowledge_base_name,
            knowledge_base_category=knowledge_base_category,
            classification=classification,
            tags=tags,
            permission_scope=permission_scope,
            sensitivity_level=sensitivity_level,
            permission_users=permission_users,
            permission_roles=permission_roles,
            metadata=metadata,
        )

    def upload(
        self,
        file,
        user_id: int,
        db: Session,
        async_mode: bool = False,
        *,
        knowledge_base_name: str | None = None,
        knowledge_base_category: str | None = None,
        classification: str | None = None,
        tags: list[str] | None = None,
        permission_scope: str = "private",
        sensitivity_level: str = "internal",
        permission_users: list[str] | None = None,
        permission_roles: list[str] | None = None,
        metadata: dict | None = None,
    ) -> Document:
        from app.core.config import get_settings

        settings = get_settings()
        filename = getattr(file, "filename", None) or "document"

        # 1) 真实 MIME：只读文件头 512B 嗅探，与扩展名/Content-Type 交叉校验。
        head = file.file.read(512)
        file.file.seek(0)
        ext, mime_type = detect_mime(head, filename)

        # 2) 流式落盘临时文件：分块读取、边算 SHA-256、强制大小上限（禁止整体 read()）。
        temp_path, size, content_hash = spool_upload_to_temp(
            file.file, max_bytes=settings.DOCUMENT_MAX_UPLOAD_MB * 1024 * 1024
        )
        try:
            # 3) zip-bomb（docx/xlsx）：只读中央目录，不实际解压。
            if ext in _ZIP_BASED_EXTS:
                inspect_zip_safety(temp_path)
            # 4) 病毒扫描：默认“未配置扫描器”策略，不伪造结果。
            scan_result = build_virus_scanner().scan(temp_path)
            if not scan_result.clean:
                raise DocumentSecurityError("DOCUMENT_VIRUS_FOUND", scan_result.note)

            current_user = db.query(User).filter(User.id == user_id).first()
            knowledge_base = self._resolve_knowledge_base(
                db=db,
                user_id=user_id,
                current_user=current_user,
                knowledge_base_name=knowledge_base_name,
                knowledge_base_category=knowledge_base_category,
                permission_scope=permission_scope,
            )

            # 5) 内容去重：同 title + content_hash 直接复用，避免重复创建等价对象/切片/索引。
            existing = document_governance_service.find_latest_version(
                db=db, user_id=user_id, title=filename, content_hash=content_hash
            )
            if existing:
                return existing

            doc, created = self._persist_document_record(
                db=db,
                user_id=user_id,
                current_user=current_user,
                title=filename,
                file_type=ext,
                content_hash=content_hash,
                knowledge_base=knowledge_base,
                classification=classification,
                tags=tags,
                permission_scope=permission_scope,
                sensitivity_level=sensitivity_level,
                permission_users=permission_users,
                permission_roles=permission_roles,
                metadata=metadata,
                mime_type=mime_type,
                size=size,
                status="uploaded",
            )
            if not created:
                return doc

            # 6) 写入对象存储：流式、只存 object_key（可预测/租户隔离命名）。
            doc.object_key = storage_service.build_object_key(
                user_id=user_id, document_id=doc.id, version_number=doc.version_number, file_ext=ext
            )
            with open(temp_path, "rb") as src:
                storage_service.put_stream(doc.object_key, src, content_type=mime_type)
            doc.content_hash = content_hash
            doc.size = size
            doc.mime_type = mime_type
            db.add(doc)
            db.commit()

            if async_mode:
                # 长流程权限快照：保证后台解析期间权限范围稳定。
                snapshot_id = None
                try:
                    from app.services.org.authorization_service import authorization_service

                    user_row = db.query(User).filter(User.id == user_id).first()
                    if user_row:
                        ctx = authorization_service.build_context(db, user_row)
                        snapshot_id = authorization_service.capture_snapshot(
                            db, user_row, ctx, document_ids=[doc.id],
                        )
                except Exception:
                    # 快照失败不阻断上传；文档为创建者所有，访问路径由任务内校验兜底。
                    pass
                from app.tasks import parse_document_task

                job = document_job_service.create_job(
                    document_id=doc.id,
                    user_id=user_id,
                    job_type="document_parse",
                    db=db,
                    current_step="submitted",
                    message="文档解析任务已提交",
                )
                task = parse_document_task.delay(
                    doc.id, doc.version_number, doc.file_type, snapshot_id,
                    headers=obs_enqueue_headers(),
                )
                document_job_service.attach_task_id(job.id, task.id, db)
                return doc

            # 同步快路径：走与异步一致的幂等阶段函数（兼容既有测试）。
            try:
                self._run_sync_pipeline(db, doc, user_id=user_id, knowledge_base_id=doc.knowledge_base_id)
            except Exception:
                # 解析失败：清理已落库的孤儿记录与存储对象，避免重传被内容哈希去重命中悬挂记录
                self._cleanup_failed_document(db, doc)
                raise
            return doc
        finally:
            temp_path.unlink(missing_ok=True)
