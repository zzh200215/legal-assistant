"""#87/飞书 M1：事件解密 + 单聊咨询卡片（法条核对）。

输入面：飞书事件回调（encrypt_key 模式 AES-256-CBC 解密、im.message.receive_v1 分派）。
处理面：绑定 open_id -> 用户，复用 legal_service 咨询链路（拒答红线 / PII 脱敏 / RAG / 兜底），
        组装飞书交互卡片（法条核对 + 风险提示）。
输出面：FeishuMessenger 出站（tenant_access_token + im/v1/messages）。
        未配置 FEISHU_APP_ID/SECRET 时出站返回 configured=False（占位禁用，同 Stripe 模式）。
"""

import base64
import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.core.config import get_settings
from app.models.feishu_binding import FeishuBinding
from app.models.user import User

logger = logging.getLogger(__name__)

FEISHU_OPEN_BASE = "https://open.feishu.cn/open-apis"
MESSAGE_EVENT_TYPE = "im.message.receive_v1"
FILE_SIZE_LIMIT_BYTES = 20 * 1024 * 1024  # 单文件 ≤20MB，与 web 端同限


def decrypt_payload(encrypt_key: str, encrypted_b64: str) -> dict:
    """飞书事件解密：key=md5(encrypt_key).hex()，iv=key[:16]，AES-256-CBC + PKCS7。

    明文为事件 JSON（{type/header/event}）。encrypt_key 未配置或密文非法时抛 ValueError。
    """
    if not encrypt_key:
        raise ValueError("FEISHU_EVENT_ENCRYPT_KEY not configured")
    key = hashlib.md5(encrypt_key.encode("utf-8")).hexdigest().encode("utf-8")
    iv = key[:16]
    raw = base64.b64decode(encrypted_b64)
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    dec = cipher.decryptor()
    data = dec.update(raw) + dec.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plaintext = unpadder.update(data) + unpadder.finalize()
    return json.loads(plaintext.decode("utf-8"))


def parse_event_body(raw_body: bytes, encrypt_key: str) -> dict:
    """解析回调原始 body：encrypt 字段存在则先解密。"""
    payload = json.loads(raw_body.decode("utf-8"))
    if isinstance(payload, dict) and payload.get("encrypt"):
        payload = decrypt_payload(encrypt_key, payload["encrypt"])
    return payload


def extract_message_event(payload: dict) -> Optional[dict]:
    """从 im.message.receive_v1 事件提取 {open_id, text, message_id, chat_id}。

    非文本消息 / 缺 open_id / 空文本返回 None。
    """
    event = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(event, dict):
        return None
    message = event.get("message") or {}
    if not isinstance(message, dict) or message.get("message_type") != "text":
        return None
    content_raw = message.get("content") or ""
    content: dict = {}
    if isinstance(content_raw, str):
        try:
            content = json.loads(content_raw)
        except json.JSONDecodeError:
            content = {}
    text = (content.get("text") or "").strip()
    if not text:
        return None
    sender_id = (event.get("sender") or {}).get("sender_id") or {}
    open_id = sender_id.get("open_id")
    if not open_id:
        return None
    return {
        "open_id": open_id,
        "text": text,
        "message_id": message.get("message_id"),
        "chat_id": message.get("chat_id"),
    }


def extract_file_event(payload: dict) -> Optional[dict]:
    """从 im.message.receive_v1 文件消息提取 {open_id, file_key, file_name, message_id}。"""
    event = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(event, dict):
        return None
    message = event.get("message") or {}
    if not isinstance(message, dict) or message.get("message_type") != "file":
        return None
    content_raw = message.get("content") or ""
    content: dict = {}
    if isinstance(content_raw, str):
        try:
            content = json.loads(content_raw)
        except json.JSONDecodeError:
            content = {}
    file_key = content.get("file_key")
    if not file_key:
        return None
    sender_id = (event.get("sender") or {}).get("sender_id") or {}
    open_id = sender_id.get("open_id")
    if not open_id:
        return None
    return {
        "open_id": open_id,
        "file_key": file_key,
        "file_name": content.get("file_name") or "contract",
        "message_id": message.get("message_id"),
        "chat_id": message.get("chat_id"),
    }


def handle_event(payload: dict) -> dict:
    """事件分派入口（回调内调用）。消息/文件事件 ack 后转后台处理，快速回包。"""
    event_type = (payload.get("header") or {}).get("event_type") or payload.get("type")
    if event_type == "url_verification":
        return {"type": "url_verification", "challenge": payload.get("challenge", "")}
    if event_type == MESSAGE_EVENT_TYPE:
        text_event = extract_message_event(payload)
        file_event = extract_file_event(payload)
        if text_event and text_event.get("text"):
            _spawn_reply(text_event)
        elif file_event and file_event.get("file_key"):
            _spawn_file_review(file_event)
    return {"received": True, "event_type": event_type}


def _spawn_reply(event: dict) -> None:
    import asyncio

    asyncio.create_task(_background_reply(event))


