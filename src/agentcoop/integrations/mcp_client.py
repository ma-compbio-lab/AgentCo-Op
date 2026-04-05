from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Mapping, Optional, Sequence

from agentcoop.ir.schema import MCPServerRef, ToolRef, Transport, WorkflowBlueprint

JsonDict = Dict[str, Any]


class MCPClientError(RuntimeError):
    """Raised when an MCP tool invocation cannot be completed."""


class MCPClientManager:
    """Thin synchronous wrapper around the MCP Python SDK client transports."""

    def describe_tools(
        self,
        bound_tools: Sequence[ToolRef],
        *,
        blueprint: WorkflowBlueprint,
        sandbox_runner: Any = None,
    ) -> List[JsonDict]:
        by_server: Dict[str, List[JsonDict]] = {}
        descriptors: List[JsonDict] = []
        for tool_ref in bound_tools:
            if tool_ref.server not in by_server:
                server_ref = self._server_for(blueprint, tool_ref.server)
                try:
                    by_server[tool_ref.server] = self.list_tools(server_ref, sandbox_runner=sandbox_runner)
                except MCPClientError:
                    by_server[tool_ref.server] = []
            match = next(
                (item for item in by_server[tool_ref.server] if item.get("name") == tool_ref.tool),
                None,
            )
            if match is None:
                match = {
                    "name": f"{tool_ref.server}:{tool_ref.tool}",
                    "server": tool_ref.server,
                    "tool": tool_ref.tool,
                    "description": "",
                    "input_schema": {},
                }
            descriptors.append(match)
        return descriptors

    def call_bound_tool(
        self,
        bound_tools: Sequence[ToolRef],
        requested_tool: str,
        arguments: Mapping[str, Any],
        *,
        blueprint: WorkflowBlueprint,
        sandbox_runner: Any = None,
    ) -> JsonDict:
        target = self._resolve_bound_tool(bound_tools, requested_tool)
        if target is None:
            raise MCPClientError(f"Tool '{requested_tool}' is not in the node's bound tool set")
        server_ref = self._server_for(blueprint, target.server)
        return self.call_tool(
            server_ref,
            target.tool,
            dict(arguments),
            sandbox_runner=sandbox_runner,
        )

    def list_tools(
        self,
        server_ref: MCPServerRef,
        *,
        sandbox_runner: Any = None,
    ) -> List[JsonDict]:
        resolved = self._resolved_server(server_ref, sandbox_runner=sandbox_runner)
        return asyncio.run(self._list_tools_async(resolved))

    def call_tool(
        self,
        server_ref: MCPServerRef,
        tool_name: str,
        arguments: Mapping[str, Any],
        *,
        sandbox_runner: Any = None,
    ) -> JsonDict:
        resolved = self._resolved_server(server_ref, sandbox_runner=sandbox_runner)
        return asyncio.run(self._call_tool_async(resolved, tool_name, dict(arguments)))

    async def _list_tools_async(self, server_ref: MCPServerRef) -> List[JsonDict]:
        async def _runner(session: Any) -> List[JsonDict]:
            listing = await session.list_tools()
            tools = getattr(listing, "tools", listing)
            return [self._normalize_tool_descriptor(tool, server_ref.name) for tool in tools]

        return await self._with_session(server_ref, _runner)

    async def _call_tool_async(
        self,
        server_ref: MCPServerRef,
        tool_name: str,
        arguments: JsonDict,
    ) -> JsonDict:
        async def _runner(session: Any) -> JsonDict:
            try:
                result = await session.call_tool(tool_name, arguments=arguments)
            except TypeError:
                result = await session.call_tool(tool_name, arguments)
            normalized = self._normalize_tool_result(result)
            if normalized.get("is_error"):
                raise MCPClientError(str(normalized.get("content") or normalized.get("structured")))
            return normalized

        return await self._with_session(server_ref, _runner)

    async def _with_session(self, server_ref: MCPServerRef, runner: Any) -> Any:
        try:
            from mcp import ClientSession, StdioServerParameters
        except ImportError as exc:
            raise MCPClientError("Install the 'mcp' extra to enable live MCP tool calls") from exc

        if server_ref.transport == Transport.stdio:
            try:
                from mcp.client.stdio import stdio_client
            except ImportError as exc:
                raise MCPClientError("The installed MCP SDK does not expose stdio_client") from exc

            if not server_ref.stdio_cmd:
                raise MCPClientError(f"stdio server '{server_ref.name}' is missing stdio_cmd")
            params = StdioServerParameters(
                command=server_ref.stdio_cmd[0],
                args=server_ref.stdio_cmd[1:],
                env={**os.environ, **server_ref.env},
            )
            async with stdio_client(params) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return await runner(session)

        if server_ref.transport == Transport.streamable_http:
            try:
                from mcp.client.streamable_http import streamable_http_client
            except ImportError as exc:
                raise MCPClientError("The installed MCP SDK does not expose streamable_http_client") from exc

            if not server_ref.url:
                raise MCPClientError(f"HTTP server '{server_ref.name}' is missing url")
            async with streamable_http_client(url=server_ref.url) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return await runner(session)

        if server_ref.transport == Transport.sse:
            try:
                from mcp.client.sse import sse_client
            except ImportError as exc:
                raise MCPClientError("The installed MCP SDK does not expose sse_client") from exc

            if not server_ref.url:
                raise MCPClientError(f"SSE server '{server_ref.name}' is missing url")
            async with sse_client(url=server_ref.url) as streams:
                read_stream, write_stream = streams[0], streams[1]
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return await runner(session)

        raise MCPClientError(f"Unsupported transport: {server_ref.transport.value}")

    @staticmethod
    def _server_for(blueprint: WorkflowBlueprint, server_name: str) -> MCPServerRef:
        for server in blueprint.mcp_servers:
            if server.name == server_name:
                return server
        raise MCPClientError(f"Unknown MCP server '{server_name}'")

    @staticmethod
    def _resolve_bound_tool(bound_tools: Sequence[ToolRef], requested_tool: str) -> Optional[ToolRef]:
        normalized = requested_tool.strip()
        for tool in bound_tools:
            if normalized in {tool.tool, f"{tool.server}:{tool.tool}"}:
                return tool
        if len(bound_tools) == 1 and not normalized:
            return bound_tools[0]
        return None

    @staticmethod
    def _resolved_server(server_ref: MCPServerRef, *, sandbox_runner: Any = None) -> MCPServerRef:
        if server_ref.sandbox is not None and sandbox_runner is not None:
            return sandbox_runner.materialize_server_ref(server_ref)
        return server_ref

    @staticmethod
    def _normalize_tool_descriptor(tool: Any, server_name: str) -> JsonDict:
        name = getattr(tool, "name", None)
        description = getattr(tool, "description", "")
        input_schema = getattr(tool, "inputSchema", None)
        if input_schema is None:
            input_schema = getattr(tool, "input_schema", {})
        return {
            "name": f"{server_name}:{name}",
            "server": server_name,
            "tool": name,
            "description": description,
            "input_schema": input_schema or {},
        }

    @staticmethod
    def _normalize_tool_result(result: Any) -> JsonDict:
        structured = getattr(result, "structuredContent", None)
        if structured is None:
            structured = getattr(result, "structured_content", None)
        content = getattr(result, "content", None)
        if content is None and isinstance(result, Mapping):
            content = result.get("content")

        normalized_content: Any = content
        if isinstance(content, list):
            pieces: List[Any] = []
            for item in content:
                text = getattr(item, "text", None)
                if text is not None:
                    pieces.append(text)
                elif isinstance(item, Mapping) and "text" in item:
                    pieces.append(item["text"])
                else:
                    pieces.append(repr(item))
            normalized_content = pieces

        return {
            "structured": structured,
            "content": normalized_content,
            "is_error": bool(getattr(result, "isError", False) or getattr(result, "is_error", False)),
        }
