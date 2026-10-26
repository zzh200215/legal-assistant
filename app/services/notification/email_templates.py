"""内置 HTML 邮件模板（审核 / 咨询类通知邮件的版式层）。

为什么是代码而不是数据库行：
- ``notification_templates`` 表是运营覆写层（可版本化、可停用），但它没有 HTML 版式
  字段；把 v1 版式放在代码里，保证「组织一配好 SMTP 就能收到像样的邮件」，不依赖
  运维先跑一次种子脚本。
- 数据库模板命中时优先使用（纯文本，运营可改文案），未命中时用这里的内置 HTML 版式，
  两者都没有才回退到事件标题 / 正文（``notification_service._render_email_content``）。

模板契约：
- 结构化字段（subject / lead / action_label / footnote）代替裸 HTML 片段，正文由
  ``_layout`` 统一拼装；邮件客户端只支持 table + inline style，这里不用 flex/grid/外链。
- 参数一律 HTML 转义；``{{body}}`` 按空行切段，不信任任何外部 HTML。
- ``action_url`` 缺失（未配置 ``NOTIFICATION_EMAIL_ACTION_BASE_URL``）时不渲染按钮，
  纯文本版也不出现空链接。
"""

from __future__ import annotations

import json
import re
from html import escape, unescape

from app.core.config import get_settings

# 与 notification_template_service 同款占位符语法，便于两类模板共用参数。
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

# 内置模板：key 与业务侧 create_multi_channel_notification(template_key=...) 一一对应。
BUILTIN_EMAIL_TEMPLATES: dict[str, dict[str, str]] = {
    "review_sla_due_soon": {
        "subject": "【律智检】{{title}}",
        "lead": "以下审核任务即将到期，请在截止时间前完成审核。",
        "action_label": "去审核",
        "footnote": "若未在截止前处理，系统会在逾期后再发一次提醒。",
    },
    "review_sla_overdue": {
        "subject": "【律智检】{{title}}",
        "lead": "以下审核任务已超过截止时间，请尽快处理。",
        "action_label": "立即处理",
        "footnote": "逾期记录会在审核队列中以「已逾期」标记。",
    },
    "review_consultation_uncited": {
        "subject": "【律智检】{{title}}",
        "lead": "有新的咨询进入审核队列，但该咨询没有关联法源引用。",
        "action_label": "去审核",
        "footnote": "无依据不影响审核流转，建议要求提交人补充依据后再通过。",
    },
    "review_consultation_weak": {
        "subject": "【律智检】{{title}}",
        "lead": "有新的咨询进入审核队列，法源依据偏弱。",
        "action_label": "去审核",
        "footnote": "请留意引用是否覆盖了回答中的关键结论。",
    },
    "consultation_returned_supplement": {
        "subject": "【律智检】{{title}}",
        "lead": "你的咨询被退回，需要补充法源依据后重新提交。",
        "action_label": "去补充依据",
        "footnote": "补充后系统会自动对比引用变化，并通知审核人复核。",
    },
    "consultation_returned": {
        "subject": "【律智检】{{title}}",
        "lead": "你的咨询被退回，请查看审核意见后重新提交。",
        "action_label": "查看审核意见",
        "footnote": "如需补充事实或依据，直接在咨询页面编辑后重新提交即可。",
    },
    "consultation_supplemented": {
        "subject": "【律智检】{{title}}",
        "lead": "有咨询补充了法源依据，等待重新审核。",
        "action_label": "去复核",
        "footnote": "对比结果已写入咨询详情，可查看补充前后的引用变化。",
    },
}

# 深链：按 reference_type 落到审核台；其他类型回到通知中心。
_ACTION_PATHS = {
    "consultation": "/legal-workspace?view=review",
    "contract_review": "/legal-workspace?view=review",
    "draft": "/legal-workspace?view=review",
}
_DEFAULT_ACTION_PATH = "/notifications"

_BRAND = "律智检"
_TAGLINE = "法律文书与合同审查智能体平台"
_FOOTER = "此邮件由系统自动发送，请勿直接回复。"

