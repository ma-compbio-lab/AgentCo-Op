from __future__ import annotations

import json

from core.contracts import (
    EvidencePack,
    EvidenceRequest,
    ExecutionPlan,
    Message,
    TaskSpec,
    ToolExecutionResult,
    ToolPlan,
)
from core.hooks import HookManager
from core.observability import Observability
from core.runtime import run_agent
from engine.protocols import ProtocolBase
from prompts import build_docker_repair_prompt, build_evidence_prompt, build_task_prompt, format_memory_block


class ExecutionEngine:
    def __init__(
        self,
        agent_pool: dict[str, object],
        protocols: dict[str, ProtocolBase],
        hooks: HookManager,
        observability: Observability,
        docker_runtime: object | None = None,
        tool_cfg: object | None = None,
        run_hooks: object | None = None,
    ) -> None:
        self.agent_pool = agent_pool
        self.protocols = protocols
        self.hooks = hooks
        self.obs = observability
        self.docker_runtime = docker_runtime
        self.tool_cfg = tool_cfg
        self.run_hooks = run_hooks

    async def run(self, task: TaskSpec, plan: ExecutionPlan, ctx, session=None) -> dict:
        from utils import clip_text, log_event, log_section, should_show_outputs, should_show_prompts

        log_section("ENGINE", "Execution")
        state: dict = {"messages": [], "artifacts": {}, "round": 0}
        protocol = self.protocols[plan.protocol]
        protocol_state = protocol.prepare(task, plan)
        mcp_manager = getattr(ctx, "mcp_manager", None)

        # Evidence is collected once up front to avoid repeated web searches per step.
        if plan.needs_web_search and plan.evidence_requests:
            await self._run_evidence_steps(
                plan.evidence_requests,
                state,
                ctx,
                session=session,
                verbosity=task.prompt_verbosity,
            )
        if plan.tool_plan:
            await self._run_tool_plan(task, plan.tool_plan, state, ctx)

        max_steps = max(plan.max_rounds, len(plan.subtasks), 1) * max(len(plan.active_agents), 1)
        log_event("ENGINE", "start", "executor started", data={"protocol": plan.protocol, "max_steps": max_steps})

        while state["round"] < max_steps:
            step = protocol.next_step(task, plan, state, protocol_state)
            if not step:
                break
            step = self.hooks.pre_step(task, plan, state, step)

            agent_id = step["agent_id"]
            if agent_id not in self.agent_pool:
                if plan.active_agents:
                    agent_id = plan.active_agents[0]
                elif self.agent_pool:
                    agent_id = next(iter(self.agent_pool))
                else:
                    log_event("ENGINE", "step_error", "no agents available", level="error")
                    break
                step["agent_id"] = agent_id
            agent = self.agent_pool[agent_id]
            inbox = self._merge_evidence_inbox(state, step.get("inbox", []))
            instructions = step.get("instructions", "")
            log_event(
                "ENGINE",
                "step_start",
                "running step",
                data={"round": state["round"], "agent_id": agent_id},
            )
            log_event(
                "ENGINE",
                "step_input",
                "input details",
                level="debug",
                data={
                    "agent_id": agent_id,
                    "instructions": instructions[:200],
                    "inbox_len": len(inbox),
                },
            )
            # Inject per-agent memory hints when enabled.
            memory_block = None
            memory = getattr(ctx, "memory", None)
            if memory and memory.enabled:
                recall = memory.recall_agent(agent_id, query=f"{task.goal} {instructions}", k=memory.top_k)
                memory_block = format_memory_block(recall, title=f"Retrieved Memory ({agent_id})")

            mcp_prompt = None
            if mcp_manager and mcp_manager.is_enabled():
                mcp_prompt = await mcp_manager.get_prompt(agent_id)

            prompt = build_task_prompt(
                agent_id,
                task,
                instructions,
                inbox,
                memory_block=memory_block,
                extra_instructions=mcp_prompt,
            )
            if should_show_prompts():
                log_event(
                    "ENGINE",
                    "prompt",
                    "prompt preview",
                    level="debug",
                    data={"agent_id": agent_id, "preview": clip_text(prompt)},
                )
            mcp_servers = []
            required_servers: list[str] = []
            allowed_tools = None
            require_approval = False
            if mcp_manager and mcp_manager.is_enabled():
                required_servers, allowed_tools, require_approval = self._extract_mcp_requirements(step, plan)
            if mcp_manager and mcp_manager.is_enabled() and required_servers:
                try:
                    async with mcp_manager.open_servers(
                        required_servers,
                        allowed_tools=allowed_tools,
                        require_approval=require_approval,
                    ) as mcp_servers:
                        log_event(
                            "ENGINE",
                            "mcp_attach",
                            "attached MCP servers",
                            data={"agent_id": agent_id, "servers": required_servers, "allowed_tools": allowed_tools},
                        )
                        with mcp_manager.bind_agent(agent, mcp_servers, extra_tools=None):
                            result = await run_agent(
                                agent,
                                prompt,
                                ctx,
                                session=session,
                                workflow_name=f"step:{agent_id}",
                                max_turns=plan.max_rounds,
                                hooks=self.run_hooks,
                            )
                except PermissionError as exc:
                    log_event(
                        "ENGINE",
                        "mcp_blocked",
                        "MCP approval required",
                        level="warn",
                        data={"agent_id": agent_id, "error": str(exc)},
                    )
                    result = await run_agent(
                        agent,
                        prompt,
                        ctx,
                        session=session,
                        workflow_name=f"step:{agent_id}",
                        max_turns=plan.max_rounds,
                        hooks=self.run_hooks,
                    )
            else:
                result = await run_agent(
                    agent,
                    prompt,
                    ctx,
                    session=session,
                    workflow_name=f"step:{agent_id}",
                    max_turns=plan.max_rounds,
                    hooks=self.run_hooks,
                )
            output_text = getattr(result, "final_output", "")
            if not isinstance(output_text, str):
                output_text = str(output_text)
            output_msg = Message(
                sender=agent_id,
                receiver="engine",
                content_type="text",
                content=output_text,
            )

            state["messages"].append(output_msg)
            self.obs.event(
                "agent_output",
                {"agent_id": agent_id, "msg": output_msg.model_dump(mode="json")},
            )
            self.obs.event_from_result("agent_usage", result, {"agent_id": agent_id})
            log_event(
                "ENGINE",
                "step_end",
                "step complete",
                data={"agent_id": agent_id, "output_len": len(output_text)},
            )
            if should_show_outputs():
                log_event(
                    "ENGINE",
                    "step_output",
                    "output preview",
                    level="debug",
                    data={"agent_id": agent_id, "preview": clip_text(output_text)},
                )

            if memory and memory.enabled and memory.store_agent_outputs:
                # Persist a short summary of outputs as agent memory.
                memory.write_agent(
                    agent_id,
                    clip_text(output_text),
                    labels=["agent_output"],
                    category="process",
                )

            patch = self.hooks.post_step(task, plan, state, output_msg)
            if patch:
                plan = plan.model_copy(update=patch)
                log_event("ENGINE", "plan_patch", "plan patched", level="warn", data=patch)

            protocol.on_step_end(task, plan, state, protocol_state, output_msg)
            state["round"] += 1

            if protocol.is_done(task, plan, state, protocol_state):
                break

        return state

    async def _run_tool_plan(self, task: TaskSpec, tool_plan: ToolPlan, state: dict, ctx) -> None:
        from utils import clip_text, log_event, log_section, should_show_outputs

        log_section("TOOLS", "Tool Execution")
        docker_runtime = getattr(ctx, "docker_runtime", None) or self.docker_runtime
        if docker_runtime is None:
            log_event("TOOLS", "skip", "docker runtime not available", level="warn")
            return
        try:
            result = docker_runtime.execute(tool_plan, run_id=getattr(ctx, "run_id", "run"))
        except Exception as exc:  # noqa: BLE001 - defensive runtime
            log_event("TOOLS", "error", "tool execution failed", level="error", data={"error": str(exc)})
            self.hooks.on_tool_error(None, None, state, exc)
            return

        if result.status != "success" and result.error_signature == "docker_build_failed":
            result = await self._attempt_docker_repair(task, tool_plan, result, ctx, docker_runtime)
            if not result:
                return
            if result.status != "success":
                log_event("TOOLS", "error", "docker repair attempts exhausted", level="warn")

        if not isinstance(result, ToolExecutionResult):
            log_event("TOOLS", "error", "invalid tool execution result", level="warn")
            return
        state.setdefault("artifacts", {})["tool_execution"] = result
        state.setdefault("artifacts", {})["tool_plan"] = tool_plan

        summary_lines = [
            f"Tool: {tool_plan.selected_tool.name}",
            f"Status: {result.status}",
            f"Artifacts: {', '.join(result.artifacts) if result.artifacts else 'None'}",
        ]
        if result.stderr and result.status != "success":
            summary_lines.append(f"Error: {clip_text(result.stderr)}")
        if result.stdout and result.status == "success":
            summary_lines.append(f"Output: {clip_text(result.stdout)}")
        summary = "\n".join(summary_lines)
        msg = Message(sender="tool_runtime", receiver="engine", content_type="text", content=summary)
        state.setdefault("messages", []).append(msg)
        self.obs.event(
            "tool_execution",
            {"status": result.status, "tool": tool_plan.selected_tool.name},
        )
        if should_show_outputs():
            log_event(
                "TOOLS",
                "output",
                "tool output summary",
                level="debug",
                data={"preview": clip_text(summary)},
            )

    async def _attempt_docker_repair(
        self,
        task: TaskSpec,
        tool_plan: ToolPlan,
        result: ToolExecutionResult,
        ctx,
        docker_runtime,
    ):
        from utils import clip_text, log_event, should_show_prompts

        max_rounds = getattr(self.tool_cfg, "docker_repair_max_rounds", 0) if self.tool_cfg else 0
        if max_rounds <= 0:
            return result
        dockerfile_path = tool_plan.container_spec.dockerfile_path
        if not dockerfile_path:
            return result
        agent = self.agent_pool.get("docker_repair")
        if agent is None:
            return result
        build_error = result.stderr or result.error_signature or ""
        for attempt in range(1, max_rounds + 1):
            try:
                with open(dockerfile_path, "r", encoding="utf-8") as f:
                    dockerfile_text = f.read()
            except OSError:
                dockerfile_text = ""
            prompt = build_docker_repair_prompt(
                task=task,
                tool_plan=tool_plan.model_dump(mode="json"),
                dockerfile_text=dockerfile_text,
                build_error=build_error,
            )
            if should_show_prompts():
                log_event(
                    "TOOLS",
                    "repair_prompt",
                    "docker repair prompt preview",
                    level="debug",
                    data={"preview": clip_text(prompt)},
                )
            repair_result = await run_agent(
                agent,
                prompt,
                ctx,
                session=None,
                workflow_name=f"docker_repair:{attempt}",
                max_turns=4,
                hooks=self.run_hooks,
            )
            dockerfile_fixed = getattr(repair_result, "final_output", "")
            if not isinstance(dockerfile_fixed, str):
                dockerfile_fixed = str(dockerfile_fixed)
            dockerfile_fixed = self._sanitize_dockerfile(dockerfile_fixed)
            if dockerfile_fixed:
                with open(dockerfile_path, "w", encoding="utf-8") as f:
                    f.write(dockerfile_fixed + "\n")
            log_event(
                "TOOLS",
                "repair_attempt",
                "retrying docker build after repair",
                data={"attempt": attempt},
            )
            retry = docker_runtime.execute(tool_plan, run_id=getattr(ctx, "run_id", "run"))
            if retry.status == "success":
                return retry
            build_error = retry.stderr or retry.error_signature or build_error
            result = retry
        return result

    @staticmethod
    def _sanitize_dockerfile(text: str) -> str:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("dockerfile", "").replace("Dockerfile", "")
        return cleaned.strip()

    async def _run_evidence_steps(
        self,
        requests: list[EvidenceRequest],
        state: dict,
        ctx,
        session=None,
        *,
        verbosity: str = "normal",
    ) -> None:
        from utils import clip_text, log_event, should_show_outputs, should_show_prompts

        if "researcher" not in self.agent_pool:
            log_event("ENGINE", "evidence_skip", "researcher agent not available", level="warn")
            return

        evidence_messages: list[Message] = []
        evidence_packs: list[EvidencePack] = []
        for idx, request in enumerate(requests, start=1):
            cache_key = self._evidence_cache_key(request)
            cached = ctx.cache.get(cache_key)
            pack = self._coerce_evidence_pack(request, cached) if cached else None
            if pack:
                log_event("ENGINE", "evidence_cache", "evidence cache hit", data={"query": request.query})
            else:
                # Allow the researcher to leverage its own memory for consistent citations.
                memory_block = None
                memory = getattr(ctx, "memory", None)
                if memory and memory.enabled:
                    recall = memory.recall_agent("researcher", query=request.query, k=memory.top_k)
                    memory_block = format_memory_block(recall, title="Retrieved Memory (researcher)")
                prompt = build_evidence_prompt(request, memory_block=memory_block, verbosity=verbosity)
                if should_show_prompts():
                    log_event(
                        "ENGINE",
                        "evidence_prompt",
                        "evidence prompt preview",
                        level="debug",
                        data={"preview": clip_text(prompt)},
                    )
                log_event("ENGINE", "evidence_start", "running evidence step", data={"index": idx})
                result = await run_agent(
                    self.agent_pool["researcher"],
                    prompt,
                    ctx,
                    session=session,
                    workflow_name=f"evidence:{idx}",
                    max_turns=6,
                    hooks=self.run_hooks,
                )
                self.obs.event_from_result("evidence_usage", result, {"index": idx})
                pack = self._coerce_evidence_pack(request, getattr(result, "final_output", None))
                if pack:
                    ctx.cache.set(cache_key, pack)
                    if memory and memory.enabled and memory.store_agent_outputs:
                        memory.write_agent(
                            "researcher",
                            clip_text(pack.summary),
                            labels=["evidence_summary"],
                            category="process",
                        )
            if not pack:
                continue
            evidence_packs.append(pack)
            evidence_messages.append(self._evidence_to_message(pack))
            if should_show_outputs():
                log_event(
                    "ENGINE",
                    "evidence_output",
                    "evidence summary preview",
                    level="debug",
                    data={"preview": clip_text(pack.summary)},
                )

        if evidence_packs:
            state["artifacts"]["evidence"] = evidence_packs
            state["artifacts"]["evidence_messages"] = evidence_messages
            state["messages"].extend(evidence_messages)

    @staticmethod
    def _evidence_cache_key(request: EvidenceRequest) -> str:
        payload = request.model_dump()
        if payload.get("allowed_domains"):
            payload["allowed_domains"] = sorted(payload["allowed_domains"])
        return f"evidence:{json.dumps(payload, sort_keys=True, ensure_ascii=True)}"

    @staticmethod
    def _coerce_evidence_pack(request: EvidenceRequest, data: object | None) -> EvidencePack | None:
        if data is None:
            return None
        if isinstance(data, EvidencePack):
            return data
        if isinstance(data, dict):
            try:
                return EvidencePack(**data)
            except Exception:
                return None
        return None

    @staticmethod
    def _evidence_to_message(pack: EvidencePack) -> Message:
        citations = []
        for item in pack.citations:
            title = item.title or "Untitled"
            snippet = f" - {item.snippet}" if item.snippet else ""
            citations.append(f"- {title} ({item.url}){snippet}")
        body = "\n".join(
            [
                "Evidence Summary:",
                pack.summary,
                "Citations:",
                "\n".join(citations) if citations else "None",
            ]
        )
        return Message(
            sender="researcher",
            receiver="engine",
            content_type="text",
            content=body,
            meta={"evidence": True, "query": pack.request.query},
        )

    @staticmethod
    def _merge_evidence_inbox(state: dict, inbox: list[Message]) -> list[Message]:
        artifacts = state.get("artifacts") or {}
        evidence_msgs = artifacts.get("evidence_messages") or []
        if not evidence_msgs:
            return inbox
        seen = {msg.msg_id for msg in inbox}
        merged = [msg for msg in evidence_msgs if msg.msg_id not in seen]
        merged.extend(msg for msg in inbox if msg.msg_id not in seen)
        return merged

    @staticmethod
    def _extract_mcp_requirements(step: dict, plan: ExecutionPlan) -> tuple[list[str], list[str] | None, bool]:
        subtask = step.get("subtask")
        if subtask is not None:
            return (
                list(getattr(subtask, "required_mcp_servers", []) or []),
                list(getattr(subtask, "allowed_tools", []) or []),
                bool(getattr(subtask, "require_approval", False)),
            )
        meta = plan.meta.get("mcp", {}) if plan.meta else {}
        return (
            list(meta.get("required_mcp_servers", []) or []),
            list(meta.get("allowed_tools", []) or []),
            bool(meta.get("require_approval", False)),
        )
