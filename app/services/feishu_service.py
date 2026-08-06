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
from datetime import datetime, timezone
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
    """事件分派入口（回调内调用）。消息/文件/卡片动作 ack 后转后台处理，快速回包。"""
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
    elif event_type == "card.action.trigger":
        card_event = extract_card_action(payload)
        if card_event:
            _spawn_card_action(card_event)
    return {"received": True, "event_type": event_type}


def _extract_operator_open_id(event: dict) -> Optional[str]:
    operator = event.get("operator") or {}
    operator_id = operator.get("operator_id") or operator.get("sender_id") or {}
    open_id = operator_id.get("open_id")
    if open_id:
        return open_id
    context = event.get("context") or {}
    return context.get("open_id")


def extract_card_action(payload: dict) -> Optional[dict]:
    """从 card.action.trigger 事件提取 {open_id, value}。value 需含 kind 路由键。"""
    event = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(event, dict):
        return None
    action = event.get("action") or {}
    if not isinstance(action, dict) or action.get("tag") != "button":
        return None
    value = action.get("value")
    if not isinstance(value, dict) or not value.get("kind"):
        return None
    open_id = _extract_operator_open_id(event)
    if not open_id:
        return None
    return {"open_id": open_id, "value": value}


def _spawn_card_action(event: dict) -> None:
    import asyncio

    asyncio.create_task(_background_card_action(event))


async def _background_card_action(event: dict) -> None:
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        await handle_card_action(event["open_id"], event["value"], db)
    except Exception as exc:  # noqa: BLE001 - 后台任务兜底
        logger.error("飞书 M3 卡片动作处理失败: %s", exc, exc_info=True)
    finally:
        db.close()


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
        await answer_text_message(event["open_id"], event["text"], db)
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
    user = _resolve_bound_user(open_id, db)
    if user is None:
        return await messenger.send_text(
            open_id, "你尚未绑定律智检账号。请先在律智检「设置-飞书绑定」完成扫码绑定后再发起咨询。"
        )
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
    user = _resolve_bound_user(open_id, db)
    if user is None:
        return await messenger.send_text(
            open_id, "你尚未绑定律智检账号。请先在律智检「设置-飞书绑定」完成扫码绑定后再发送文件。"
        )

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


# ── M3：文本路由（审核队列 / 文书生成 / 咨询）───────────────────────────────

REVIEW_COMMAND_WORDS = ("待审核", "审核队列")
DRAFT_TYPE_KEYWORDS = {
    "labor_arbitration_application": ("劳动仲裁", "仲裁申请书"),
    "private_lending_complaint": ("民间借贷", "借款起诉"),
    "consumer_complaint": ("消费投诉", "消费纠纷"),
    "supplementary_agreement": ("补充协议",),
}
DRAFT_TYPE_LABELS = {
    "labor_arbitration_application": "劳动人事争议仲裁申请书",
    "private_lending_complaint": "民间借贷纠纷起诉状",
    "consumer_complaint": "消费纠纷投诉书",
    "supplementary_agreement": "补充协议",
}
REVIEW_ITEM_TYPE_LABELS = {"consultation": "咨询", "contract_review": "审查", "draft": "文书"}
REVIEW_STATUS_LABELS = {
    "pending_review": "待审核", "needs_lawyer_review": "需律师复核", "needs_facts": "待补充事实",
    "lawyer_approved": "已通过", "returned_for_facts": "已退回补充", "archived": "已归档",
}


async def answer_text_message(open_id: str, text: str, db) -> dict:
    """M3 文本路由：待审核命令 → 审核队列；文书关键词 → 文书生成；否则走咨询卡片。"""
    if any(word in text for word in REVIEW_COMMAND_WORDS):
        return await answer_review_queue(open_id, db)
    if detect_draft_type(text):
        return await answer_draft_request(open_id, text, db)
    return await answer_consultation(open_id, text, db)