# 运营可覆写的版式字段（类型 layout 的模板只改这些文案，不动 HTML 结构）。
LAYOUT_OVERRIDE_FIELDS = ("subject", "lead", "action_label", "footnote")
LAYOUT_FIELD_LIMITS = {"subject": 120, "lead": 300, "action_label": 16, "footnote": 300}


class LayoutOverrideError(ValueError):
    """layout 覆写不合法（未知字段 / 超长 / 非 JSON）。"""


def parse_layout_overrides(text: str | None) -> dict[str, str]:
    """解析 layout 覆写 JSON：只认 4 个文案字段，空值和缺失都表示「用内置文案」。"""
    if not text or not str(text).strip():
        return {}
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LayoutOverrideError("布局覆写必须是 JSON 对象") from exc
    if not isinstance(payload, dict):
        raise LayoutOverrideError("布局覆写必须是 JSON 对象")
    overrides: dict[str, str] = {}
    for key, value in payload.items():
        if key not in LAYOUT_OVERRIDE_FIELDS:
            raise LayoutOverrideError(f"不支持的覆写字段: {key}")
        if value is None:
            continue
        if not isinstance(value, str):
            raise LayoutOverrideError(f"覆写字段 {key} 必须是字符串")
        value = value.strip()
        if not value:
            continue
        if len(value) > LAYOUT_FIELD_LIMITS[key]:
            raise LayoutOverrideError(f"覆写字段 {key} 超过 {LAYOUT_FIELD_LIMITS[key]} 字")
        overrides[key] = value
    return overrides


def describe_builtin_templates() -> list[dict]:
    """内置模板清单（管理页展示用），不含渲染逻辑。"""
    return [
        {
            "template_key": key,
            "subject": spec["subject"],
            "lead": spec["lead"],
            "action_label": spec["action_label"],
            "footnote": spec["footnote"],
        }
        for key, spec in BUILTIN_EMAIL_TEMPLATES.items()
    ]


def html_to_text(html: str) -> str:
    """HTML → 纯文本回退（自定义 html 模板没有手写纯文本版时用）。

    只做保守剥离：去掉 script/style，块级标签转换行，其余标签删除，实体还原。
    """
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|tr|h[1-6]|li)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def action_url(reference_type: str | None) -> str | None:
    """按组织配置的前端地址拼深链；未配置返回 None（不渲染按钮）。"""
    base = (get_settings().NOTIFICATION_EMAIL_ACTION_BASE_URL or "").strip().rstrip("/")
    if not base:
        return None
    path = _ACTION_PATHS.get(str(reference_type or ""), _DEFAULT_ACTION_PATH)
    return f"{base}{path}"


def _fill(template: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda match: values.get(match.group(1), ""), template)


def _paragraphs_html(text: str) -> str:
    """纯文本按空行/换行切段；每段转义后套统一段落样式。"""
    parts = [part.strip() for part in re.split(r"\n+", text or "") if part.strip()]
    style = "margin:0 0 12px;font-size:15px;line-height:24px;color:#1f2937;"
    if not parts:
        return ""
    return "".join(f'<p style="{style}">{escape(part)}</p>' for part in parts)