def _spawn_file_review(event: dict) -> None:
    import asyncio

    asyncio.create_task(_background_file_review(event))


async def _background_reply(event: dict) -> None:
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        await answer_consultation(event["open_id"], event["text"], db)
    except Exception as exc:  # noqa: BLE001 - 后台任务兜底
        logger.error("飞书 M1 咨询处理失败: %s", exc, exc_info=True)
    finally:
        db.close()


async def _background_file_review(event: dict) -> None:
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        await answer_file_review(event["open_id"], event["file_key"], event["file_name"], db)
    except Exception as exc:  # noqa: BLE001 - 后台任务兜底
        logger.error("飞书 M2 文件审查处理失败: %s", exc, exc_info=True)
    finally:
        db.close()


async def build_consultation_card(question: str, user_id: int, db) -> dict:
    """法条核对卡片：复用 legal_service 咨询链路 + RAG 法条检索。"""
    from app.models.legal import LegalSource
    from app.services.legal_service import consultation_payload, ensure_demo_sources
    from app.services.rag_service import rag_service

    ensure_demo_sources(db, user_id)
    sources = db.query(LegalSource).filter(
        LegalSource.user_id == user_id, LegalSource.status == "active"
    ).all()

    rag_refs: list[dict] = []
    try:
        chunks = await rag_service.search_async(question, top_k=3, user_id=user_id)
        rag_refs = [
            {
                "source_id": c.get("document_id"),
                "title": c.get("document_title") or "法条检索",
                "snippet": (c.get("chunk_text") or "")[:120],
            }
            for c in chunks[:3]
        ]
    except Exception:  # noqa: BLE001 - 检索失败不阻断主链路
        pass

    category, known, missing, refs, advice, risk, status = await consultation_payload(
        question, sources, user_id=user_id, db=db
    )

    ref_lines = []
    for ref in (refs or [])[:3]:
        title = ref.get("title") or ref.get("citation") or "法条"
        ref_lines.append(f"· {title}")
    ref_lines.extend(f"· {r['title']}" for r in rag_refs if r.get("title"))
    ref_block = "\n".join(ref_lines) if ref_lines else "暂无可用法条（系统仅提供一般性信息）"

    risk_label = "高" if risk == "high" else ("中" if risk == "medium" else "低")
    status_label = "需律师复核" if status == "needs_lawyer_review" else "可参考"
    known_block = "\n".join(f"· {k}" for k in (known or [])[:5]) or "未提取到明确事实"
    missing_block = "\n".join(f"· {m}" for m in (missing or [])[:5]) or "已足够，可补充更多细节继续追问"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red" if risk == "high" else "blue",
            "title": {"tag": "plain_text", "content": f"法条核对 · {category}"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**结论：**\n{advice}"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**风险：** {risk_label} ｜ **状态：** {status_label}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**已知事实**\n{known_block}"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**待补充信息**\n{missing_block}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**相关法条**\n{ref_block}"}},
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "可直接回复补充事实，我会继续分析。AI 结果仅供专业执业人员参考，不构成最终法律意见。"}
                ],
            },
        ],
    }


async def answer_consultation(open_id: str, question: str, db) -> dict:
    """按 open_id 解析绑定用户并回复咨询卡片；未绑定/未配置出站时返回状态说明。"""
    messenger = FeishuMessenger(get_settings().FEISHU_APP_ID, get_settings().FEISHU_APP_SECRET)
    binding = db.query(FeishuBinding).filter(
        FeishuBinding.open_id == open_id, FeishuBinding.status == "active"
    ).first()
    if not binding:
        return await messenger.send_text(
            open_id, "你尚未绑定律智检账号。请先在律智检「设置-飞书绑定」完成扫码绑定后再发起咨询。"
        )
    user = db.query(User).filter(User.id == binding.user_id).first()
    if not user:
        return {"configured": True, "sent": False, "reason": "user_not_found"}
    card = await build_consultation_card(question, user.id, db)
    return await messenger.send_card(open_id, card)


def build_contract_review_card(risks: list, summary: str, file_name: str) -> dict:
    """M2 合同初筛卡片：风险条款列表 + 深度审查入口（web 端落地）。"""
    high_count = 0
    risk_lines = []
    for item in (risks or [])[:5]:
        level = item.get("risk_level") or "medium"
        if level == "high":
            high_count += 1
        label = item.get("label") or item.get("clause_type") or "条款"
        snippet = ((item.get("source_location") or {}).get("snippet") or "")[:80]
        suggestion = item.get("suggestion") or "建议复核"
        prefix = "[高]" if level == "high" else ("[中]" if level == "medium" else "[低]")
        risk_lines.append(f"{prefix} **{label}**\n> {snippet}\n建议：{suggestion}")
    risk_block = "\n\n".join(risk_lines) if risk_lines else "未识别到明确风险条款"

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red" if high_count else "orange",
            "title": {"tag": "plain_text", "content": f"合同初筛 · {file_name[:20]}"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": summary}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**风险条款（前 {min(len(risks or []), 5)} 项）**\n{risk_block}"}},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "深度审查"},
                        "type": "primary",
                        "url": "/legal-workspace",
                    }
                ],
            },
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "AI 初筛仅供参考，最终意见以律师复核为准。"}],
            },
        ],
    }