def detect_draft_type(message: str) -> Optional[str]:
    for document_type, keywords in DRAFT_TYPE_KEYWORDS.items():
        if any(keyword in message for keyword in keywords):
            return document_type
    return None


def parse_draft_fields(message: str, document_type: str) -> dict:
    """从"申请人:张三 被申请人:公司"式消息解析文书字段，忽略与模板无关的键。"""
    import re

    from app.services.legal_service import DRAFT_FIELDS

    fields: dict[str, str] = {}
    for line in re.split(r"[\s\n,，;；]+", message):
        if ":" not in line and "：" not in line:
            continue
        key, value = re.split(r"[:：]", line, 1)
        key = key.strip()
        value = value.strip()
        if key and value and key in DRAFT_FIELDS.get(document_type, []):
            fields[key] = value
    return fields


async def answer_review_queue(open_id: str, db) -> dict:
    """S4：拉取当前用户的待审核队列，逐项发送审核卡片（通过/退回/关闭按钮）。"""
    from app.services.legal_workspace_service import legal_workspace_read_module

    messenger = FeishuMessenger(get_settings().FEISHU_APP_ID, get_settings().FEISHU_APP_SECRET)
    user = _resolve_bound_user(open_id, db)
    if user is None:
        return await messenger.send_text(
            open_id, "你尚未绑定律智检账号。请先在律智检「设置-飞书绑定」完成扫码绑定后再使用审核队列。"
        )
    items = legal_workspace_read_module.review_queue(db, user)
    if not items:
        return await messenger.send_text(open_id, "当前没有待审核事项。")
    sent = 0
    for item in items[:3]:
        result = await messenger.send_card(open_id, build_review_item_card(item))
        if result.get("sent"):
            sent += 1
    return {"configured": True, "sent": bool(sent), "total": len(items), "cards_sent": sent}


def build_review_item_card(item: dict) -> dict:
    """单条待审核事项卡片：内容预览 + 通过/退回按钮，value 携带回写路由。"""
    kind = REVIEW_ITEM_TYPE_LABELS.get(item.get("target_type"), item.get("target_type"))
    title = item.get("question") or item.get("title") or item.get("document_type") or f"#{item.get('id')}"
    title_short = str(title)[:40]
    preview = item.get("advice") or item.get("summary") or item.get("question") or ""
    status_label = REVIEW_STATUS_LABELS.get(item.get("status"), item.get("status"))
    target_type = item["target_type"]
    target_id = item["id"]
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "red",
            "title": {"tag": "plain_text", "content": f"待审核 · {kind}"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**{title_short}**（{status_label}）"}},
            {"tag": "div", "text": {"tag": "lark_md", "content": str(preview)[:200]}},
            {"tag": "hr"},
            {
                "tag": "action",
                "actions": [
                    {"tag": "button", "text": {"tag": "plain_text", "content": "通过"}, "type": "primary",
                     "value": {"kind": "review", "action": "approve", "target_type": target_type,
                               "target_id": target_id, "title": title_short}},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "退回"}, "type": "danger",
                     "value": {"kind": "review", "action": "return", "target_type": target_type,
                               "target_id": target_id, "title": title_short,
                               "note": "飞书一键退回，请到 Web 端补充原因"}},
                    {"tag": "button", "text": {"tag": "plain_text", "content": "关闭"}, "type": "default",
                     "value": {"kind": "review", "action": "close", "target_type": target_type,
                               "target_id": target_id, "title": title_short}},
                ],
            },
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": "审核结果与 Web 审核队列实时一致（AI-2 回流）。"}],
            },
        ],
    }


async def handle_card_action(open_id: str, value: dict, db) -> dict:
    """M3 卡片动作分派：kind=review → 审核回写；kind=draft → 文书续写。"""
    kind = value.get("kind")
    if kind == "review":
        return await _handle_review_action(open_id, value, db)
    if kind == "draft":
        return await _handle_draft_action(open_id, value, db)
    return {"configured": True, "sent": False, "reason": f"unknown_kind:{kind}"}


