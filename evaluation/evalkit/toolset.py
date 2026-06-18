"""Convert MCP tool schemas into OpenAI function-calling tool definitions.

The MCP server (via tools/list) advertises each tool's name, description, and a JSON
Schema for its arguments. OpenAI function-calling wants the same information under a
slightly different envelope. This is the only translation needed — the agent picks a
function, and the result is routed back through the MCP client to the real server.
"""
from __future__ import annotations

from typing import Any

from evalkit.mcp_bridge import ToolSpec


def _as_object_schema(schema: dict[str, Any] | None) -> dict[str, Any]:
    if not schema or schema.get("type") != "object":
        return {"type": "object", "properties": {}}
    # OpenAI is strict about the schema shape; keep only what it understands.
    out: dict[str, Any] = {"type": "object", "properties": schema.get("properties") or {}}
    if "required" in schema:
        out["required"] = schema["required"]
    return out


def to_openai_tools(specs: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": (s.description or "")[:1024],
                "parameters": _as_object_schema(s.input_schema),
            },
        }
        for s in specs
    ]