def _extract_file_text(file_bytes: bytes, file_name: str) -> str:
    """把下载字节落临时文件后用 document_parsing.extract_file_text 提取文本。"""
    from app.services.document_parsing import extract_file_text as _extract

    ext = Path(file_name).suffix.lower() or ".txt"
    file_type = ext.lstrip(".")
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    try:
        return _extract(tmp_path, file_type) or ""
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def answer_file_review(open_id: str, file_key: str, file_name: str, db) -> dict:
    """M2 合同初筛：下载文件 → 提取文本 → 审查 → 风险卡片。出站未配置时占位提示。"""
    from app.services.legal_service import review_contract

    messenger = FeishuMessenger(get_settings().FEISHU_APP_ID, get_settings().FEISHU_APP_SECRET)
    binding = db.query(FeishuBinding).filter(
        FeishuBinding.open_id == open_id, FeishuBinding.status == "active"
    ).first()
    if not binding:
        return await messenger.send_text(
            open_id, "你尚未绑定律智检账号。请先在律智检「设置-飞书绑定」完成扫码绑定后再发送文件。"
        )
    user = db.query(User).filter(User.id == binding.user_id).first()
    if not user:
        return {"configured": True, "sent": False, "reason": "user_not_found"}

    file_bytes = await messenger.download_file(file_key)
    if file_bytes is None:
        return await messenger.send_text(open_id, "飞书出站尚未开通，合同初筛暂不可用（配置企业自建应用凭据后生效）。")
    if len(file_bytes) > FILE_SIZE_LIMIT_BYTES:
        return await messenger.send_text(open_id, "文件超过 20MB 限制，请压缩后重试。")

    text = _extract_file_text(file_bytes, file_name)
    if not text.strip():
        return await messenger.send_text(open_id, "未能从文件中提取到文本内容（暂不支持扫描件 OCR 初筛）。")

    risks, summary = await review_contract(text, user_id=user.id)
    card = build_contract_review_card(risks, summary, file_name)
    return await messenger.send_card(open_id, card)


class FeishuMessenger:
    """飞书出站客户端：tenant_access_token 缓存 + 发送文本/交互卡片。

    未配置 FEISHU_APP_ID / FEISHU_APP_SECRET 时 send_* 返回 {"configured": False}，
    不发起网络请求（占位禁用）。
    """

    def __init__(self, app_id: str = "", app_secret: str = "", base_url: str = FEISHU_OPEN_BASE):
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = base_url
        self._token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._http: Optional[httpx.AsyncClient] = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10.0)
        return self._http

    async def tenant_access_token(self) -> Optional[str]:
        if not self.app_id or not self.app_secret:
            return None
        if self._token and time.monotonic() < self._token_expires_at - 60:
            return self._token
        client = await self._client()
        resp = await client.post(
            f"{self.base_url}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.warning("飞书 tenant_access_token 获取失败: %s", data.get("msg"))
            return None
        self._token = data.get("tenant_access_token")
        self._token_expires_at = time.monotonic() + float(data.get("expire", 7200))
        return self._token

    async def send_card(self, receive_id: str, card: dict) -> dict:
        return await self._send_message(receive_id, "interactive", card)

    async def send_text(self, receive_id: str, text: str) -> dict:
        return await self._send_message(receive_id, "text", {"text": text})

    async def download_file(self, file_key: str) -> Optional[bytes]:
        """下载飞书消息文件（im/v1/files/{file_key}）。未配置凭据或失败返回 None。"""
        token = await self.tenant_access_token()
        if not token:
            return None
        client = await self._client()
        resp = await client.get(
            f"{self.base_url}/im/v1/files/{file_key}",
            headers={"Authorization": f"Bearer {token}"},
        )
        content_type = resp.headers.get("content-type", "")
        if content_type and "application/json" in content_type:
            data = resp.json()
            if data.get("code") != 0:
                logger.warning("飞书文件下载失败: %s", data.get("msg"))
            return None
        return resp.content

    async def _send_message(self, receive_id: str, msg_type: str, content: dict) -> dict:
        token = await self.tenant_access_token()
        if not token:
            return {"configured": False}
        client = await self._client()
        resp = await client.post(
            f"{self.base_url}/im/v1/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False),
            },
        )
        data = resp.json()
        if data.get("code") != 0:
            return {
                "configured": True,
                "sent": False,
                "code": data.get("code"),
                "message": data.get("msg"),
            }
        return {
            "configured": True,
            "sent": True,
            "message_id": (data.get("data") or {}).get("message_id"),
        }

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