async def _handle_review_action(open_id: str, value: dict, db) -> dict:
    from app.services.legal_workspace_service import legal_workspace_read_module

    messenger = FeishuMessenger(get_settings().FEISHU_APP_ID, get_settings().FEISHU_APP_SECRET)
    user = _resolve_bound_user(open_id, db)
    if user is None:
        return await messenger.send_text(open_id, "你尚未绑定律智检账号。")
    action = value.get("action")
    target_type = value.get("target_type")
    target_id = value.get("target_id")
    note = value.get("note") or ""
    label = value.get("title") or f"{target_type}#{target_id}"
    try:
        result = legal_workspace_read_module.apply_review_action(
            db, user, target_type=target_type, target_id=target_id, action=action, note=note,
        )
    except (LookupError, PermissionError, ValueError) as exc:
        return await messenger.send_text(open_id, f"审核动作失败：{exc}")
    status_label = REVIEW_STATUS_LABELS.get(result.get("status"), result.get("status"))
    return await messenger.send_text(open_id, f"审核完成：{label} → {status_label}")


def build_draft_card(row, document_type: str, missing_required: list) -> dict:
    """文书草稿卡片：内容预览 + 待补充字段提示。"""
    label = DRAFT_TYPE_LABELS.get(document_type, document_type)
    content = str(getattr(row, "content", "") or "")[:300]
    missing = list(missing_required or getattr(row, "missing_fields", None) or [])[:6]
    missing_block = "\n".join(f"· {m}" for m in missing) or "无（可补充更多细节）"
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": f"文书草稿 · {label}"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": content or "（空草稿）"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**待补充字段**\n{missing_block}"}},
            {
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": "可在 Web 端补全字段后导出 DOCX。继续回复字段内容即可生成新草稿。"}
                ],
            },
        ],
    }


async def answer_draft_request(open_id: str, message: str, db) -> dict:
    """S3：识别文书类型 → 解析字段 → 生成草稿卡片。"""
    from app.services.legal_workspace_service import legal_workspace_module

    messenger = FeishuMessenger(get_settings().FEISHU_APP_ID, get_settings().FEISHU_APP_SECRET)
    user = _resolve_bound_user(open_id, db)
    if user is None:
        return await messenger.send_text(
            open_id, "你尚未绑定律智检账号。请先在律智检「设置-飞书绑定」完成扫码绑定后再生成文书。"
        )
    document_type = detect_draft_type(message)
    if not document_type:
        return await messenger.send_text(open_id, "未识别文书类型。可尝试：文书 劳动仲裁申请书 / 民间借贷起诉状 / 补充协议。")
    fields = parse_draft_fields(message, document_type)
    try:
        row, missing_required = await legal_workspace_module.create_draft(
            db, user, document_type=document_type, fields=fields, case_id=None,
        )
    except ValueError as exc:
        if str(exc) == "QUOTA_EXCEEDED":
            return await messenger.send_text(open_id, "本月文书生成配额已用完，请升级订阅。")
        raise
    card = build_draft_card(row, document_type, missing_required)
    return await messenger.send_card(open_id, card)


async def _handle_draft_action(open_id: str, value: dict, db) -> dict:
    """文书续写（占位）：value 携带 document_type，后续按需扩展。"""
    messenger = FeishuMessenger(get_settings().FEISHU_APP_ID, get_settings().FEISHU_APP_SECRET)
    return await messenger.send_text(open_id, "文书续写请在 Web 端完成（本版本仅支持一键生成草稿）。")


def _resolve_bound_user(open_id: str, db):
    binding = db.query(FeishuBinding).filter(
        FeishuBinding.open_id == open_id, FeishuBinding.status == "active"
    ).first()
    if not binding:
        return None
    return db.query(User).filter(User.id == binding.user_id).first()


