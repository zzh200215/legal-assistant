from __future__ import annotations

from typing import Any


def _sanitize_tool_error(error: str | None, fallback_message: str) -> str:
    if not error:
        return fallback_message
    return fallback_message


def tool_success(message: str, data: dict | None = None) -> dict:
    return {
        "success": True,
        "message": message,
        "data": data or {},
        "error": None,
    }


def tool_error(message: str, error: str | None = None, data: dict | None = None) -> dict:
    return {
        "success": False,
        "message": message,
        "data": data or {},
        "error": _sanitize_tool_error(error, message),
    }


class BaseAgentTool:
    name = ""
    description = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    auto_context_fields: tuple[str, ...] = ()

    def spec(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "auto_context_fields": list(self.auto_context_fields),
        }

    def normalize_result(self, result: dict | None) -> dict:
        if not isinstance(result, dict):
            return tool_error("Tool returned invalid payload", data={"raw_result": result})

        success = bool(result.get("success", False))
        message = str(result.get("message") or ("ok" if success else "error"))
        return {
            "success": success,
            "message": message,
            "data": result.get("data") if isinstance(result.get("data"), dict) else {},
            "error": None if success else _sanitize_tool_error(result.get("error"), message),
        }
