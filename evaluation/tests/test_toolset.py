"""Unit tests for MCP-schema -> OpenAI function-calling conversion."""
from __future__ import annotations

from evalkit.mcp_bridge import ToolSpec
from evalkit.toolset import to_openai_tools


def test_conversion_preserves_schema():
    specs = [ToolSpec(
        name="create_pet",
        description="Create a pet",
        input_schema={"type": "object",
                      "properties": {"name": {"type": "string"}},
                      "required": ["name"]},
    )]
    tools = to_openai_tools(specs)
    assert len(tools) == 1
    fn = tools[0]["function"]
    assert tools[0]["type"] == "function"
    assert fn["name"] == "create_pet"
    assert fn["parameters"]["required"] == ["name"]
    assert fn["parameters"]["properties"]["name"]["type"] == "string"


def test_non_object_schema_defaults_to_empty_object():
    tools = to_openai_tools([ToolSpec("x", "", {"type": "string"})])
    assert tools[0]["function"]["parameters"] == {"type": "object", "properties": {}}


def test_missing_schema_is_safe():
    tools = to_openai_tools([ToolSpec("y", "", {})])
    assert tools[0]["function"]["parameters"] == {"type": "object", "properties": {}}