# ── M4：提醒类（激活引导 / 周报回访；期限提醒与闭环进度按 spec §5 待试点数据决策）────────

def user_activity_stats(db, user_id: int) -> dict:
    from app.models.legal import ContractReview, LegalConsultation, LegalDraft

    return {
        "consultation_count": db.query(LegalConsultation).filter(LegalConsultation.user_id == user_id).count(),
        "review_count": db.query(ContractReview).filter(ContractReview.user_id == user_id).count(),
        "draft_count": db.query(LegalDraft).filter(LegalDraft.user_id == user_id).count(),
    }


def build_activation_card(user_name: str) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "欢迎使用律智检"}},
        "elements": [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": (
                        f"你好，{user_name}。可以直接在这里：\n"
                        "· 发送法律问题 → 法条核对卡片\n"
                        "· 发送合同文件 → 合同风险初筛\n"
                        "· 回复「待审核」→ 审核队列"
                    ),
                },
            },
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "AI 结果仅供参考，不构成最终法律意见。"}]},
        ],
    }


def build_weekly_digest_card(stats: dict, pending_count: int) -> dict:
    body = (
        f"本周动态：咨询 {stats['consultation_count']} 次、审查 {stats['review_count']} 次、"
        f"文书 {stats['draft_count']} 份。待审核 {pending_count} 项。"
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {"template": "orange", "title": {"tag": "plain_text", "content": "律智检 · 周报回访"}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": body}},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "回复「待审核」可处理审核队列。"}]},
        ],
    }


def _reminder_due(open_id: str, kind: str) -> bool:
    """发送冷却（best-effort）：激活每 30 天一次；周报每周一次。Redis 不可用则放行。"""
    try:
        import redis as redis_lib

        client = redis_lib.from_url(get_settings().REDIS_URL, socket_connect_timeout=1)
        base_key = f"aibg:feishu:reminder:{kind}:{open_id}"
        if kind == "activation":
            return client.set(base_key, "1", nx=True, ex=30 * 86400) is not None
        week = datetime.now(timezone.utc).strftime("%G-W%V")
        return client.set(f"{base_key}:{week}", "1", nx=True, ex=8 * 86400) is not None
    except Exception:  # noqa: BLE001 - 冷却失败不阻断推送
        return True


async def dispatch_feishu_reminders(db) -> dict:
    """M4 beat 任务：向已绑定用户推送激活引导（无活动）或周报回访（有活动）。"""
    from app.services.legal_workspace_service import legal_workspace_read_module

    bindings = db.query(FeishuBinding).filter(FeishuBinding.status == "active").all()
    messenger = FeishuMessenger(get_settings().FEISHU_APP_ID, get_settings().FEISHU_APP_SECRET)
    sent_activation = 0
    sent_digest = 0
    try:
        for binding in bindings:
            user = db.query(User).filter(User.id == binding.user_id).first()
            if not user:
                continue
            stats = user_activity_stats(db, user.id)
            active = (stats["consultation_count"] + stats["review_count"] + stats["draft_count"]) > 0
            pending = len(legal_workspace_read_module.review_queue(db, user))
            if not active and _reminder_due(binding.open_id, "activation"):
                result = await messenger.send_card(binding.open_id, build_activation_card(user.username))
                if result.get("sent"):
                    sent_activation += 1
            elif active and _reminder_due(binding.open_id, "digest"):
                result = await messenger.send_card(binding.open_id, build_weekly_digest_card(stats, pending))
                if result.get("sent"):
                    sent_digest += 1
    finally:
        # 释放 httpx 连接池，避免每轮 beat 泄漏一个 AsyncClient
        await messenger.aclose()
    return {"bindings": len(bindings), "sent_activation": sent_activation, "sent_digest": sent_digest}


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
            params={"type": "file"},
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
            params={"receive_id_type": "open_id"},
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
