"""纯函数工具集：RAG 检索/生成流程中无状态、无副作用的文本与元数据辅助函数。

这些函数原为 ``RAGService`` 上的 ``@staticmethod`` / ``@classmethod``，
为拆分上帝类而抽取到本模块，仅依赖标准库与全局配置。
"""

import hashlib
import json
import re

from app.core.config import get_settings

settings = get_settings()

# 参与“是否需重建”判定的元数据签名字段：content_hash 覆盖内容，embedding_model 单独比对
SIGNATURE_FIELDS = (
    "user_id",
    "knowledge_base_id",
    "document_status",
    "chunk_index",
    "page_number",
    "section_title",
    "section_path",
    "segment_type",
    "table_like",
    "visual_tags",
    "ocr_quality",
    "visual_evidence",
    "visual_region",
)


def metadata_hash(metadata: dict) -> str:
    signature = {key: metadata.get(key) for key in SIGNATURE_FIELDS}
    serialized = json.dumps(
        signature, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def compact_metadata(metadata: dict) -> dict:
    return {key: value for key, value in metadata.items() if value is not None}


def excerpt(text: str, limit: int = 240) -> str:
    return text[:limit] + "..." if len(text) > limit else text


def build_citation_locator(metadata: dict) -> str:
    parts = []
    if metadata.get("document_id") is not None:
        parts.append(f"doc:{metadata['document_id']}")
    if metadata.get("page_number") is not None:
        parts.append(f"page:{metadata['page_number']}")
    if metadata.get("section_title"):
        parts.append(f"section:{metadata['section_title']}")
    if metadata.get("segment_type"):
        parts.append(f"type:{metadata['segment_type']}")
    if metadata.get("visual_tags"):
        parts.append(f"tags:{metadata['visual_tags']}")
    if metadata.get("visual_evidence"):
        parts.append(f"evidence:{excerpt(str(metadata['visual_evidence']), 80)}")
    if metadata.get("visual_region"):
        parts.append(f"region:{metadata['visual_region']}")
    if metadata.get("chunk_index") is not None:
        parts.append(f"chunk:{metadata['chunk_index']}")
    return " | ".join(parts)


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：中文约 1.5 字符/token，ASCII 约 3.5 字符/token。"""
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    other_chars = len(text) - ascii_chars
    return max(1, int(ascii_chars / 3.5 + other_chars / 1.5))


def build_visual_context_summary(metadata: dict) -> str:
    visual_tags = str(metadata.get("visual_tags") or "").split()
    if not visual_tags and metadata.get("ocr_quality") is None:
        return ""
    labels = {
        "ocr": "OCR识别",
        "visual": "视觉线索",
        "scanned_page": "扫描页",
        "page_visual": "页面视觉",
        "image_visual": "图片视觉",
        "table_visual": "表格视觉",
        "table_dense": "表格密集",
        "seal_present": "公章",
        "stamp_present": "印章",
        "signature_present": "签字",
        "signed_page": "签署页",
        "attachment_like": "附件页",
        "image_like": "图像内容",
        "document_copy": "扫描件/复印件",
    }
    visual_label_text = "、".join(labels.get(tag, tag.replace("_", " ")) for tag in visual_tags[:6])
    parts = []
    if visual_label_text:
        parts.append(f"视觉线索: {visual_label_text}")
    if metadata.get("ocr_quality") is not None:
        parts.append(f"OCR质量: {round(float(metadata['ocr_quality']), 2):.2f}")
    if metadata.get("visual_region"):
        region_labels = {
            "top": "页面上部",
            "middle": "页面中部",
            "bottom": "页面下部",
        }
        parts.append(f"区域: {region_labels.get(str(metadata['visual_region']), metadata['visual_region'])}")
    return f"[视觉摘要] {'；'.join(parts)}" if parts else ""


def looks_like_refusal(answer: str) -> bool:
    markers = ("无法确认", "不能确认", "未提及", "未找到", "信息不足", "没有相关信息")
    return any(marker in answer for marker in markers)


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", (query or "").strip())


def should_use_llm_rewrite(query: str) -> bool:
    """长/歧义查询（多意图、含并列/标点）才触发 LLM 改写，控制成本。"""
    compact = re.sub(r"\s+", "", query or "")
    if len(compact) >= settings.RAG_QUERY_REWRITE_LLM_MIN_CHARS:
        return True
    return bool(re.search(r"[。；;，,]|和|与|并且|或者|或者|还是", compact))


def parse_llm_json(raw: str) -> dict:
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        return {}
    return json.loads(raw[start:end + 1])


def metadata_matches_where(metadata: dict, where: dict | None) -> bool:
    if not where:
        return True
    if "$and" in where:
        return all(metadata_matches_where(metadata, clause) for clause in where["$and"])
    if "$or" in where:
        return any(metadata_matches_where(metadata, clause) for clause in where["$or"])
    for field, value in where.items():
        if isinstance(value, dict):
            op, operand = next(iter(value.items()))
            if op == "$in":
                if metadata.get(field) not in list(operand):
                    return False
                continue
            if op == "$ne":
                if metadata.get(field) == operand:
                    return False
                continue
        if metadata.get(field) != value:
            return False
    return True
