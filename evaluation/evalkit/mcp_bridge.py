"""MCP client that lists and calls tools on a generated server over stdio.

This is the spine of the evaluation: the agent never touches HTTP directly. It picks
a tool by name + args, and this bridge routes the call through the *real* generated
MCP server (launched as a subprocess) to the sandbox — so we measure the artifact
SYNAPSE actually ships, not a reconstruction. ``list_tools`` yields each tool's JSON
schema, which the agent layer turns into OpenAI function-calling definitions.
"""
from __future__ import annotations

import json
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from evalkit.server_host import GeneratedServer


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


class McpToolError(RuntimeError):
    pass


class McpToolClient:
    """Async context manager wrapping an MCP stdio session to a generated server."""

    def __init__(self, server: GeneratedServer) -> None:
        self._server = server
        self._session: ClientSession | None = None
        self._stack: AsyncExitStack | None = None

    async def __aenter__(self) -> "McpToolClient":
        # AsyncExitStack enters and unwinds both contexts within this single task,
        # avoiding anyio's "exit cancel scope in a different task" error that a manual
        # split __aenter__/__aexit__ provokes when an exception propagates.
        spec = self._server.launch_spec()
        params = StdioServerParameters(command=spec.command, args=spec.args, env=spec.env)
        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        self._session = None

    async def list_tools(self) -> list[ToolSpec]:
        assert self._session is not None
        resp = await self._session.list_tools()
        return [
            ToolSpec(t.name, t.description or "", dict(t.inputSchema or {}))
            for t in resp.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        assert self._session is not None
        result = await self._session.call_tool(name, arguments=arguments)
        text = "\n".join(
            getattr(c, "text", "") for c in result.content if getattr(c, "type", None) == "text"
        )
        if getattr(result, "isError", False):
            raise McpToolError(f"tool {name} errored: {text}")
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return text