def _layout(*, title: str, lead: str, body: str, action_label: str | None,
            href: str | None, footnote: str) -> str:
    """统一的邮件外框：600px 居中卡片 + 品牌头 + 正文 + CTA + 页脚。"""
    title_html = escape(title)
    lead_html = escape(lead)
    footnote_html = escape(footnote)
    cta = ""
    if href and action_label:
        cta = (
            '<table role="presentation" cellpadding="0" cellspacing="0" '
            'style="margin:0 0 20px;"><tr><td '
            'style="border-radius:6px;background:#4F46E5;">'
            f'<a href="{escape(href, quote=True)}" target="_blank" '
            'style="display:inline-block;padding:11px 22px;font-size:14px;font-weight:600;'
            'color:#ffffff;text-decoration:none;">'
            f"{escape(action_label)}</a></td></tr></table>"
        )
        link_text = (
            '<p style="margin:0 0 12px;font-size:13px;line-height:20px;color:#6b7280;">'
            "按钮无法点击时，复制链接到浏览器打开：<br>"
            f'<span style="color:#4F46E5;word-break:break-all;">{escape(href)}</span></p>'
        )
    else:
        link_text = ""
    return (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title_html}</title></head>"
        '<body style="margin:0;padding:0;background:#f4f5f7;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f4f5f7;padding:24px 12px;"><tr><td align="center">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="width:600px;max-width:100%;background:#ffffff;border-radius:10px;'
        'border:1px solid #e5e7eb;overflow:hidden;">'
        # 品牌头
        '<tr><td style="padding:18px 28px;border-bottom:1px solid #eef0f3;">'
        '<span style="font-size:15px;font-weight:700;color:#111827;">'
        f"{_BRAND}</span>"
        '<span style="font-size:12px;color:#9ca3af;">&nbsp;·&nbsp;'
        f"{_TAGLINE}</span></td></tr>"
        # 正文
        '<tr><td style="padding:26px 28px 8px;">'
        '<span style="display:inline-block;padding:2px 8px;border-radius:4px;'
        'background:#eef2ff;color:#4F46E5;font-size:12px;font-weight:600;">待处理</span>'
        f'<h1 style="margin:12px 0 10px;font-size:19px;line-height:28px;color:#111827;">'
        f"{title_html}</h1>"
        f'<p style="margin:0 0 14px;font-size:15px;line-height:24px;color:#374151;">'
        f"{lead_html}</p>"
        f"{_paragraphs_html(body)}"
        f"{cta}{link_text}"
        f'<p style="margin:0 0 18px;font-size:13px;line-height:20px;color:#6b7280;">'
        f"{footnote_html}</p>"
        "</td></tr>"
        # 页脚
        '<tr><td style="padding:14px 28px 20px;border-top:1px solid #eef0f3;">'
        f'<p style="margin:0;font-size:12px;line-height:18px;color:#9ca3af;">'
        f"{_FOOTER}</p>"
        "</td></tr>"
        "</table></td></tr></table></body></html>"
    )


def render_builtin_email(template_key: str | None, *, params: dict | None = None,
                         layout_overrides: dict | None = None) -> dict | None:
    """渲染内置模板；key 未注册返回 None（由调用方回退到事件文案）。

    ``layout_overrides`` 只替换文案字段（subject/lead/action_label/footnote），
    HTML 结构与样式保持不变；空值表示沿用内置文案。
    返回 ``{"subject", "text", "html", "template_key"}``，subject/html 已转义。
    """
    spec = BUILTIN_EMAIL_TEMPLATES.get(str(template_key or ""))
    if spec is None:
        return None
    overrides = layout_overrides or {}

    raw = {key: str(value or "") for key, value in (params or {}).items()}
    title = raw.get("title", "")
    body = raw.get("body", "")
    href = raw.get("action_url") or None
    subject_template = overrides.get("subject") or spec["subject"]
    lead = overrides.get("lead") or spec["lead"]
    footnote = overrides.get("footnote") or spec["footnote"]
    action_label = (overrides.get("action_label") or spec["action_label"]) if href else ""

    subject = _fill(subject_template, {**raw, "title": re.sub(r"\s+", " ", title).strip()})

    text_lines = [lead, "", title.strip()]
    if body.strip():
        text_lines += ["", body.strip()]
    if href:
        text_lines += ["", f"{action_label}：{href}"]
    text_lines += ["", footnote, "", "—", f"{_BRAND} · {_TAGLINE}", _FOOTER]
    text = "\n".join(text_lines)

    html = _layout(title=title, lead=lead, body=body, action_label=action_label,
                   href=href, footnote=footnote)
    return {"subject": subject, "text": text, "html": html, "template_key": str(template_key)}
