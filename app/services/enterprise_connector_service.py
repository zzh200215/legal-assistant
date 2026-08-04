from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx

from app.models.connector import ExternalConnector
from app.services.mailbox_service import mailbox_service


SUPPORTED_REMOTE_FILE_TYPES = {".md", ".txt", ".csv", ".pdf", ".docx", ".xlsx"}
GRAPH_HOST = "graph.microsoft.com"


@dataclass
class EnterpriseSyncBatch:
    documents: list[dict]
    scanned_count: int
    source: str
    sync_cursor: dict | None = None


class EnterpriseConnectorService:
    """Remote data-source adapters with strict, explicit connector configuration."""

    GRAPH_TYPES = {"ms_graph_onedrive", "ms_graph_sharepoint"}
    REST_TYPES = {"erp_rest", "crm_rest"}

    @staticmethod
    def _config(connector: ExternalConnector) -> dict:
        try:
            value = json.loads(connector.config_json or "{}")
        except (TypeError, ValueError):
            value = {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _cursor(connector: ExternalConnector) -> dict:
        try:
            value = json.loads(connector.sync_cursor_json or "{}")
        except (TypeError, ValueError):
            value = {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _credentials(connector: ExternalConnector) -> dict[str, str]:
        if not connector.credential_ciphertext:
            return {}
        return mailbox_service.decrypt_credentials(connector.credential_ciphertext)

    @staticmethod
    def _parse_expiry(value: object) -> datetime | None:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

    def _graph_access_token(self, *, connector: ExternalConnector, config: dict, credentials: dict[str, str]) -> str:
        access_token = str(credentials.get("access_token") or "").strip()
        expires_at = self._parse_expiry(credentials.get("expires_at"))
        if access_token and (expires_at is None or expires_at > datetime.now(timezone.utc) + timedelta(seconds=90)):
            return access_token

        refresh_token = str(credentials.get("refresh_token") or "").strip()
        client_secret = str(credentials.get("client_secret") or "").strip()
        tenant_id = str(config.get("tenant_id") or "").strip()
        client_id = str(config.get("client_id") or "").strip()
        if not refresh_token or not client_secret or not tenant_id or not client_id:
            if access_token:
                return access_token
            raise ValueError("Microsoft Graph 令牌不可用，请由管理员重新授权")
        response = httpx.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "scope": "offline_access Files.Read.All Sites.Read.All",
            },
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("Microsoft 刷新令牌未返回 access_token")
        credentials.update({
            "access_token": access_token,
            "refresh_token": str(payload.get("refresh_token") or refresh_token).strip(),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=int(payload.get("expires_in") or 3600))).isoformat(),
        })
        connector.credential_ciphertext = mailbox_service.encrypt_credentials(credentials)
        return access_token

    @staticmethod
    def _positive_int(value: Any, default: int, maximum: int = 100) -> int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = default
        return max(1, min(value, maximum))

    @staticmethod
    def _safe_remote_url(value: str, *, expected_host: str | None = None) -> str:
        parsed = urlparse(str(value or "").strip())
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("远端连接器仅允许使用 HTTPS 地址，且地址中不能包含账号或密码")
        if expected_host and parsed.hostname.lower() != expected_host:
            raise ValueError("远端分页地址不属于允许的服务域名")
        return parsed.geturl()

    @staticmethod
    def _dotted_value(payload: Any, path: str | None, default: Any = None) -> Any:
        if not path:
            return payload
        current = payload
        for key in path.split("."):
            if isinstance(current, dict):
                current = current.get(key)
            else:
                return default
        return current if current is not None else default

    @staticmethod
    def _render_record_content(record: dict, fields: list[str]) -> str:
        lines: list[str] = []
        for field in fields:
            value = EnterpriseConnectorService._dotted_value(record, field)
            if value in (None, "", [], {}):
                continue
            if isinstance(value, (dict, list)):
                rendered = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            else:
                rendered = str(value)
            lines.append(f"- {field}: {rendered[:4000]}")
        return "\n".join(lines) or json.dumps(record, ensure_ascii=False, indent=2)[:12000]

    def build_batch(self, connector: ExternalConnector) -> EnterpriseSyncBatch:
        if connector.connector_type in self.GRAPH_TYPES:
            return self._build_graph_batch(connector)
        if connector.connector_type in self.REST_TYPES:
            return self._build_rest_batch(connector)
        raise ValueError("不支持的企业连接器类型")

    def _graph_delta_url(self, connector: ExternalConnector, config: dict, cursor: dict) -> str:
        previous = str(cursor.get("delta_link") or "").strip()
        if previous:
            return self._safe_remote_url(previous, expected_host=GRAPH_HOST)
        drive_id = str(config.get("drive_id") or "").strip()
        if connector.connector_type == "ms_graph_sharepoint":
            site_id = str(config.get("site_id") or "").strip()
            if not site_id and not drive_id:
                raise ValueError("SharePoint 连接器需要 site_id 或 drive_id")
            resource = f"/drives/{quote(drive_id, safe='')}/root/delta" if drive_id else f"/sites/{quote(site_id, safe='')}/drive/root/delta"
        else:
            resource = f"/drives/{quote(drive_id, safe='')}/root/delta" if drive_id else "/me/drive/root/delta"
        return f"https://{GRAPH_HOST}/v1.0{resource}"

    def _build_graph_batch(self, connector: ExternalConnector) -> EnterpriseSyncBatch:
        config = self._config(connector)
        credentials = self._credentials(connector)
        access_token = self._graph_access_token(connector=connector, config=config, credentials=credentials)

        max_files = self._positive_int(config.get("max_files"), 50)
        max_pages = self._positive_int(config.get("max_pages"), 10, maximum=50)
        cursor = self._cursor(connector)
        url = self._graph_delta_url(connector, config, cursor)
        headers = {"Authorization": f"Bearer {access_token}"}
        records: list[dict] = []
        scanned = 0
        delta_link = None

        with httpx.Client(timeout=30, follow_redirects=False) as client:
            for _ in range(max_pages):
                response = client.get(self._safe_remote_url(url, expected_host=GRAPH_HOST), headers=headers)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Microsoft Graph 返回的数据格式不正确")
                values = payload.get("value") if isinstance(payload.get("value"), list) else []
                scanned += len(values)
                for item in values:
                    if not isinstance(item, dict) or item.get("deleted") or not isinstance(item.get("file"), dict):
                        continue
                    name = str(item.get("name") or "").strip()
                    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    if not name or suffix not in SUPPORTED_REMOTE_FILE_TYPES:
                        continue
                    records.append(item)
                    if len(records) >= max_files:
                        break
                delta_link = payload.get("@odata.deltaLink") or delta_link
                next_link = payload.get("@odata.nextLink")
                if len(records) >= max_files or not next_link:
                    break
                url = str(next_link)

            documents: list[dict] = []
            for item in records:
                name = str(item["name"])
                suffix = name.rsplit(".", 1)[-1].lower()
                download_url = str(item.get("@microsoft.graph.downloadUrl") or "").strip()
                if download_url:
                    binary = client.get(self._safe_remote_url(download_url)).content
                else:
                    item_id = quote(str(item.get("id") or ""), safe="")
                    if not item_id:
                        continue
                    binary_response = client.get(
                        f"https://{GRAPH_HOST}/v1.0/drives/{quote(str(item.get('parentReference', {}).get('driveId') or config.get('drive_id') or ''), safe='')}/items/{item_id}/content",
                        headers=headers,
                    )
                    binary_response.raise_for_status()
                    binary = binary_response.content
                if not binary:
                    continue
                remote_url = str(item.get("webUrl") or "")
                documents.append(
                    {
                        "title": name,
                        "file_type": suffix,
                        "file_bytes": binary,
                        "knowledge_base_name": str(config.get("knowledge_base_name") or connector.name),
                        "knowledge_base_category": connector.connector_type,
                        "classification": connector.connector_type,
                        "tags": ["connector", connector.connector_type, "microsoft_graph"],
                        "permission_scope": str(config.get("permission_scope") or "org"),
                        "sensitivity_level": str(config.get("sensitivity_level") or "internal"),
                        "metadata": {
                            "remote_id": item.get("id"),
                            "remote_url": remote_url,
                            "remote_last_modified_at": item.get("lastModifiedDateTime"),
                            "remote_e_tag": item.get("eTag"),
                            "source_provider": "microsoft_graph",
                        },
                    }
                )

        next_cursor = {"delta_link": delta_link} if delta_link else None
        source = "Microsoft Graph / SharePoint" if connector.connector_type == "ms_graph_sharepoint" else "Microsoft Graph / OneDrive"
        return EnterpriseSyncBatch(documents=documents, scanned_count=scanned, source=source, sync_cursor=next_cursor)

    def _build_rest_batch(self, connector: ExternalConnector) -> EnterpriseSyncBatch:
        config = self._config(connector)
        credentials = self._credentials(connector)
        endpoint = self._safe_remote_url(str(config.get("endpoint") or ""))
        resource_path = str(config.get("resource_path") or "").strip()
        if resource_path and not resource_path.startswith("/"):
            raise ValueError("resource_path 必须以 / 开头")
        url = urljoin(endpoint.rstrip("/") + "/", resource_path.lstrip("/")) if resource_path else endpoint
        headers: dict[str, str] = {"Accept": "application/json"}
        bearer_token = str(credentials.get("bearer_token") or "").strip()
        api_key = str(credentials.get("api_key") or "").strip()
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        elif api_key:
            header_name = str(config.get("api_key_header") or "X-API-Key").strip()
            if not header_name.replace("-", "").isalnum():
                raise ValueError("api_key_header 不合法")
            headers[header_name] = api_key
        else:
            raise ValueError("ERP/CRM 连接器缺少 bearer_token 或 api_key")

        raw_params = config.get("query_params") if isinstance(config.get("query_params"), dict) else {}
        params = {str(key): str(value) for key, value in raw_params.items() if isinstance(value, (str, int, float, bool))}
        cursor = self._cursor(connector)
        cursor_param = str(config.get("cursor_param") or "").strip()
        if cursor_param and cursor.get("value"):
            params[cursor_param] = str(cursor["value"])
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            response = client.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()

        items_path = str(config.get("items_path") or "data").strip()
        records = self._dotted_value(payload, items_path, default=payload if isinstance(payload, list) else [])
        if not isinstance(records, list):
            raise ValueError("ERP/CRM 返回中未找到列表数据，请检查 items_path")
        max_records = self._positive_int(config.get("max_records"), 100)
        title_field = str(config.get("title_field") or "name").strip()
        id_field = str(config.get("id_field") or "id").strip()
        cursor_field = str(config.get("cursor_field") or "updated_at").strip()
        content_fields = config.get("content_fields") if isinstance(config.get("content_fields"), list) else []
        fields = [str(field).strip() for field in content_fields if str(field).strip()]
        entity_name = str(config.get("entity_name") or ("CRM 记录" if connector.connector_type == "crm_rest" else "ERP 记录")).strip()
        documents: list[dict] = []
        next_cursor = cursor.get("value")
        for record in records[:max_records]:
            if not isinstance(record, dict):
                continue
            record_id = self._dotted_value(record, id_field, "")
            title_value = self._dotted_value(record, title_field, "")
            title = f"{entity_name}-{title_value or record_id or len(documents) + 1}"
            record_cursor = self._dotted_value(record, cursor_field)
            if record_cursor is not None and (next_cursor is None or str(record_cursor) > str(next_cursor)):
                next_cursor = record_cursor
            documents.append(
                {
                    "title": title[:256],
                    "content": f"# {title}\n\n{self._render_record_content(record, fields)}",
                    "file_type": "md",
                    "knowledge_base_name": str(config.get("knowledge_base_name") or connector.name),
                    "knowledge_base_category": connector.connector_type,
                    "classification": connector.connector_type,
                    "tags": ["connector", connector.connector_type, "rest_api"],
                    "permission_scope": str(config.get("permission_scope") or "org"),
                    "sensitivity_level": str(config.get("sensitivity_level") or "internal"),
                    "metadata": {
                        "remote_id": record_id,
                        "entity_name": entity_name,
                        "cursor_field": cursor_field,
                        "cursor_value": record_cursor,
                        "source_provider": "rest_api",
                    },
                }
            )
        source = "CRM REST API" if connector.connector_type == "crm_rest" else "ERP REST API"
        return EnterpriseSyncBatch(
            documents=documents,
            scanned_count=len(records),
            source=source,
            sync_cursor={"value": next_cursor} if next_cursor is not None else None,
        )


enterprise_connector_service = EnterpriseConnectorService()
