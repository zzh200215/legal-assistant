import asyncio
import hashlib
import json
import re
import uuid
from pathlib import Path
from statistics import median

import pdfplumber
from docx import Document as DocxDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.document import Document, DocumentChunk
from app.models.user import User
from app.services.analysis_service import analysis_service
from app.services.document_governance_service import document_governance_service
from app.services.document_job_service import document_job_service
from app.services.document_indexing import build_embedding_id as _build_embedding_id
from app.services.document_indexing import prepare_chunks_for_indexing
from app.services.document_qa_service import document_qa_service
from app.services.llm_service import llm_service
from app.services.rag_service import rag_service
from app.services.agentic_rag_service import agentic_rag_service
from app.services.storage_service import storage_service

UPLOAD_DIR = storage_service.ensure_dir(storage_service.base_dir())
settings = get_settings()

DOCUMENT_STATUS_PARSED = "parsed"
DOCUMENT_STATUS_INDEXED = "indexed"
IMAGE_FILE_TYPES = {"png", ".png", "jpg", ".jpg", "jpeg", ".jpeg", "bmp", ".bmp", "webp", ".webp"}
VISION_SUPPORTED_FILE_TYPES = IMAGE_FILE_TYPES | {"pdf", ".pdf"}


class DocumentParsePermanentError(ValueError):
    pass


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_conflict_label(value: str | None) -> str:
    """Keep fact matching deliberately conservative to avoid false conflict alerts."""
    text = (value or "").strip().lower()
    text = re.sub(r"[\s\W_]+", "", text)
    for token in ("日期", "时间", "期限", "截止", "金额", "数额", "费用", "负责人", "责任人", "负责", "相关", "事项"):
        text = text.replace(token, "")
    return text


def _facts_describe_same_subject(left: str | None, right: str | None) -> bool:
    left_label = _normalize_conflict_label(left)
    right_label = _normalize_conflict_label(right)
    if not left_label or not right_label:
        return False
    return left_label in right_label or right_label in left_label


def _build_section_path(parent_path: list[str] | None, title: str | None) -> list[str]:
    path = list(parent_path or [])
    normalized_title = (title or "").strip()
    if normalized_title:
        path.append(normalized_title)
    return path


