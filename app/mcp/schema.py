"""MCP tool schema conversion — adapts existing BaseAgentTool metadata to
standard MCP Tool format and validates tool arguments against schemas."""

from __future__ import annotations

from typing import Any

from jsonschema import ValidationError, validate as json_validate

from app.tools.base import BaseAgentTool


def tool_to_mcp_spec(tool: BaseAgentTool) -> dict[str, Any]:
    """Convert a BaseAgentTool into an MCP tool-spec dict.

    The output matches the shape of MCP's ``Tool`` model fields
    (name, description, inputSchema) and can be serialised as-is.
    """
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.parameters,
    }


def validate_tool_args(tool: BaseAgentTool, args: dict[str, Any]) -> dict[str, Any]:
    """Validate ``args`` against the tool's JSON Schema.

    Returns the validated args (auto-context fields may still be injected
    later by the caller).  Raises ``jsonschema.ValidationError`` on failure.
    """
    schema = tool.parameters
    if schema.get("properties"):
        json_validate(instance=args, schema=schema)
    return args


def trim_sensitive_args(args: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``args`` with sensitive fields removed (for logging)."""
    SENSITIVE_KEYS = {"db", "password", "api_key", "token", "secret"}
    safe = {}
    for k, v in args.items():
        if k in SENSITIVE_KEYS:
            safe[k] = "****"
        elif isinstance(v, dict):
            safe[k] = trim_sensitive_args(v)
        else:
            safe[k] = v
    return safe
