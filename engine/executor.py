from __future__ import annotations

from core.contracts import ExecutionPlan, Message, TaskSpec
from core.hooks import HookManager
from core.observability import Observability
from core.runtime import run_agent
from engine.protocols import ProtocolBase
from prompts import build_task_prompt


class ExecutionEngine:
    def __init__(
        self,
        agent_pool: dict[str, object],
        protocols: dict[str, ProtocolBase],
        hooks: HookManager,
        observability: Observability,
        run_hooks: object | None = None,
    ) -> None:
        self.agent_pool = agent_pool
        self.protocols = protocols
        self.hooks = hooks
        self.obs = observability
        self.run_hooks = run_hooks

    async def run(self, task: TaskSpec, plan: ExecutionPlan, ctx, session=None) -> dict:
        from utils import clip_text, log_event, log_section, should_show_outputs, should_show_prompts

        log_section("ENGINE", "Execution")
        state: dict = {"messages": [], "artifacts": [], "round": 0}
        protocol = self.protocols[plan.protocol]
        protocol_state = protocol.prepare(task, plan)

        max_steps = max(plan.max_rounds, len(plan.subtasks), 1) * max(len(plan.active_agents), 1)
        log_event("ENGINE", "start", "executor started", data={"protocol": plan.protocol, "max_steps": max_steps})

        while state["round"] < max_steps:
            step = protocol.next_step(task, plan, state, protocol_state)
            if not step:
                break
            step = self.hooks.pre_step(task, plan, state, step)

            agent_id = step["agent_id"]
            if agent_id not in self.agent_pool:
                agent_id = plan.active_agents[0]
                step["agent_id"] = agent_id
            agent = self.agent_pool[agent_id]
            inbox = step.get("inbox", [])
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
            prompt = build_task_prompt(agent_id, task, instructions, inbox)
            if should_show_prompts():
                log_event(
                    "ENGINE",
                    "prompt",
                    "prompt preview",
                    level="debug",
                    data={"agent_id": agent_id, "preview": clip_text(prompt)},
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

            patch = self.hooks.post_step(task, plan, state, output_msg)
            if patch:
                plan = plan.model_copy(update=patch)
                log_event("ENGINE", "plan_patch", "plan patched", level="warn", data=patch)

            protocol.on_step_end(task, plan, state, protocol_state, output_msg)
            state["round"] += 1

            if protocol.is_done(task, plan, state, protocol_state):
                break

        return state
