from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

from sqlalchemy.orm import Session

from app.core.observability import log_async_task_event
from app.models.connector import ConnectorOAuthState, ConnectorSyncJob, ExternalConnector
from app.models.user import User
from app.services.mailbox_service import mailbox_service
from app.services.oplog_service import oplog_service
from app.services.storage_service import storage_service

SUPPORTED_CONNECTOR_FILE_TYPES = {".md", ".txt", ".csv", ".pdf", ".docx", ".xlsx"}


class ConnectorService:
    CREDENTIAL_ROTATION_TYPES = {"imap_mailbox", "smtp_outbound", "oa_approval"}
    ENTERPRISE_CONNECTOR_TYPES = {"ms_graph_onedrive", "ms_graph_sharepoint", "erp_rest", "crm_rest"}
    @staticmethod
    def _default_permission_scope(connector: ExternalConnector) -> str:
        if connector.department_id:
            return "department"
        if connector.organization_id:
            return "org"
        return "private"

    @staticmethod
    def _can_access_connector(
        connector: ExternalConnector,
        *,
        user: User,
    ) -> bool:
        if user.role == "admin":
            return True
        if connector.user_id == user.id:
            return True
        if connector.department_id:
            return bool(user.department_id and user.department_id == connector.department_id)
        if user.organization_id and connector.organization_id and user.organization_id == connector.organization_id:
            return True
        return False

    @staticmethod
    def _normalize_seed_document(raw: dict, *, connector: ExternalConnector, default_kb_name: str) -> dict:
        title = str(raw.get("title") or "").strip() or f"{connector.name}-导入文档.md"
        content = str(raw.get("content") or "").strip()
        file_type = str(raw.get("file_type") or "md").strip().lower() or "md"
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        return {
            "title": title,
            "content": content or f"# {connector.name}\n\n来源连接器：{connector.name}",
            "file_type": file_type,
            "knowledge_base_name": str(raw.get("knowledge_base_name") or default_kb_name).strip(),
            "knowledge_base_category": str(raw.get("knowledge_base_category") or connector.connector_type).strip(),
            "classification": str(raw.get("classification") or connector.connector_type).strip(),
            "tags": [str(item).strip() for item in (raw.get("tags") or []) if str(item).strip()],
            "permission_scope": str(raw.get("permission_scope") or ConnectorService._default_permission_scope(connector)).strip(),
            "sensitivity_level": str(raw.get("sensitivity_level") or "internal").strip(),
            "metadata": metadata,
        }

    @staticmethod
    def _is_within(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _resolve_local_source_dir(self, raw_path: str | None) -> Path | None:
        if not raw_path:
            return None
        path_value = Path(str(raw_path).strip()).expanduser()
        candidate = path_value if path_value.is_absolute() else (Path.cwd() / path_value)
        resolved = candidate.resolve()
        allowed_roots = [
            Path.cwd().resolve(),
            storage_service.base_dir().resolve(),
        ]
        if not any(self._is_within(resolved, root) for root in allowed_roots):
            return None
        if not resolved.exists() or not resolved.is_dir():
            return None
        return resolved

    @staticmethod
    def _normalize_extensions(values, *, fallback: set[str] | None = None) -> set[str]:
        if not isinstance(values, list):
            return set(fallback or set())
        items: set[str] = set()
        for value in values:
            cleaned = str(value or "").strip().lower()
            if not cleaned:
                continue
            items.add(cleaned if cleaned.startswith(".") else f".{cleaned}")
        return items

    @staticmethod
    def _coerce_positive_int(value, *, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _coerce_bool(value, *, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                return True
            if normalized in {"false", "0", "no", "n"}:
                return False
        return default

    def _build_documents_from_local_dir(
        self,
        *,
        connector: ExternalConnector,
        source_dir: Path,
        default_kb_name: str,
        config: dict,
    ) -> list[dict]:
        include_extensions = self._normalize_extensions(
            config.get("include_extensions"),
            fallback=SUPPORTED_CONNECTOR_FILE_TYPES,
        )
        exclude_extensions = self._normalize_extensions(config.get("exclude_extensions"))
        recursive = self._coerce_bool(config.get("recursive"), default=True)
        max_files = self._coerce_positive_int(config.get("max_files"), default=50)
        iterator = source_dir.rglob("*") if recursive else source_dir.glob("*")
        items: list[dict] = []
        for file_path in sorted(iterator):
            suffix = file_path.suffix.lower()
            if not file_path.is_file() or suffix not in SUPPORTED_CONNECTOR_FILE_TYPES:
                continue
            if include_extensions and suffix not in include_extensions:
                continue
            if exclude_extensions and suffix in exclude_extensions:
                continue
            relative_path = file_path.relative_to(source_dir).as_posix()
            item = {
                "title": relative_path,
                "file_type": suffix.lstrip(".") or "txt",
                "knowledge_base_name": default_kb_name,
                "knowledge_base_category": connector.connector_type,
                "classification": connector.connector_type,
                "tags": ["connector", connector.connector_type, "local_sync"],
                "permission_scope": self._default_permission_scope(connector),
                "sensitivity_level": "internal",
                "metadata": {
                    "connector_source_path": str(source_dir),
                    "connector_origin_file": relative_path,
                    "generated_by": "connector_sync_local_dir",
                    "connector_recursive": recursive,
                },
            }
            if suffix in {".md", ".txt", ".csv"}:
                try:
                    item["content"] = file_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    item["content"] = file_path.read_text(encoding="utf-8", errors="ignore")
            else:
                item["source_file_path"] = str(file_path)
            items.append(item)
            if len(items) >= max_files:
                break
        return items

    def create_connector(
        self,
        *,
        db: Session,
        user: User,
        connector_type: str,
        name: str,
        config_json: str | None = None,
    ) -> ExternalConnector:
        connector_type = connector_type.strip()
        if connector_type == "imap_mailbox":
            raise ValueError("请使用邮箱专用连接接口创建 IMAP 连接器")
        if connector_type in self.ENTERPRISE_CONNECTOR_TYPES:
            raise ValueError("企业数据源请使用管理员企业连接器接口创建")
        config = self.parse_config(config_json)
        sensitive_keys = {"password", "authorization_code", "access_token", "refresh_token", "client_secret", "api_key"}
        if any(key in config for key in sensitive_keys):
            raise ValueError("通用连接器配置不能包含凭据，请使用专用连接器接口")
        row = ExternalConnector(
            user_id=user.id,
            organization_id=user.organization_id,
            department_id=user.department_id,
            connector_type=connector_type,
            name=name.strip(),
            config_json=config_json,
            status="active",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def _validate_https_endpoint(value: object, *, field_name: str) -> None:
        parsed = urlparse(str(value or "").strip())
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f"{field_name} 必须是合法 HTTPS 地址，且不能含账号或密码")

    def _validate_enterprise_config(self, *, connector_type: str, config: dict, credentials: dict[str, str]) -> dict:
        if connector_type not in self.ENTERPRISE_CONNECTOR_TYPES:
            raise ValueError("不支持的企业连接器类型")
        if not isinstance(config, dict) or not isinstance(credentials, dict):
            raise ValueError("企业连接器配置格式不正确")
        sensitive_keys = {"password", "authorization_code", "access_token", "refresh_token", "client_secret", "api_key", "bearer_token"}
        if sensitive_keys.intersection(config):
            raise ValueError("令牌和密钥必须通过凭据字段保存，不能写入配置")
        cleaned_credentials = {
            str(key).strip(): str(value).strip()
            for key, value in credentials.items()
            if str(key).strip() and str(value).strip()
        }
        if connector_type in {"ms_graph_onedrive", "ms_graph_sharepoint"}:
            if not cleaned_credentials.get("access_token") and not cleaned_credentials.get("client_secret"):
                raise ValueError("Microsoft Graph 连接器需要 access_token，或配置 client_id/tenant_id 后提供 client_secret")
            if cleaned_credentials.get("client_secret") and not (config.get("client_id") and config.get("tenant_id")):
                raise ValueError("使用 OAuth 授权时，Graph 配置必须包含 client_id 和 tenant_id")
            if connector_type == "ms_graph_sharepoint" and not (config.get("site_id") or config.get("drive_id")):
                raise ValueError("SharePoint 连接器需要 site_id 或 drive_id")
        else:
            self._validate_https_endpoint(config.get("endpoint"), field_name="endpoint")
            if not (cleaned_credentials.get("bearer_token") or cleaned_credentials.get("api_key")):
                raise ValueError("ERP/CRM 连接器需要 bearer_token 或 api_key")
        return cleaned_credentials

    @staticmethod
    def _oauth_redirect_uri(value: str) -> str:
        parsed = urlparse(str(value or "").strip())
        if parsed.scheme not in {"https", "http"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("redirect_uri 必须是合法 HTTP(S) 地址，且不能包含账号或密码")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
            raise ValueError("生产环境 redirect_uri 必须使用 HTTPS")
        return parsed.geturl()

    def start_microsoft_oauth(
        self,
        *,
        db: Session,
        user: User,
        connector_id: int,
        redirect_uri: str,
    ) -> dict:
        connector = db.query(ExternalConnector).filter(
            ExternalConnector.id == connector_id,
            ExternalConnector.connector_type.in_(self.ENTERPRISE_CONNECTOR_TYPES),
            ExternalConnector.connector_type.in_({"ms_graph_onedrive", "ms_graph_sharepoint"}),
            ExternalConnector.status == "active",
        ).first()
        if not connector or (connector.organization_id and connector.organization_id != user.organization_id):
            raise ValueError("Microsoft Graph 连接器不存在或无权操作")
        config = self.parse_config(connector.config_json)
        tenant_id = str(config.get("tenant_id") or "").strip()
        client_id = str(config.get("client_id") or "").strip()
        if not tenant_id or not client_id:
            raise ValueError("请先在连接器配置中填写 tenant_id 和 client_id")
        redirect_uri = self._oauth_redirect_uri(redirect_uri)
        credentials = mailbox_service.decrypt_credentials(connector.credential_ciphertext) if connector.credential_ciphertext else {}
        if not credentials.get("client_secret"):
            raise ValueError("请先保存 OAuth client_secret")

        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
        db.add(ConnectorOAuthState(
            connector_id=connector.id,
            user_id=user.id,
            state_hash=hashlib.sha256(state.encode("utf-8")).hexdigest(),
            code_verifier_ciphertext=mailbox_service.encrypt_credentials({"code_verifier": verifier}),
            redirect_uri=redirect_uri,
            expires_at=expires_at,
        ))
        db.commit()
        scopes = config.get("scopes") if isinstance(config.get("scopes"), list) else []
        if not scopes:
            scopes = ["offline_access", "Files.Read.All"]
            if connector.connector_type == "ms_graph_sharepoint":
                scopes.append("Sites.Read.All")
        authorize_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?" + urlencode({
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": " ".join(str(scope).strip() for scope in scopes if str(scope).strip()),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return {"authorize_url": authorize_url, "expires_at": expires_at}

    def complete_microsoft_oauth(self, *, db: Session, code: str, state: str) -> ExternalConnector:
        state_hash = hashlib.sha256(str(state).encode("utf-8")).hexdigest()
        grant = db.query(ConnectorOAuthState).filter(
            ConnectorOAuthState.state_hash == state_hash,
            ConnectorOAuthState.consumed_at.is_(None),
        ).first()
        now = datetime.now(timezone.utc)
        if not grant or grant.expires_at.replace(tzinfo=timezone.utc) <= now:
            raise ValueError("授权状态已失效，请重新发起授权")
        connector = db.query(ExternalConnector).filter(
            ExternalConnector.id == grant.connector_id,
            ExternalConnector.connector_type.in_({"ms_graph_onedrive", "ms_graph_sharepoint"}),
            ExternalConnector.status == "active",
        ).first()
        if not connector:
            raise ValueError("Microsoft Graph 连接器不存在或已停用")
        config = self.parse_config(connector.config_json)
        tenant_id = str(config.get("tenant_id") or "").strip()
        client_id = str(config.get("client_id") or "").strip()
        credentials = mailbox_service.decrypt_credentials(connector.credential_ciphertext) if connector.credential_ciphertext else {}
        client_secret = str(credentials.get("client_secret") or "").strip()
        verifier = mailbox_service.decrypt_credentials(grant.code_verifier_ciphertext).get("code_verifier")
        if not tenant_id or not client_id or not client_secret or not verifier:
            raise ValueError("OAuth 配置不完整，请重新保存连接器凭据")
        response = httpx.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": grant.redirect_uri,
                "code_verifier": verifier,
                "scope": "offline_access Files.Read.All Sites.Read.All",
            },
            timeout=20,
        )
        response.raise_for_status()
        token_payload = response.json()
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("Microsoft 未返回 access_token")
        merged = {
            "client_secret": client_secret,
            "access_token": access_token,
            "refresh_token": str(token_payload.get("refresh_token") or credentials.get("refresh_token") or "").strip(),
            "expires_at": (now + timedelta(seconds=int(token_payload.get("expires_in") or 3600))).isoformat(),
        }
        connector.credential_ciphertext = mailbox_service.encrypt_credentials(merged)
        grant.consumed_at = now
        db.commit()
        db.refresh(connector)
        oplog_service.log(
            module="connector",
            action="microsoft_oauth_completed",
            db=db,
            user_id=grant.user_id,
            target_type="connector",
            target_id=connector.id,
            detail=f"connector_type={connector.connector_type}; credential_values=redacted",
        )
        return connector

    def create_enterprise_connector(
        self,
        *,
        db: Session,
        user: User,
        connector_type: str,
        name: str,
        config: dict,
        credentials: dict[str, str],
    ) -> ExternalConnector:
        cleaned_credentials = self._validate_enterprise_config(
            connector_type=connector_type,
            config=config,
            credentials=credentials,
        )
        row = ExternalConnector(
            user_id=user.id,
            organization_id=user.organization_id,
            department_id=None,
            connector_type=connector_type,
            name=name.strip(),
            config_json=json.dumps(config, ensure_ascii=False),
            credential_ciphertext=mailbox_service.encrypt_credentials(cleaned_credentials),
            status="active",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        oplog_service.log(
            module="connector",
            action="enterprise_connector_created",
            db=db,
            user_id=user.id,
            target_type="connector",
            target_id=row.id,
            detail=f"connector_type={connector_type}; credential_values=redacted",
        )
        return row

    def update_enterprise_credentials(
        self,
        *,
        db: Session,
        user: User,
        connector_id: int,
        credentials: dict[str, str],
    ) -> ExternalConnector:
        connector = db.query(ExternalConnector).filter(ExternalConnector.id == connector_id).first()
        if not connector or connector.connector_type not in self.ENTERPRISE_CONNECTOR_TYPES:
            raise ValueError("企业连接器不存在")
        if connector.organization_id and user.organization_id != connector.organization_id:
            raise ValueError("无权更新其他组织的企业连接器")
        config = self.parse_config(connector.config_json)
        existing_credentials = (
            mailbox_service.decrypt_credentials(connector.credential_ciphertext)
            if connector.credential_ciphertext
            else {}
        )
        merged_credentials = {**existing_credentials, **(credentials or {})}
        cleaned_credentials = self._validate_enterprise_config(
            connector_type=connector.connector_type,
            config=config,
            credentials=merged_credentials,
        )
        connector.credential_ciphertext = mailbox_service.encrypt_credentials(cleaned_credentials)
        db.commit()
        db.refresh(connector)
        oplog_service.log(
            module="connector",
            action="enterprise_connector_credentials_rotated",
            db=db,
            user_id=user.id,
            target_type="connector",
            target_id=connector.id,
            detail=f"connector_type={connector.connector_type}; credential_values=redacted",
        )
        return connector

    def serialize_connector(self, connector: ExternalConnector) -> dict:
        config = self.parse_config(connector.config_json)
        # Credentials are never stored in config_json for mailbox connectors. Defend against legacy input too.
        for key in ("password", "authorization_code", "access_token", "refresh_token", "client_secret"):
            config.pop(key, None)
        return {
            "id": connector.id,
            "user_id": connector.user_id,
            "organization_id": connector.organization_id,
            "department_id": connector.department_id,
            "connector_type": connector.connector_type,
            "name": connector.name,
            "status": connector.status,
            "config_json": json.dumps(config, ensure_ascii=False) if config else None,
            "last_sync_at": getattr(connector, "last_sync_at", None),
            "last_sync_status": getattr(connector, "last_sync_status", None),
            "last_imported_count": getattr(connector, "last_imported_count", 0),
            "last_skipped_count": getattr(connector, "last_skipped_count", 0),
            "total_imported_count": getattr(connector, "total_imported_count", 0),
            "total_skipped_count": getattr(connector, "total_skipped_count", 0),
            "created_at": connector.created_at,
            "updated_at": connector.updated_at,
        }

    def list_connectors(self, *, db: Session, user: User, include_all: bool = False) -> list[ExternalConnector]:
        rows = db.query(ExternalConnector).order_by(ExternalConnector.created_at.desc(), ExternalConnector.id.desc()).all()
        if not include_all or user.role != "admin":
            rows = [row for row in rows if self._can_access_connector(row, user=user)]
        if not rows:
            return rows

        connector_ids = [row.id for row in rows]
        job_query = db.query(ConnectorSyncJob).filter(ConnectorSyncJob.connector_id.in_(connector_ids))
        jobs = job_query.order_by(ConnectorSyncJob.updated_at.desc(), ConnectorSyncJob.id.desc()).all()

        by_connector: dict[int, list[ConnectorSyncJob]] = {}
        for job in jobs:
            by_connector.setdefault(job.connector_id, []).append(job)

        for row in rows:
            connector_jobs = by_connector.get(row.id, [])
            last_job = connector_jobs[0] if connector_jobs else None
            last_detail = self.parse_config(last_job.result_detail_json) if last_job else {}
            total_imported = 0
            total_skipped = 0
            for job in connector_jobs:
                detail = self.parse_config(job.result_detail_json)
                total_imported += int(detail.get("imported_count") or 0)
                total_skipped += int(detail.get("skipped_count") or 0)
            row.last_sync_at = last_job.updated_at if last_job else None
            row.last_sync_status = last_job.status if last_job else None
            row.last_imported_count = int(last_detail.get("imported_count") or 0)
            row.last_skipped_count = int(last_detail.get("skipped_count") or 0)
            row.total_imported_count = total_imported
            row.total_skipped_count = total_skipped
        return rows

    def get_connector(self, *, db: Session, connector_id: int, user: User) -> ExternalConnector | None:
        connector = db.query(ExternalConnector).filter(ExternalConnector.id == connector_id).first()
        if not connector:
            return None
        return connector if self._can_access_connector(connector, user=user) else None

    def rotate_credentials(self, *, db: Session, connector_id: int, user: User, username: str, password: str) -> ExternalConnector:
        connector = db.query(ExternalConnector).filter(
            ExternalConnector.id == connector_id,
            ExternalConnector.user_id == user.id,
        ).first()
        if not connector:
            raise ValueError("Connector not found")
        if connector.connector_type not in self.CREDENTIAL_ROTATION_TYPES:
            raise ValueError("Connector type does not support credential rotation")
        connector.credential_ciphertext = mailbox_service.encrypt_credentials({
            "username": username.strip(),
            "password": password,
        })
        db.commit()
        db.refresh(connector)
        oplog_service.log(
            module="connector",
            action="connector_credentials_rotated",
            db=db,
            user_id=user.id,
            target_type="connector",
            target_id=connector.id,
            detail=f"connector_type={connector.connector_type}; credential_values=redacted",
        )
        return connector

    def disable_connector(self, *, db: Session, connector_id: int, user: User) -> ExternalConnector:
        connector = db.query(ExternalConnector).filter(
            ExternalConnector.id == connector_id,
            ExternalConnector.user_id == user.id,
        ).first()
        if not connector:
            raise ValueError("Connector not found")
        connector.status = "disabled"
        connector.credential_ciphertext = None
        connector.sync_cursor_json = None
        db.commit()
        db.refresh(connector)
        oplog_service.log(
            module="connector", action="connector_disabled", db=db, user_id=user.id,
            target_type="connector", target_id=connector.id,
            detail=f"connector_type={connector.connector_type}; credentials_revoked=true",
        )
        return connector

    def create_sync_job(self, *, db: Session, connector_id: int, user: User, sync_mode: str = "manual") -> ConnectorSyncJob:
        connector = self.get_connector(db=db, connector_id=connector_id, user=user)
        if not connector:
            raise ValueError("Connector not found")
        job = ConnectorSyncJob(
            connector_id=connector.id,
            user_id=user.id,
            status="pending",
            sync_mode=sync_mode,
            result_summary="同步任务已提交，等待执行",
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        from app.tasks import connector_sync_task

        task = connector_sync_task.delay(job.id)
        log_async_task_event(
            user_id=user.id,
            module="async_task",
            action="connector_sync_submitted",
            target_type="connector_sync_job",
            target_id=job.id,
            detail=f"task_id={task.id}; connector_id={connector.id}; sync_mode={sync_mode}",
        )
        return job

    def list_sync_jobs(
        self,
        *,
        db: Session,
        user: User,
        connector_id: int | None = None,
        status: str | None = None,
    ) -> list[ConnectorSyncJob]:
        visible_connectors = {row.id for row in self.list_connectors(db=db, user=user, include_all=(user.role == "admin"))}
        query = db.query(ConnectorSyncJob).filter(ConnectorSyncJob.connector_id.in_(visible_connectors or {-1}))
        if connector_id is not None:
            query = query.filter(ConnectorSyncJob.connector_id == connector_id)
        if status:
            query = query.filter(ConnectorSyncJob.status == status)
        return query.order_by(ConnectorSyncJob.updated_at.desc(), ConnectorSyncJob.id.desc()).all()

    @staticmethod
    def parse_config(config_json: str | None) -> dict:
        if not config_json:
            return {}
        try:
            payload = json.loads(config_json)
            return payload if isinstance(payload, dict) else {}
        except (TypeError, ValueError):
            return {}

    def build_sync_documents(self, connector: ExternalConnector) -> list[dict]:
        config = self.parse_config(connector.config_json)
        default_kb_name = str(config.get("knowledge_base_name") or connector.name).strip() or connector.name
        seed_documents = config.get("seed_documents")
        if isinstance(seed_documents, list) and seed_documents:
            items = []
            for raw in seed_documents:
                if isinstance(raw, dict):
                    items.append(self._normalize_seed_document(raw, connector=connector, default_kb_name=default_kb_name))
            if items:
                return items

        source_dir = self._resolve_local_source_dir(config.get("path"))
        if source_dir:
            items = self._build_documents_from_local_dir(
                connector=connector,
                source_dir=source_dir,
                default_kb_name=default_kb_name,
                config=config,
            )
            if items:
                return items

        source_path = str(config.get("path") or config.get("space") or config.get("mailbox") or connector.name).strip()
        generated_content = "\n".join(
            [
                f"# {connector.name} 连接器同步",
                "",
                f"- 连接器类型：{connector.connector_type}",
                f"- 来源标识：{source_path}",
                "- 同步说明：当前为连接器接入骨架，可将该来源纳入知识库检索与追踪。",
            ]
        )
        return [
            {
                "title": f"{connector.name}-同步摘要.md",
                "content": generated_content,
                "file_type": "md",
                "knowledge_base_name": default_kb_name,
                "knowledge_base_category": connector.connector_type,
                "classification": connector.connector_type,
                "tags": ["connector", connector.connector_type],
                "permission_scope": self._default_permission_scope(connector),
                "sensitivity_level": "internal",
                "metadata": {
                    "connector_source_path": source_path,
                    "generated_by": "connector_sync",
                },
            }
        ]

    def build_sync_batch(self, connector: ExternalConnector) -> dict:
        if connector.connector_type in self.ENTERPRISE_CONNECTOR_TYPES:
            from app.services.enterprise_connector_service import enterprise_connector_service

            batch = enterprise_connector_service.build_batch(connector)
            return {
                "documents": batch.documents,
                "scanned_count": batch.scanned_count,
                "source": batch.source,
                "sync_cursor": batch.sync_cursor,
            }

        from app.tasks import _simulate_connector_sync

        scanned, _, source = _simulate_connector_sync(connector)
        return {
            "documents": self.build_sync_documents(connector),
            "scanned_count": scanned,
            "source": source,
            "sync_cursor": None,
        }


connector_service = ConnectorService()
