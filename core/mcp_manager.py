from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, Iterable

from utils import log_event


@dataclass
class MCPPromptRef:
    server: str
    prompt: str


class MCPManager:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.enabled = bool(getattr(cfg, "enabled", False))
        self.approval_mode = getattr(cfg, "approval_mode", "auto")
        self.cache_tools_list = bool(getattr(cfg, "cache_tools_list", True))
        self.enforce_tool_filter = bool(getattr(cfg, "enforce_tool_filter", False))
        self._servers_cfg = getattr(cfg, "servers", {}) or {}
        self._prompt_map = getattr(cfg, "prompts", {}) or {}
        self._tool_cache: dict[str, list[str]] = {}
        self._prompt_cache: dict[str, str] = {}
        self._mcp = None
        if self.enabled:
            try:
                from agents import mcp as mcp_mod  # type: ignore

                self._mcp = mcp_mod
            except Exception as exc:  # noqa: BLE001 - optional dependency
                log_event("RUNTIME", "mcp", "MCP disabled (agents.mcp unavailable)", level="warn", data={"error": str(exc)})
                self.enabled = False

    def server_names(self) -> list[str]:
        return list(self._servers_cfg.keys())

    def is_enabled(self) -> bool:
        return bool(self.enabled and self._mcp)

    def prompt_ref(self, agent_id: str) -> MCPPromptRef | None:
        ref = self._prompt_map.get(agent_id) if isinstance(self._prompt_map, dict) else None
        if not isinstance(ref, dict):
            return None
        server = ref.get("server")
        prompt = ref.get("prompt")
        if not server or not prompt:
            return None
        return MCPPromptRef(server=server, prompt=prompt)

    async def list_tools(self, server_name: str) -> list[str]:
        if server_name in self._tool_cache:
            return self._tool_cache[server_name]
        if not self.is_enabled():
            return []
        try:
            async with self.open_servers([server_name]) as servers:
                if not servers:
                    return []
                tools = await self._fetch_tools(servers[0])
                self._tool_cache[server_name] = tools
                return tools
        except Exception as exc:  # noqa: BLE001 - external server failures
            log_event("RUNTIME", "mcp", "failed to list tools", level="warn", data={"server": server_name, "error": str(exc)})
            return []

    async def describe_servers(self) -> str:
        if not self.is_enabled() or not self._servers_cfg:
            return ""
        lines = []
        for name in self.server_names():
            tools = await self.list_tools(name)
            if tools:
                lines.append(f"- {name}: {', '.join(tools)}")
            else:
                lines.append(f"- {name}: (tools unavailable)")
        return "\n".join(lines)

    async def get_prompt(self, agent_id: str) -> str | None:
        if not self.is_enabled():
            return None
        ref = self.prompt_ref(agent_id)
        if not ref:
            return None
        cache_key = f"{ref.server}:{ref.prompt}"
        if cache_key in self._prompt_cache:
            return self._prompt_cache[cache_key]
        async with self.open_servers([ref.server]) as servers:
            if not servers:
                return None
            prompt_obj = await self._fetch_prompt(servers[0], ref.prompt)
            prompt_text = self._normalize_prompt(prompt_obj)
            if prompt_text:
                self._prompt_cache[cache_key] = prompt_text
            return prompt_text

    @asynccontextmanager
    async def open_servers(
        self,
        server_names: Iterable[str],
        *,
        allowed_tools: list[str] | None = None,
        require_approval: bool = False,
    ):
        if not self.is_enabled():
            yield []
            return
        names = [name for name in server_names if name in self._servers_cfg]
        if not names:
            yield []
            return
        if require_approval and self.approval_mode == "required":
            raise PermissionError("MCP tool usage requires approval.")
        async with AsyncExitStack() as stack:
            servers = []
            for name in names:
                try:
                    server = self._build_server(name, allowed_tools=allowed_tools, require_approval=require_approval)
                except Exception as exc:  # noqa: BLE001 - external server errors
                    log_event("RUNTIME", "mcp", "failed to build MCP server", level="warn", data={"server": name, "error": str(exc)})
                    continue
                if hasattr(server, "__aenter__"):
                    try:
                        server = await stack.enter_async_context(server)
                    except Exception as exc:  # noqa: BLE001
                        log_event("RUNTIME", "mcp", "failed to start MCP server", level="warn", data={"server": name, "error": str(exc)})
                        continue
                servers.append(server)
            yield servers

    @contextmanager
    def bind_agent(self, agent, mcp_servers: list[object], extra_tools: list[object] | None = None):
        if not mcp_servers and not extra_tools:
            yield agent
            return
        prev_servers = getattr(agent, "mcp_servers", None)
        prev_tools = getattr(agent, "tools", None)
        try:
            if mcp_servers:
                setattr(agent, "mcp_servers", mcp_servers)
            if extra_tools and isinstance(prev_tools, list):
                setattr(agent, "tools", prev_tools + list(extra_tools))
            yield agent
        finally:
            if mcp_servers:
                setattr(agent, "mcp_servers", prev_servers)
            if extra_tools and isinstance(prev_tools, list):
                setattr(agent, "tools", prev_tools)

    def _build_server(self, name: str, *, allowed_tools: list[str] | None, require_approval: bool):
        cfg = self._servers_cfg.get(name, {}) if isinstance(self._servers_cfg, dict) else {}
        transport = (cfg.get("transport") or "stdio").lower()
        tool_filter = self._build_tool_filter(cfg, allowed_tools=allowed_tools)
        cache_tools = bool(cfg.get("cache_tools_list", self.cache_tools_list))

        if transport == "stdio":
            params = {
                "command": cfg.get("command"),
                "args": self._expand_args(cfg.get("args", [])),
                "env": self._expand_env(cfg.get("env", {})),
            }
            server_cls = getattr(self._mcp, "MCPServerStdio", None)
            if server_cls is None:
                raise AttributeError("MCPServerStdio not available in agents.mcp")
            return self._init_server(server_cls, name=name, params=params, tool_filter=tool_filter, cache=cache_tools)
        if transport in {"streamable_http", "http"}:
            url = cfg.get("url")
            headers = self._expand_env(cfg.get("headers", {}))
            server_cls = getattr(self._mcp, "MCPServerStreamableHttp", None)
            if server_cls is None:
                raise AttributeError("MCPServerStreamableHttp not available in agents.mcp")
            return self._init_http_server(
                server_cls,
                name=name,
                url=url,
                headers=headers,
                tool_filter=tool_filter,
                cache=cache_tools,
            )
        if transport in {"sse", "http_sse"}:
            url = cfg.get("url")
            headers = self._expand_env(cfg.get("headers", {}))
            server_cls = getattr(self._mcp, "MCPServerSse", None)
            if server_cls is None:
                raise AttributeError("MCPServerSse not available in agents.mcp")
            return self._init_http_server(
                server_cls,
                name=name,
                url=url,
                headers=headers,
                tool_filter=tool_filter,
                cache=cache_tools,
            )
        log_event("RUNTIME", "mcp", "unsupported MCP transport", level="warn", data={"name": name, "transport": transport})
        server_cls = getattr(self._mcp, "MCPServerStdio", None)
        if server_cls is None:
            raise AttributeError("MCPServerStdio not available in agents.mcp")
        return self._init_server(server_cls, name=name, params={}, tool_filter=None, cache=False)

    def _build_tool_filter(self, cfg: dict, *, allowed_tools: list[str] | None):
        if not self._mcp:
            return None
        create_filter = getattr(self._mcp, "create_static_tool_filter", None)
        if not create_filter:
            return None
        default_tools = cfg.get("default_tools") if isinstance(cfg, dict) else None
        tools = allowed_tools or (default_tools if isinstance(default_tools, list) else None) or []
        if not tools and not self.enforce_tool_filter:
            return None
        return create_filter(allowed_tool_names=tools)

    def _expand_env(self, env: dict[str, str] | None) -> dict[str, str]:
        if not env:
            return {}
        return {key: os.path.expandvars(str(value)) for key, value in env.items()}

    def _expand_args(self, args: list | None) -> list[str]:
        if not args:
            return []
        return [os.path.expandvars(str(arg)) for arg in args]

    def _init_server(self, cls, *, name: str, params: dict[str, Any], tool_filter, cache: bool):
        try:
            return cls(name=name, params=params, tool_filter=tool_filter, cache_tools_list=cache)
        except TypeError:
            try:
                return cls(name, params, tool_filter=tool_filter, cache_tools_list=cache)
            except TypeError:
                return cls(name=name, params=params)

    def _init_http_server(self, cls, *, name: str, url: str | None, headers: dict[str, str], tool_filter, cache: bool):
        try:
            return cls(name=name, url=url, headers=headers, tool_filter=tool_filter, cache_tools_list=cache)
        except TypeError:
            try:
                return cls(name=name, params={"url": url, "headers": headers}, tool_filter=tool_filter, cache_tools_list=cache)
            except TypeError:
                return cls(name=name, url=url)

    async def _fetch_tools(self, server) -> list[str]:
        tool_list = []
        if hasattr(server, "list_tools"):
            tools = server.list_tools()
            tools = await tools if asyncio.iscoroutine(tools) else tools
            tool_list = tools or []
        elif hasattr(server, "tools"):
            tool_list = getattr(server, "tools") or []
        names = []
        for tool in tool_list:
            if isinstance(tool, str):
                names.append(tool)
            elif isinstance(tool, dict) and tool.get("name"):
                names.append(tool["name"])
            else:
                name = getattr(tool, "name", None)
                if name:
                    names.append(name)
        return names

    async def _fetch_prompt(self, server, prompt_name: str) -> Any:
        if hasattr(server, "get_prompt"):
            prompt = server.get_prompt(prompt_name)
            return await prompt if asyncio.iscoroutine(prompt) else prompt
        if hasattr(server, "list_prompts"):
            prompts = server.list_prompts()
            prompts = await prompts if asyncio.iscoroutine(prompts) else prompts
            for item in prompts or []:
                name = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
                if name == prompt_name:
                    return item
        return None

    @staticmethod
    def _normalize_prompt(prompt_obj: Any) -> str | None:
        if prompt_obj is None:
            return None
        if isinstance(prompt_obj, str):
            return prompt_obj
        if isinstance(prompt_obj, dict):
            if "content" in prompt_obj and isinstance(prompt_obj["content"], str):
                return prompt_obj["content"]
            messages = prompt_obj.get("messages")
            if isinstance(messages, list):
                return "\n".join(
                    f"{m.get('role','user')}: {m.get('content','')}" if isinstance(m, dict) else str(m)
                    for m in messages
                )
        messages = getattr(prompt_obj, "messages", None)
        if isinstance(messages, list):
            return "\n".join(
                f"{getattr(m, 'role', 'user')}: {getattr(m, 'content', '')}" if not isinstance(m, dict) else str(m)
                for m in messages
            )
        content = getattr(prompt_obj, "content", None)
        if isinstance(content, str):
            return content
        return None