def _unique_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        cleaned = (value or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _humanize_visual_tag(tag: str) -> str:
    labels = {
        "ocr": "OCR识别",
        "visual": "视觉线索",
        "scanned_page": "扫描页",
        "page_visual": "页面视觉",
        "image_visual": "图片视觉",
        "table_visual": "表格视觉",
        "table_dense": "表格密集",
        "seal_present": "检测到公章",
        "stamp_present": "检测到印章",
        "signature_present": "检测到签字",
        "signed_page": "签署页",
        "attachment_like": "附件页",
        "image_like": "图像内容",
        "document_copy": "扫描件/复印件",
    }
    return labels.get(tag, tag.replace("_", " "))


def _detect_segment_type(text: str, section_title: str | None = None) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return "empty"
    if re.match(r"^(\d+[\.\)]|[-*•])\s+", normalized):
        return "list"
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if lines and all(("|" in line) or ("\t" in line) for line in lines[: min(3, len(lines))]):
        return "table"
    if len(lines) <= 2 and len(normalized) <= 40:
        return "heading"
    return "paragraph"


def _build_segment(
    *,
    text: str,
    page_number: int | None,
    section_title: str | None,
    section_path: list[str] | None = None,
    segment_type: str | None = None,
    visual_tags: list[str] | None = None,
    ocr_quality: float | None = None,
) -> dict:
    normalized_title = (section_title or "").strip() or "正文"
    normalized_text = _normalize_text(text)
    resolved_path = section_path or _build_section_path([], normalized_title)
    resolved_type = segment_type or _detect_segment_type(normalized_text, normalized_title)
    resolved_visual_tags = _derive_visual_tags(
        normalized_text,
        section_title=normalized_title,
        section_path=resolved_path,
        segment_type=resolved_type,
        existing_tags=visual_tags,
    )
    resolved_ocr_quality = ocr_quality
    if resolved_ocr_quality is None and resolved_type in {"page_ocr", "image_ocr"}:
        resolved_ocr_quality = round(_estimate_readable_ratio(normalized_text), 4)
    return {
        "text": normalized_text,
        "page_number": page_number,
        "section_title": normalized_title,
        "section_path": resolved_path,
        "segment_type": resolved_type,
        "visual_tags": resolved_visual_tags,
        "ocr_quality": resolved_ocr_quality,
        "visual_evidence": _extract_visual_evidence(normalized_text, visual_tags=resolved_visual_tags),
        "visual_region": None,
    }


def _load_ocr_dependencies():
    try:
        from PIL import Image, ImageEnhance, ImageFilter, ImageOps  # type: ignore
        import pytesseract  # type: ignore

        return Image, ImageEnhance, ImageFilter, ImageOps, pytesseract
    except Exception:
        return None, None, None, None, None


def _safe_float(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _open_ocr_target(*, file_path: str | None = None, image=None):
    image_module, _, _, _, _ = _load_ocr_dependencies()
    if image_module is None:
        raise DocumentParsePermanentError("当前环境未启用 OCR，无法解析图片或扫描 PDF。")
    if image is not None:
        return image, None
    if not file_path:
        raise DocumentParsePermanentError("OCR 缺少输入文件。")
    opened_image = image_module.open(file_path)
    return opened_image, opened_image


def _collect_ocr_words(data: dict) -> list[dict]:
    texts = data.get("text") or []
    if not isinstance(texts, list):
        return []

    lefts = data.get("left") or []
    tops = data.get("top") or []
    widths = data.get("width") or []
    heights = data.get("height") or []
    confs = data.get("conf") or []
    words = []
    for index, raw_text in enumerate(texts):
        text = _normalize_text(str(raw_text or "").replace("\n", " "))
        if not text:
            continue
        conf = _safe_float(confs[index] if index < len(confs) else None, default=None)
        if conf is not None and conf < 0:
            continue
        left = _safe_int(lefts[index] if index < len(lefts) else 0)
        top = _safe_int(tops[index] if index < len(tops) else 0)
        width = max(1, _safe_int(widths[index] if index < len(widths) else 1))
        height = max(1, _safe_int(heights[index] if index < len(heights) else 1))
        words.append(
            {
                "text": text,
                "left": left,
                "top": top,
                "right": left + width,
                "width": width,
                "height": height,
                "conf": conf,
            }
        )
    words.sort(key=lambda item: (item["top"], item["left"]))
    return words


def _estimate_readable_ratio(text: str) -> float:
    normalized = _normalize_text(text or "")
    if not normalized:
        return 0.0
    meaningful = re.findall(r"[A-Za-z0-9\u4e00-\u9fff]", normalized)
    total = re.findall(r"\S", normalized)
    if not total:
        return 0.0
    return len(meaningful) / len(total)


def _estimate_table_density(text: str) -> float:
    normalized = _normalize_text(text or "")
    if not normalized:
        return 0.0
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return 0.0
    table_lines = sum(1 for line in lines if ("|" in line) or ("\t" in line))
    return table_lines / max(len(lines), 1)


def _derive_visual_tags(
    text: str,
    *,
    section_title: str | None = None,
    section_path: list[str] | None = None,
    segment_type: str | None = None,
    existing_tags: list[str] | None = None,
) -> list[str]:
    merged = " ".join(
        [
            text or "",
            section_title or "",
            " ".join(section_path or []),
            segment_type or "",
        ]
    )
    tags = list(existing_tags or [])
    if segment_type in {"page_ocr", "image_ocr"}:
        tags.extend(["ocr", "visual"])
    if segment_type == "page_ocr":
        tags.extend(["scanned_page", "page_visual"])
    if segment_type == "image_ocr":
        tags.extend(["image_visual"])
    if segment_type == "table" or _estimate_table_density(text) >= 0.35:
        tags.extend(["table_visual", "table_dense"])
    if re.search(r"(盖章|签章|公章|印章|骑缝章)", merged):
        tags.extend(["seal_present", "stamp_present"])
    if re.search(r"(签字|签署|签名|签约人|授权代表)", merged):
        tags.extend(["signature_present", "signed_page"])
    if re.search(r"(附件|附录|附件清单|附页)", merged):
        tags.extend(["attachment_like"])
    if re.search(r"(图片|截图|照片|扫描|影印)", merged):
        tags.extend(["image_like"])
    if re.search(r"(原件|复印件|扫描件)", merged):
        tags.extend(["document_copy"])
    return _unique_preserve_order(tags)


def _build_visual_summary(
    *,
    visual_tags: list[str] | None = None,
    segment_type: str | None = None,
    page_number: int | None = None,
    ocr_quality: float | None = None,
    section_title: str | None = None,
) -> str:
    tags = _unique_preserve_order(list(visual_tags or []))
    parts: list[str] = []
    if segment_type in {"page_ocr", "image_ocr"}:
        parts.append("来源: OCR")
    if page_number is not None:
        parts.append(f"页码: 第 {page_number} 页")
    if section_title:
        parts.append(f"标题: {section_title}")
    if tags:
        parts.append("视觉标签: " + "、".join(_humanize_visual_tag(tag) for tag in tags[:6]))
    if ocr_quality is not None:
        parts.append(f"OCR质量: {round(float(ocr_quality), 2):.2f}")
    if not parts:
        return ""
    return "[视觉摘要] " + "；".join(parts)


def _extract_visual_evidence(
    text: str,
    *,
    visual_tags: list[str] | None = None,
    max_lines: int = 2,
) -> str:
    normalized = _normalize_text(text or "")
    if not normalized:
        return ""
    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return ""

    patterns = []
    tags = set(visual_tags or [])
    if tags.intersection({"seal_present", "stamp_present"}):
        patterns.append(r"(盖章|签章|公章|印章|骑缝章)")
    if tags.intersection({"signature_present", "signed_page"}):
        patterns.append(r"(签字|签署|签名|签约人|授权代表)")
    if "attachment_like" in tags:
        patterns.append(r"(附件|附录|附件清单|附页)")
    if "table_visual" in tags:
        patterns.append(r"(\||\t|表格|表头|金额|日期)")

    if not patterns:
        return ""

    matched_lines = []
    for line in lines:
        if any(re.search(pattern, line) for pattern in patterns):
            matched_lines.append(line)
        if len(matched_lines) >= max_lines:
            break
    return "\n".join(matched_lines)


def _resolve_vertical_region(top: int, min_top: int, max_bottom: int) -> str:
    span = max(1, max_bottom - min_top)
    ratio = (top - min_top) / span
    if ratio < 0.33:
        return "top"
    if ratio < 0.66:
        return "middle"
    return "bottom"


def _question_has_visual_hint(question: str) -> bool:
    return "补充视觉分析线索：" in (question or "")


def _extract_visual_evidence_with_positions(
    data: dict,
    *,
    visual_tags: list[str] | None = None,
    max_lines: int = 2,
) -> tuple[str, str | None]:
    words = _collect_ocr_words(data)
    rows = _group_ocr_words_into_rows(words)
    if not rows:
        return "", None

    patterns = []
    tags = set(visual_tags or [])
    if tags.intersection({"seal_present", "stamp_present"}):
        patterns.append(r"(盖章|签章|公章|印章|骑缝章)")
    if tags.intersection({"signature_present", "signed_page"}):
        patterns.append(r"(签字|签署|签名|签约人|授权代表)")
    if "attachment_like" in tags:
        patterns.append(r"(附件|附录|附件清单|附页)")
    if "table_visual" in tags:
        patterns.append(r"(\||\t|表格|表头|金额|日期)")
    if not patterns:
        return "", None

    min_top = min(word["top"] for word in words)
    max_bottom = max(word["top"] + word["height"] for word in words)
    matched_lines = []
    matched_regions = []
    for row in rows:
        line = " ".join(item["text"] for item in row).strip()
        if not line:
            continue
        if any(re.search(pattern, line) for pattern in patterns):
            matched_lines.append(line)
            matched_regions.append(_resolve_vertical_region(row[0]["top"], min_top, max_bottom))
        if len(matched_lines) >= max_lines:
            break
    if not matched_lines:
        return "", None
    region = matched_regions[0] if matched_regions else None
    return "\n".join(matched_lines), region


def _looks_like_low_quality_text(text: str) -> bool:
    normalized = _normalize_text(text or "")
    if not normalized:
        return True
    if len(normalized) < max(1, settings.OCR_MIN_TEXT_LENGTH):
        return True
    return _estimate_readable_ratio(normalized) < max(0.0, min(1.0, settings.OCR_MIN_READABLE_RATIO))


def _build_ocr_image_variants(image) -> list:
    if image is None:
        return []
    if not settings.OCR_ENABLE_IMAGE_PREPROCESS:
        return [image]

    _, image_enhance_module, image_filter_module, image_ops_module, _ = _load_ocr_dependencies()
    if image_enhance_module is None or image_filter_module is None or image_ops_module is None:
        return [image]

    variants = [image]
    try:
        grayscale = image.convert("L")
        autocontrast = image_ops_module.autocontrast(grayscale)
        variants.append(autocontrast)

        sharpened = autocontrast.filter(image_filter_module.SHARPEN)
        variants.append(sharpened)

        contrast_boost = image_enhance_module.Contrast(autocontrast).enhance(1.8)
        variants.append(contrast_boost)

        binary = contrast_boost.point(lambda value: 255 if value > 160 else 0)
        variants.append(binary)
    except Exception:
        return [image]

    unique_variants = []
    seen = set()
    for variant in variants:
        marker = (id(variant), getattr(variant, "mode", None), getattr(variant, "size", None))
        if marker in seen:
            continue
        seen.add(marker)
        unique_variants.append(variant)
    return unique_variants


def _group_ocr_words_into_rows(words: list[dict]) -> list[list[dict]]:
    if not words:
        return []
    row_threshold = max(8, int(median([item["height"] for item in words]) * 0.7))
    rows: list[list[dict]] = []
    current_row: list[dict] = []
    current_top = words[0]["top"]
    for word in words:
        if not current_row or abs(word["top"] - current_top) <= row_threshold:
            current_row.append(word)
            current_top = int(sum(item["top"] for item in current_row) / len(current_row))
            continue
        rows.append(sorted(current_row, key=lambda item: item["left"]))
        current_row = [word]
        current_top = word["top"]
    if current_row:
        rows.append(sorted(current_row, key=lambda item: item["left"]))
    return rows


def _split_ocr_row_into_cells(row: list[dict]) -> list[dict]:
    if not row:
        return []
    gap_threshold = max(14, int(median([item["width"] for item in row]) * 0.8))
    cells = []
    current_words = [row[0]]
    current_left = row[0]["left"]
    current_right = row[0]["right"]
    for word in row[1:]:
        gap = word["left"] - current_right
        if gap > gap_threshold:
            cells.append(
                {
                    "left": current_left,
                    "right": current_right,
                    "text": " ".join(item["text"] for item in current_words),
                }
            )
            current_words = [word]
            current_left = word["left"]
            current_right = word["right"]
        else:
            current_words.append(word)
            current_right = max(current_right, word["right"])
    cells.append(
        {
            "left": current_left,
            "right": current_right,
            "text": " ".join(item["text"] for item in current_words),
        }
    )
    return cells


def _cluster_column_positions(positions: list[int], tolerance: int) -> list[int]:
    if not positions:
        return []
    sorted_positions = sorted(positions)
    clusters: list[list[int]] = [[sorted_positions[0]]]
    for position in sorted_positions[1:]:
        if abs(position - int(sum(clusters[-1]) / len(clusters[-1]))) <= tolerance:
            clusters[-1].append(position)
        else:
            clusters.append([position])
    return [int(sum(cluster) / len(cluster)) for cluster in clusters]


def _render_table_from_ocr_data(data: dict) -> str | None:
    words = _collect_ocr_words(data)
    rows = _group_ocr_words_into_rows(words)
    if len(rows) < 2:
        return None

    row_cells = [_split_ocr_row_into_cells(row) for row in rows]
    candidate_rows = [cells for cells in row_cells if len(cells) >= 2]
    if len(candidate_rows) < 2:
        return None

    cell_widths = [max(1, cell["right"] - cell["left"]) for cells in candidate_rows for cell in cells]
    tolerance = max(24, int(median(cell_widths) * 0.6))
    anchors = _cluster_column_positions([cell["left"] for cells in candidate_rows for cell in cells], tolerance)
    if len(anchors) < 2:
        return None

    rendered_rows = []
    populated_rows = 0
    for cells in row_cells:
        values = [""] * len(anchors)
        populated = 0
        for cell in cells:
            anchor_index = min(range(len(anchors)), key=lambda idx: abs(cell["left"] - anchors[idx]))
            if values[anchor_index]:
                values[anchor_index] = f"{values[anchor_index]} {cell['text']}".strip()
            else:
                values[anchor_index] = cell["text"]
                populated += 1
        if populated >= 2:
            populated_rows += 1
        rendered_rows.append(values)

    if populated_rows < 2:
        return None

    non_empty_columns = [
        index for index in range(len(anchors))
        if any((row[index] or "").strip() for row in rendered_rows)
    ]
    if len(non_empty_columns) < 2:
        return None

    lines = []
    for row in rendered_rows:
        filtered = [(row[index] or "").strip() for index in non_empty_columns]
        if sum(1 for item in filtered if item) < 2:
            continue
        lines.append(f"| {' | '.join(filtered)} |")
    return _normalize_text("\n".join(lines)) if len(lines) >= 2 else None


def _ocr_image_to_table_text(*, file_path: str | None = None, image=None) -> str | None:
    image_module, _, _, _, pytesseract_module = _load_ocr_dependencies()
    if image_module is None or pytesseract_module is None:
        raise DocumentParsePermanentError("当前环境未启用 OCR，无法解析图片或扫描 PDF。")

    target_image, opened_image = _open_ocr_target(file_path=file_path, image=image)
    try:
        output_type = getattr(getattr(pytesseract_module, "Output", None), "DICT", None)
        for variant in _build_ocr_image_variants(target_image):
            for lang in ("chi_sim+eng", "eng", None):
                try:
                    kwargs = {"lang": lang} if lang else {}
                    if output_type is not None:
                        kwargs["output_type"] = output_type
                    data = pytesseract_module.image_to_data(variant, **kwargs)
                    if isinstance(data, dict):
                        table_text = _render_table_from_ocr_data(data)
                        if table_text:
                            return table_text
                except Exception:
                    continue
        return None
    finally:
        if opened_image is not None:
            opened_image.close()


def _ocr_image_to_text(*, file_path: str | None = None, image=None) -> str:
    image_module, _, _, _, pytesseract_module = _load_ocr_dependencies()
    if image_module is None or pytesseract_module is None:
        raise DocumentParsePermanentError("当前环境未启用 OCR，无法解析图片或扫描 PDF。")

    def run_with_lang(target, lang: str | None) -> str:
        kwargs = {"lang": lang} if lang else {}
        return pytesseract_module.image_to_string(target, **kwargs)

    tried_errors: list[str] = []
    best_candidate = ""
    target_image = image
    opened_image = None
    try:
        if target_image is None:
            target_image, opened_image = _open_ocr_target(file_path=file_path, image=image)

        for variant in _build_ocr_image_variants(target_image):
            for lang in ("chi_sim+eng", "eng", None):
                try:
                    raw_text = run_with_lang(variant, lang)
                    normalized = _normalize_text(raw_text)
                    if not normalized:
                        continue
                    if not _looks_like_low_quality_text(normalized):
                        return normalized
                    if len(normalized) > len(best_candidate):
                        best_candidate = normalized
                except Exception as exc:
                    tried_errors.append(str(exc))
        if best_candidate:
            return best_candidate
        raise DocumentParsePermanentError(
            "OCR 已启用，但未识别到可用文本。"
            if not tried_errors
            else f"OCR 识别失败：{tried_errors[-1]}"
        )
    finally:
        if opened_image is not None:
            opened_image.close()


def _ocr_image_to_text_with_layout(*, file_path: str | None = None, image=None, visual_tags: list[str] | None = None) -> tuple[str, str | None]:
    text = _ocr_image_to_text(file_path=file_path, image=image)
    _, _, _, _, pytesseract_module = _load_ocr_dependencies()
    if pytesseract_module is None:
        return text, None
    target_image = image
    opened_image = None
    try:
        if target_image is None:
            target_image, opened_image = _open_ocr_target(file_path=file_path, image=image)
        output_type = getattr(getattr(pytesseract_module, "Output", None), "DICT", None)
        if output_type is None:
            return text, None
        for variant in _build_ocr_image_variants(target_image):
            for lang in ("chi_sim+eng", "eng", None):
                try:
                    kwargs = {"lang": lang} if lang else {}
                    kwargs["output_type"] = output_type
                    data = pytesseract_module.image_to_data(variant, **kwargs)
                    if isinstance(data, dict):
                        evidence, region = _extract_visual_evidence_with_positions(
                            data,
                            visual_tags=visual_tags,
                        )
                        if evidence:
                            return text, region
                except Exception:
                    continue
        return text, None
    finally:
        if opened_image is not None:
            opened_image.close()


def _ocr_pdf_page(page) -> tuple[str, str, str | None]:
    try:
        page_image = page.to_image(resolution=max(120, settings.OCR_PDF_RENDER_DPI))
        pil_image = getattr(page_image, "original", None)
        if pil_image is None:
            raise DocumentParsePermanentError("当前 PDF 渲染能力不可用，无法对扫描页执行 OCR。")
        table_text = _ocr_image_to_table_text(image=pil_image)
        if table_text:
            return table_text, "table", None
        visual_tags = _derive_visual_tags("", segment_type="page_ocr")
        text, visual_region = _ocr_image_to_text_with_layout(image=pil_image, visual_tags=visual_tags)
        return text, "page_ocr", visual_region
    except DocumentParsePermanentError:
        raise
    except Exception as exc:
        raise DocumentParsePermanentError(f"扫描 PDF 页 OCR 失败：{exc}") from exc


def _extract_segments_from_pdf(file_path: str) -> list[dict]:
    segments = []
    ocr_attempted = False
    with pdfplumber.open(file_path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            page_text = _normalize_text(page.extract_text() or "")
            segment_type = "page"
            visual_region = None
            if _looks_like_low_quality_text(page_text):
                ocr_attempted = True
                try:
                    page_text, segment_type, visual_region = _ocr_pdf_page(page)
                except DocumentParsePermanentError:
                    if _looks_like_low_quality_text(page_text):
                        page_text = ""
            if not page_text:
                continue
            segments.append(
                _build_segment(
                    text=page_text,
                    page_number=index,
                    section_title=f"第 {index} 页",
                    section_path=[f"第 {index} 页"],
                    segment_type=segment_type,
                    visual_tags=_derive_visual_tags(page_text, section_title=f"第 {index} 页", section_path=[f"第 {index} 页"], segment_type=segment_type),
                )
            )
            if visual_region and segments[-1].get("segment_type") in {"page_ocr", "image_ocr"}:
                segments[-1]["visual_region"] = visual_region
    if not segments and ocr_attempted:
        raise DocumentParsePermanentError("PDF 未提取到可读文本，且当前环境无法完成扫描页 OCR。")
    return segments


def _extract_segments_from_docx(file_path: str) -> list[dict]:
    doc = DocxDocument(file_path)
    segments = []
    current_title = "正文"
    current_path = [current_title]
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        joined = _normalize_text("\n".join(buffer))
        if joined:
            segments.append(
                _build_segment(
                    text=joined,
                    page_number=None,
                    section_title=current_title,
                    section_path=current_path,
                )
            )
        buffer = []

    for paragraph in doc.paragraphs:
        text = (paragraph.text or "").strip()
        if not text:
            continue
        style_name = getattr(paragraph.style, "name", "") or ""
        if style_name.lower().startswith("heading"):
            flush()
            current_title = text
            current_path = _build_section_path([], current_title)
            continue
        buffer.append(text)
    flush()
    return segments


def _split_markdown_sections(md_text: str) -> list[dict]:
    lines = md_text.splitlines()
    sections: list[dict] = []
    current_title = "正文"
    current_path = [current_title]
    buffer: list[str] = []
    heading_stack: list[tuple[int, str]] = []

    def flush() -> None:
        nonlocal buffer
        joined = _normalize_text("\n".join(buffer))
        if joined:
            sections.append(
                _build_segment(
                    text=joined,
                    page_number=None,
                    section_title=current_title,
                    section_path=current_path,
                )
            )
        buffer = []

    for line in lines:
        stripped = line.strip()
        heading_match = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if heading_match:
            flush()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            heading_stack = [item for item in heading_stack if item[0] < level]
            heading_stack.append((level, title))
            current_title = title
            current_path = [item[1] for item in heading_stack]
            continue
        buffer.append(line)
    flush()
    return sections


def _extract_segments_from_markdown(file_path: str) -> list[dict]:
    with open(file_path, "r", encoding="utf-8") as f:
        md_text = f.read()
    return _split_markdown_sections(md_text)


def _extract_segments_from_excel(file_path: str) -> list[dict]:
    wb = load_workbook(file_path, data_only=True)
    segments = []
    for index, sheet in enumerate(wb.worksheets, start=1):
        rows = []
        for row in sheet.iter_rows(values_only=True):
            row_text = " ".join([str(cell) for cell in row if cell is not None])
            if row_text.strip():
                rows.append(row_text)
        joined = _normalize_text("\n".join(rows))
        if joined:
            segments.append(
                _build_segment(
                    text=joined,
                    page_number=index,
                    section_title=sheet.title,
                    section_path=[sheet.title],
                    segment_type="table",
                )
            )
    return segments


def _extract_segments_from_txt(file_path: str) -> list[dict]:
    with open(file_path, "r", encoding="utf-8") as f:
        text = _normalize_text(f.read())
    if not text:
        return []
    return [_build_segment(text=text, page_number=None, section_title="正文", section_path=["正文"])]


def _extract_segments_from_image(file_path: str) -> list[dict]:
    table_text = _ocr_image_to_table_text(file_path=file_path)
    if table_text:
        title = Path(file_path).name
        return [
            _build_segment(
                text=table_text,
                page_number=1,
                section_title=title,
                section_path=[title],
                segment_type="table",
            )
        ]

    title = Path(file_path).name
    visual_tags = _derive_visual_tags("", section_title=title, section_path=[title], segment_type="image_ocr")
    text, visual_region = _ocr_image_to_text_with_layout(
        file_path=file_path,
        visual_tags=visual_tags,
    )
    if not text:
        raise DocumentParsePermanentError("图片未识别到可用文本。")
    segment = _build_segment(
            text=text,
            page_number=1,
            section_title=title,
            section_path=[title],
            segment_type="image_ocr",
        )
    if visual_region:
        segment["visual_region"] = visual_region
    return [segment]


def _extract_segments(file_path: str, file_type: str) -> list[dict]:
    ext = file_type.lower()
    if ext in ("pdf", ".pdf"):
        return _extract_segments_from_pdf(file_path)
    if ext in ("docx", ".docx"):
        return _extract_segments_from_docx(file_path)
    if ext in ("xlsx", ".xlsx", "xls", ".xls"):
        return _extract_segments_from_excel(file_path)
    if ext in ("md", ".md", "markdown"):
        return _extract_segments_from_markdown(file_path)
    if ext in ("txt", ".txt"):
        return _extract_segments_from_txt(file_path)
    if ext in IMAGE_FILE_TYPES:
        return _extract_segments_from_image(file_path)
    raise DocumentParsePermanentError(f"Unsupported file type: {ext}")


def _extract_text(file_path: str, file_type: str) -> str:
    segments = _extract_segments(file_path, file_type)
    return _normalize_text("\n\n".join(segment["text"] for segment in segments))


def extract_file_text(file_path: str, file_type: str) -> str:
    return _extract_text(file_path, file_type)


def _file_to_data_url(file_path: str, file_type: str) -> str:
    mime_map = {
        "png": "image/png",
        ".png": "image/png",
        "jpg": "image/jpeg",
        ".jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        ".jpeg": "image/jpeg",
        "bmp": "image/bmp",
        ".bmp": "image/bmp",
        "webp": "image/webp",
        ".webp": "image/webp",
        "pdf": "application/pdf",
        ".pdf": "application/pdf",
    }
    normalized_type = (file_type or "").lower()
    mime_type = mime_map.get(normalized_type)
    if not mime_type:
        raise DocumentParsePermanentError("当前文件类型不支持视觉模型分析。")
    return storage_service.to_data_url(file_path, mime_type)


def _supports_visual_analysis(file_type: str) -> bool:
    return (file_type or "").lower() in VISION_SUPPORTED_FILE_TYPES


def _split_text(text_or_segments: str | list[dict], chunk_size: int = 800, chunk_overlap: int = 100) -> list[dict]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", ".", " ", ""],
    )
    if isinstance(text_or_segments, str):
        segments = [_build_segment(text=text_or_segments, page_number=None, section_title="正文", section_path=["正文"])]
    else:
        segments = text_or_segments

    chunks = []
    chunk_index = 0
    for segment in segments:
        segment_text = _normalize_text(segment.get("text", ""))
        if not segment_text:
            continue
        for content in splitter.split_text(segment_text):
            normalized = _normalize_text(content)
            if not normalized:
                continue
            chunks.append(
                {
                    "chunk_index": chunk_index,
                    "content": normalized,
                    "page_number": segment.get("page_number"),
                    "section_title": segment.get("section_title"),
                    "section_path": segment.get("section_path") or [segment.get("section_title") or "正文"],
                    "segment_type": segment.get("segment_type") or _detect_segment_type(normalized, segment.get("section_title")),
                    "table_like": bool((segment.get("segment_type") == "table") or ("|" in normalized) or ("\t" in normalized)),
                    "visual_tags": _derive_visual_tags(
                        normalized,
                        section_title=segment.get("section_title"),
                        section_path=segment.get("section_path") or [segment.get("section_title") or "正文"],
                        segment_type=segment.get("segment_type") or _detect_segment_type(normalized, segment.get("section_title")),
                        existing_tags=segment.get("visual_tags") or [],
                    ),
                    "ocr_quality": segment.get("ocr_quality"),
                    "visual_evidence": segment.get("visual_evidence")
                    or _extract_visual_evidence(normalized, visual_tags=segment.get("visual_tags") or []),
                    "visual_region": segment.get("visual_region"),
                }
            )
            chunk_index += 1
    return chunks


def _fallback_summary_from_text(text: str, max_length: int) -> str:
    normalized = _normalize_text(text or "")
    if not normalized:
        return "文档内容为空，暂时无法生成摘要。"
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[:max_length].rstrip()}..."


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _prepare_chunks_for_indexing(document_id: int, chunks: list[dict]) -> list[dict]:
    return prepare_chunks_for_indexing(
        document_id,
        chunks,
        build_visual_summary=_build_visual_summary,
    )


def _try_index_document(document_id: int, chunks: list[dict], *, user_id: int | None = None) -> Exception | None:
    try:
        rag_service.index_document(
            document_id,
            _prepare_chunks_for_indexing(document_id, chunks),
            user_id=user_id,
        )
        return None
    except Exception as exc:
        return exc


class DocumentService:
    @staticmethod
    def _parse_metadata_json(raw: str | None) -> dict:
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except (TypeError, ValueError):
            return {}

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
        unique_name = f"{uuid.uuid4().hex}.{file_ext}"
        file_path = storage_service.save_bytes(base_dir=UPLOAD_DIR, filename=unique_name, content=file_bytes)
        content_hash = _sha256_bytes(file_bytes)
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
            file_path=str(file_path),
            file_type=file_ext,
            content_hash=content_hash,
            knowledge_base=knowledge_base,
            classification=classification,
            tags=tags,
            permission_scope=permission_scope,
            sensitivity_level=sensitivity_level,
            permission_users=permission_users,
            permission_roles=permission_roles,
            metadata=metadata,
            status=DOCUMENT_STATUS_PARSED,
        )
        if not created:
            return doc, False

        segments = _extract_segments(str(file_path), file_ext)
        chunks = _split_text(segments)
        db.add_all(
            [
                DocumentChunk(
                    document_id=doc.id,
                    chunk_index=chunk["chunk_index"],
                    content=chunk["content"],
                    page_number=chunk.get("page_number"),
                    section_title=chunk.get("section_title"),
                    section_path=" > ".join(chunk.get("section_path") or []),
                    segment_type=chunk.get("segment_type"),
                    table_like=bool(chunk.get("table_like")),
                    visual_tags=" ".join(chunk.get("visual_tags") or []),
                    ocr_quality=chunk.get("ocr_quality"),
                    embedding_id=_build_embedding_id(doc.id, chunk["chunk_index"]),
                )
                for chunk in chunks
            ]
        )
        db.commit()
        index_error = _try_index_document(doc.id, chunks, user_id=user_id)
        if index_error is None:
            doc.status = DOCUMENT_STATUS_INDEXED
            db.commit()
        return doc, True

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
        file_path: str,
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
            file_path=str(file_path),
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
        ext = Path(file.filename).suffix
        unique_name = f"{uuid.uuid4().hex}{ext}"
        file_bytes = file.file.read()
        file_path = storage_service.save_bytes(base_dir=UPLOAD_DIR, filename=unique_name, content=file_bytes)

        file_type = ext.lstrip(".") if ext else "txt"
        content_hash = _sha256_bytes(file_bytes)
        current_user = db.query(User).filter(User.id == user_id).first()
        knowledge_base = self._resolve_knowledge_base(
            db=db,
            user_id=user_id,
            current_user=current_user,
            knowledge_base_name=knowledge_base_name,
            knowledge_base_category=knowledge_base_category,
            permission_scope=permission_scope,
        )

        if async_mode:
            doc, created = self._persist_document_record(
                db=db,
                user_id=user_id,
                current_user=current_user,
                title=file.filename,
                file_path=str(file_path),
                file_type=file_type,
                content_hash=content_hash,
                knowledge_base=knowledge_base,
                classification=classification,
                tags=tags,
                permission_scope=permission_scope,
                sensitivity_level=sensitivity_level,
                permission_users=permission_users,
                permission_roles=permission_roles,
                metadata=metadata,
                status="pending",
            )
            if not created:
                return doc

            from app.tasks import parse_document_task

            job = document_job_service.create_job(
                document_id=doc.id,
                user_id=user_id,
                job_type="document_parse",
                db=db,
                current_step="submitted",
                message="文档解析任务已提交",
            )
            task = parse_document_task.delay(doc.id, str(file_path), file_type)
            document_job_service.attach_task_id(job.id, task.id, db)
            return doc

        segments = _extract_segments(str(file_path), file_type)

        doc, created = self._persist_document_record(
            db=db,
            user_id=user_id,
            current_user=current_user,
            title=file.filename,
            file_path=str(file_path),
            file_type=file_type,
            content_hash=content_hash,
            knowledge_base=knowledge_base,
            classification=classification,
            tags=tags,
            permission_scope=permission_scope,
            sensitivity_level=sensitivity_level,
            permission_users=permission_users,
            permission_roles=permission_roles,
            metadata=metadata,
            status=DOCUMENT_STATUS_PARSED,
        )
        if not created:
            return doc

        chunks = _split_text(segments)
        db_chunks = [
            DocumentChunk(
                document_id=doc.id,
                chunk_index=chunk["chunk_index"],
                content=chunk["content"],
                page_number=chunk.get("page_number"),
                section_title=chunk.get("section_title"),
                section_path=" > ".join(chunk.get("section_path") or []),
                segment_type=chunk.get("segment_type"),
                table_like=bool(chunk.get("table_like")),
                visual_tags=" ".join(chunk.get("visual_tags") or []),
                ocr_quality=chunk.get("ocr_quality"),
                embedding_id=_build_embedding_id(doc.id, chunk["chunk_index"]),
            )
            for chunk in chunks
        ]
        db.add_all(db_chunks)
        db.commit()

        index_error = _try_index_document(doc.id, chunks, user_id=user_id)
        if index_error is None:
            doc.status = DOCUMENT_STATUS_INDEXED
            db.commit()
        return doc

    def get(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> Document | None:
        doc = db.query(Document).filter(Document.id == document_id).first()
        if not doc:
            return None
        if user_id is None:
            return doc
        return doc if document_governance_service.can_access_document(
            document=doc,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        ) else None

    def summarize(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> str:
        doc = self.get(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if not doc:
            raise ValueError("Document not found")
        return _extract_text(doc.file_path, doc.file_type)

    def ask(
        self,
        document_id: int,
        question: str,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> dict:
        doc = self.get(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if not doc:
            raise ValueError("Document not found")
        final_question = question
        visual_analysis = None
        if _supports_visual_analysis(doc.file_type) and not _question_has_visual_hint(question):
            try:
                visual_analysis = asyncio.run(
                    self.analyze_visual(
                        document_id=document_id,
                        prompt=f"请仅提取与这个问题最相关的视觉线索：{question}",
                        db=db,
                        user_id=doc.user_id,
                    )
                )
            except Exception:
                visual_analysis = None
        if visual_analysis and visual_analysis.get("analysis"):
            final_question = f"{question}\n\n补充视觉分析线索：{visual_analysis['analysis']}"

        result = agentic_rag_service.answer(final_question, document_id=document_id, user_id=doc.user_id)
        qa_record = document_qa_service.record(
            document_id=document_id,
            user_id=doc.user_id,
            question=question,
            answer=result["answer"],
            db=db,
            citations=result["citations"],
            hit_chunks=result["hit_chunks"],
            latency_ms=result["latency_ms"],
            source="document",
        )
        return {
            "qa_record_id": qa_record.id,
            "answer": result["answer"],
            "citations": result["citations"],
            "confidence": result["confidence"],
            "can_answer": result["can_answer"],
            "agentic_rag": result.get("agentic_rag"),
            "feedback_value": qa_record.feedback_value,
            "feedback_status": qa_record.feedback_status,
        }

    async def analyze_visual(
        self,
        document_id: int,
        prompt: str,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> dict:
        doc = self.get(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if not doc:
            raise ValueError("Document not found")
        if doc.file_type.lower() not in VISION_SUPPORTED_FILE_TYPES:
            raise ValueError("Document visual analysis only supports image and PDF files")

        image_url = _file_to_data_url(doc.file_path, doc.file_type)
        analysis = await llm_service.generate_with_images(
            prompt,
            image_urls=[image_url],
            temperature=0.2,
            action="document_visual_analyze",
            user_id=doc.user_id,
        )
        return {
            "document_id": doc.id,
            "title": doc.title,
            "file_type": doc.file_type,
            "analysis": analysis.strip(),
            "image_count": 1,
        }

    async def analyze(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        max_length: int = 500,
    ) -> dict:
        raw_text = self._get_document_text(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        summary_result, risks_result, todos_result, clauses_result, fields_result = await asyncio.gather(
            analysis_service.summarize_document(raw_text, max_length=max_length, user_id=user_id),
            analysis_service.extract_document_risks(raw_text, user_id=user_id),
            analysis_service.extract_document_todos(raw_text, user_id=user_id),
            analysis_service.extract_document_clauses(raw_text, user_id=user_id),
            analysis_service.extract_document_fields(raw_text, user_id=user_id),
            return_exceptions=True,
        )

        warnings: list[dict] = []
        summary = summary_result
        if isinstance(summary_result, Exception):
            summary = _fallback_summary_from_text(raw_text, max_length=max_length)
            warnings.append(
                {
                    "stage": "summary",
                    "message": str(summary_result),
                    "fallback_applied": True,
                }
            )
        risks = risks_result if not isinstance(risks_result, Exception) else []
        if isinstance(risks_result, Exception):
            warnings.append(
                {
                    "stage": "risks",
                    "message": str(risks_result),
                    "fallback_applied": True,
                }
            )
        todos = todos_result if not isinstance(todos_result, Exception) else []
        if isinstance(todos_result, Exception):
            warnings.append(
                {
                    "stage": "todos",
                    "message": str(todos_result),
                    "fallback_applied": True,
                }
            )
        clauses = clauses_result if not isinstance(clauses_result, Exception) else []
        if isinstance(clauses_result, Exception):
            warnings.append(
                {
                    "stage": "clauses",
                    "message": str(clauses_result),
                    "fallback_applied": True,
                }
            )
        structured_fields = fields_result if not isinstance(fields_result, Exception) else {
            "dates": [],
            "amounts": [],
            "owners": [],
            "risk_clauses": [],
        }
        if isinstance(fields_result, Exception):
            warnings.append(
                {
                    "stage": "structured_fields",
                    "message": str(fields_result),
                    "fallback_applied": True,
                }
            )

        doc = self.get(document_id, db, user_id=user_id, role=role, organization_id=organization_id, department_id=department_id)
        if doc:
            doc.summary = summary
            db.commit()

        chunks = self.get_chunks(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
            limit=8,
        )
        references = self._build_references(
            risks=risks,
            todos=todos,
            clauses=clauses,
            structured_fields=structured_fields,
            chunks=chunks,
        )

        return {
            "document_id": document_id,
            "summary": summary,
            "risks": risks,
            "todos": todos,
            "clauses": clauses,
            "structured_fields": structured_fields,
            "references": references,
            "analysis_status": "partial" if warnings else "success",
            "analysis_warnings": warnings,
        }

    @staticmethod
    def _conflict_locator(source_text: str | None, chunks: list[DocumentChunk]) -> dict:
        source = (source_text or "").strip()
        matched_chunk = None
        if source:
            normalized_source = re.sub(r"\s+", "", source)
            for chunk in chunks:
                normalized_chunk = re.sub(r"\s+", "", chunk.content or "")
                if normalized_source in normalized_chunk or normalized_chunk in normalized_source:
                    matched_chunk = chunk
                    break
        return {
            "source_text": source or None,
            "chunk_id": matched_chunk.id if matched_chunk else None,
            "page_number": matched_chunk.page_number if matched_chunk else None,
            "section_title": matched_chunk.section_title if matched_chunk else None,
            "section_path": matched_chunk.section_path if matched_chunk else None,
        }

    def _build_conflict_fact(
        self,
        *,
        document: dict,
        field_type: str,
        item: dict,
        chunks: list[DocumentChunk],
    ) -> dict | None:
        if field_type == "dates":
            value = str(item.get("normalized_date") or item.get("value") or "").strip()
            subject = str(item.get("description") or "").strip()
        elif field_type == "amounts":
            value = str(item.get("amount") or item.get("value") or "").strip()
            subject = str(item.get("description") or "").strip()
        elif field_type == "owners":
            value = str(item.get("name") or "").strip()
            subject = str(item.get("responsibility") or item.get("role") or "").strip()
        else:
            return None
        if not value or not subject:
            return None
        locator = self._conflict_locator(item.get("source_text"), chunks)
        return {
            "document_id": document["document_id"],
            "document_title": document["title"],
            "field_type": field_type,
            "field": subject,
            "value": value,
            **locator,
        }

    @staticmethod
    def _facts_have_same_value(left: dict, right: dict) -> bool:
        def normalize_value(value: str) -> str:
            return re.sub(r"[\s,，。；;：:\-_/]+", "", (value or "").lower())

        return normalize_value(left["value"]) == normalize_value(right["value"])

    def _detect_cross_document_conflicts(self, analyses: list[dict], db: Session) -> dict:
        facts: list[dict] = []
        for document in analyses:
            chunks = (
                db.query(DocumentChunk)
                .filter(DocumentChunk.document_id == document["document_id"])
                .order_by(DocumentChunk.chunk_index.asc())
                .all()
            )
            fields = document.get("structured_fields") or {}
            for field_type in ("dates", "amounts", "owners"):
                for item in fields.get(field_type) or []:
                    if isinstance(item, dict):
                        fact = self._build_conflict_fact(
                            document=document,
                            field_type=field_type,
                            item=item,
                            chunks=chunks,
                        )
                        if fact:
                            facts.append(fact)

        severity_by_type = {"dates": "high", "amounts": "high", "owners": "medium"}
        action_by_type = {
            "dates": "确认最终时间基线，并同步更新计划和会议结论。",
            "amounts": "核对审批版本、合同条款与预算口径后确认最终金额。",
            "owners": "确认唯一责任人，并在任务中明确交付边界和截止时间。",
        }
        label_by_type = {"dates": "日期", "amounts": "金额", "owners": "负责人"}
        conflicts: list[dict] = []
        seen: set[tuple] = set()
        compared_pairs = 0
        for index, left in enumerate(facts):
            for right in facts[index + 1 :]:
                if left["document_id"] == right["document_id"] or left["field_type"] != right["field_type"]:
                    continue
                if not _facts_describe_same_subject(left["field"], right["field"]):
                    continue
                compared_pairs += 1
                if self._facts_have_same_value(left, right):
                    continue
                key = (
                    left["field_type"],
                    tuple(sorted((left["document_id"], right["document_id"]))),
                    tuple(sorted((left["value"], right["value"]))),
                )
                if key in seen:
                    continue
                seen.add(key)
                evidence_complete = all(
                    source.get("source_text")
                    and (source.get("chunk_id") is not None or source.get("page_number") is not None or source.get("section_title"))
                    for source in (left, right)
                )
                conflicts.append(
                    {
                        "field_type": left["field_type"],
                        "field_label": label_by_type[left["field_type"]],
                        "field": left["field"],
                        "source_a": left,
                        "source_b": right,
                        "severity": severity_by_type[left["field_type"]],
                        "recommended_action": action_by_type[left["field_type"]],
                        "evidence_complete": evidence_complete,
                        "status": "confirmed" if evidence_complete else "needs_evidence",
                    }
                )
        conflicts.sort(key=lambda item: (item["evidence_complete"] is False, item["severity"] != "high", item["field"]))
        return {
            "facts_extracted": len(facts),
            "comparable_pairs": compared_pairs,
            "conflicts": conflicts,
            "confirmed_conflict_count": sum(1 for item in conflicts if item["evidence_complete"]),
            "needs_evidence_count": sum(1 for item in conflicts if not item["evidence_complete"]),
        }

    async def compare(
        self,
        document_ids: list[int],
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        max_length: int = 500,
        ) -> dict:
        if len(document_ids) < 2:
            raise ValueError("At least two documents are required for comparison")
        if len(document_ids) > 5:
            raise ValueError("At most five documents can be compared at one time")

        analyses = []
        for document_id in document_ids:
            doc = self.get(document_id, db, user_id=user_id, role=role, organization_id=organization_id, department_id=department_id)
            if not doc:
                raise ValueError(f"Document not found: {document_id}")

            analysis = await self.analyze(
                document_id,
                db,
                user_id=user_id,
                role=role,
                organization_id=organization_id,
                department_id=department_id,
                max_length=max_length,
            )
            analyses.append(
                {
                    "document_id": document_id,
                    "title": doc.title,
                    "summary": analysis["summary"],
                    "risks": analysis["risks"],
                    "todos": analysis["todos"],
                    "structured_fields": analysis.get(
                        "structured_fields",
                        {"dates": [], "amounts": [], "owners": [], "risk_clauses": []},
                    ),
                    "references": analysis["references"],
                    "risks_text": "；".join(
                        [f"{item.get('title', '')}:{item.get('description', '')}" for item in analysis["risks"][:5]]
                    ),
                    "todos_text": "；".join(
                        [f"{item.get('title', '')}:{item.get('description', '')}" for item in analysis["todos"][:5]]
                    ),
                }
            )

        comparison = await analysis_service.compare_documents(analyses, user_id=user_id)
        conflict_analysis = self._detect_cross_document_conflicts(analyses, db)
        comparison["conflict_analysis"] = conflict_analysis
        summary_cards = [
            {
                "document_id": item["document_id"],
                "title": item["title"],
                "summary": item["summary"],
                "risk_count": len(item["risks"]),
                "todo_count": len(item["todos"]),
                "reference_count": len(item["references"]),
            }
            for item in analyses
        ]
        return {
            "document_ids": document_ids,
            "documents": analyses,
            "summary_cards": summary_cards,
            "comparison": comparison,
        }

    async def extract_risks(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> list[dict]:
        raw_text = self._get_document_text(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        return await analysis_service.extract_document_risks(raw_text, user_id=user_id)

    async def extract_todos(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> list[dict]:
        raw_text = self._get_document_text(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        return await analysis_service.extract_document_todos(raw_text, user_id=user_id)

    async def extract_key_clauses(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> list[dict]:
        raw_text = self._get_document_text(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        return await analysis_service.extract_document_clauses(raw_text, user_id=user_id)

    def _get_document_text(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
    ) -> str:
        doc = self.get(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if not doc:
            raise ValueError("Document not found")
        return _extract_text(doc.file_path, doc.file_type)

    def get_chunks(
        self,
        document_id: int,
        db: Session,
        user_id: int | None = None,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        limit: int = 8,
    ) -> list[DocumentChunk]:
        doc = self.get(
            document_id,
            db,
            user_id=user_id,
            role=role,
            organization_id=organization_id,
            department_id=department_id,
        )
        if not doc:
            raise ValueError("Document not found")
        return (
            db.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index.asc())
            .limit(limit)
            .all()
        )

    def list_documents(
        self,
        *,
        db: Session,
        user_id: int,
        role: str | None = None,
        organization_id: int | None = None,
        department_id: int | None = None,
        knowledge_base_id: int | None = None,
        classification: str | None = None,
        sensitivity_level: str | None = None,
        connector_id: int | None = None,
        query: str | None = None,
    ) -> list[Document]:
        rows = db.query(Document).order_by(Document.created_at.desc(), Document.id.desc()).all()
        filtered = []
        for doc in rows:
            if not document_governance_service.can_access_document(
                document=doc,
                user_id=user_id,
                role=role,
                organization_id=organization_id,
                department_id=department_id,
            ):
                continue
            if knowledge_base_id is not None and doc.knowledge_base_id != knowledge_base_id:
                continue
            if classification and doc.classification != classification:
                continue
            if sensitivity_level and doc.sensitivity_level != sensitivity_level:
                continue
            if connector_id is not None:
                metadata = self._parse_metadata_json(doc.metadata_json)
                if _safe_int(metadata.get("connector_id"), 0) != connector_id:
                    continue
            if query and query not in (doc.title or ""):
                continue
            filtered.append(doc)
        return filtered

    def _build_references(
        self,
        risks: list[dict],
        todos: list[dict],
        clauses: list[dict],
        structured_fields: dict,
        chunks: list[DocumentChunk],
    ) -> list[dict]:
        references = []
        seen = set()

        def add_reference(text: str | None, source_type: str, label: str) -> None:
            normalized = (text or "").strip()
            if not normalized:
                return
            key = normalized[:180]
            if key in seen:
                return
            seen.add(key)
            references.append(
                {
                    "source_type": source_type,
                    "label": label,
                    "quote": normalized[:240],
                }
            )

        for index, item in enumerate(risks, start=1):
            add_reference(item.get("evidence"), "risk", f"风险依据 {index}")
        for index, item in enumerate(todos, start=1):
            add_reference(item.get("source_text") or item.get("evidence"), "todo", f"待办依据 {index}")
        for index, item in enumerate(clauses, start=1):
            add_reference(item.get("evidence"), "clause", f"条款依据 {index}")
        for index, item in enumerate(structured_fields.get("dates") or [], start=1):
            add_reference(item.get("source_text"), "field", f"日期依据 {index}")
        for index, item in enumerate(structured_fields.get("amounts") or [], start=1):
            add_reference(item.get("source_text"), "field", f"金额依据 {index}")
        for index, item in enumerate(structured_fields.get("owners") or [], start=1):
            add_reference(item.get("source_text"), "field", f"责任人依据 {index}")
        for index, item in enumerate(structured_fields.get("risk_clauses") or [], start=1):
            add_reference(item.get("source_text"), "field", f"风险条款依据 {index}")

        if not references:
            for chunk in chunks[:6]:
                add_reference(chunk.content, "chunk", f"文档片段 {chunk.chunk_index + 1}")

        return references[:8]


document_service = DocumentService()
