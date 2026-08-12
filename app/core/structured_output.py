"""结构化输出：从模型文本中提取并校验 JSON（JSON Schema / Pydantic）。

- ``normalize_schema``：把 JSON Schema(dict) 或 Pydantic 模型统一为 ``SchemaSpec``。
- ``extract_json_candidate``：无模型修复——去掉代码块与前后噪声，取首个合法 JSON 片段。
- ``parse_structured_output``：提取 + 校验，返回 (data, None) 或 (None, 失败类别)。
- ``build_repair_prompt``：携带原始 schema 的修复请求 prompt；仅修格式、不改语义。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

import jsonschema
from pydantic import BaseModel, ValidationError

from app.core.model_policy import ModelErrorKind


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def extract_json_candidate(text: str | None) -> str | None:
    """无模型修复：去代码块/前后噪声后返回首个合法 JSON 片段；无则 None。"""
    if not text:
        return None
    cleaned = _strip_code_fence(text)
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char not in "{[":
            continue
        try:
            _, end = decoder.raw_decode(cleaned, index)
        except json.JSONDecodeError:
            continue
        return cleaned[index:end]
    return None


@dataclass(frozen=True)
class SchemaSpec:
    """统一后的 schema：携带 JSON Schema 表示 + 数据校验器。"""

    json_schema: dict[str, Any]
    validator: Callable[[Any], str | None]

    def validate(self, data: Any) -> str | None:
        """返回 None 表示通过；否则返回可读的失败原因。"""
        return self.validator(data)


def _json_schema_validator(schema: dict[str, Any]) -> Callable[[Any], str | None]:
    validator = jsonschema.Draft202012Validator(schema)

    def validate(data: Any) -> str | None:
        try:
            validator.validate(data)
            return None
        except jsonschema.ValidationError as exc:
            return exc.message

    return validate


def _pydantic_validator(model_cls: type[BaseModel]) -> Callable[[Any], str | None]:
    def validate(data: Any) -> str | None:
        try:
            model_cls.model_validate(data)
            return None
        except ValidationError as exc:
            return str(exc)

    return validate


def normalize_schema(schema: dict | type[BaseModel]) -> SchemaSpec:
    """把 JSON Schema(dict) 或 Pydantic 模型统一为 SchemaSpec；其他类型抛 TypeError。"""
    if isinstance(schema, dict):
        jsonschema.Draft202012Validator.check_schema(schema)
        return SchemaSpec(json_schema=schema, validator=_json_schema_validator(schema))
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        return SchemaSpec(json_schema=schema.model_json_schema(), validator=_pydantic_validator(schema))
    raise TypeError("schema 必须是 JSON Schema dict 或 Pydantic 模型")


def parse_structured_output(raw: str, spec: SchemaSpec) -> tuple[Any | None, ModelErrorKind | None]:
    """提取 + 校验：返回 (data, None) 或 (None, invalid_response / schema_validation_failed)。"""
    candidate = extract_json_candidate(raw)
    if candidate is None:
        return None, ModelErrorKind.INVALID_RESPONSE
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None, ModelErrorKind.INVALID_RESPONSE
    error = spec.validate(data)
    if error is not None:
        return None, ModelErrorKind.SCHEMA_VALIDATION_FAILED
    return data, None


_REPAIR_INSTRUCTION = (
    "上一条模型输出的 JSON 不合法或不符合给定的 JSON Schema。"
    "请只修复 JSON 格式使其符合 Schema，不得改变原有语义与内容。"
    "只输出一个 JSON 值，不要代码块标记、解释或多余文本。"
)


def build_repair_prompt(schema: dict[str, Any], raw_text: str) -> str:
    """构造修复请求 prompt：携带原始 schema，仅要求格式修复（不改业务语义）。"""
    schema_json = json.dumps(schema, ensure_ascii=False, sort_keys=True)
    return (
        f"{_REPAIR_INSTRUCTION}\n\n"
        f"JSON Schema：\n{schema_json}\n\n"
        f"需要修复的原始输出：\n{raw_text}\n\n"
        "只输出 JSON。"
    )
