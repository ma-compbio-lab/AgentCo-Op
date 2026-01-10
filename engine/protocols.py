from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.contracts import ExecutionPlan, TaskSpec


class ProtocolBase(ABC):
    name = "base"

    def prepare(self, task: TaskSpec, plan: ExecutionPlan) -> dict[str, Any]:
        return {}

    @abstractmethod
    def next_step(
        self, task: TaskSpec, plan: ExecutionPlan, state: dict, protocol_state: dict[str, Any]
    ) -> dict[str, Any] | None:
        ...

    def on_step_end(
        self,
        task: TaskSpec,
        plan: ExecutionPlan,
        state: dict,
        protocol_state: dict[str, Any],
        output_msg,
    ) -> None:
        return None

    @abstractmethod
    def is_done(
        self, task: TaskSpec, plan: ExecutionPlan, state: dict, protocol_state: dict[str, Any]
    ) -> bool:
        ...


class PipelineProtocol(ProtocolBase):
    name = "pipeline"

    def prepare(self, task: TaskSpec, plan: ExecutionPlan) -> dict[str, Any]:
        return {"index": 0}

    def next_step(self, task, plan, state, protocol_state):
        idx = protocol_state["index"]
        if idx >= len(plan.subtasks):
            return None
        subtask = plan.subtasks[idx]
        return {
            "agent_id": subtask.assigned_to,
            "instructions": subtask.instructions,
            "inbox": state["messages"][-1:],
            "subtask": subtask,
        }

    def on_step_end(self, task, plan, state, protocol_state, output_msg):
        protocol_state["index"] += 1

    def is_done(self, task, plan, state, protocol_state):
        return protocol_state["index"] >= len(plan.subtasks)


class RoundtableProtocol(ProtocolBase):
    name = "roundtable"

    def prepare(self, task: TaskSpec, plan: ExecutionPlan) -> dict[str, Any]:
        return {"turn": 0}

    def next_step(self, task, plan, state, protocol_state):
        if not plan.active_agents:
            return None
        agent_id = plan.active_agents[protocol_state["turn"] % len(plan.active_agents)]
        return {
            "agent_id": agent_id,
            "instructions": task.goal,
            "inbox": state["messages"],
        }

    def on_step_end(self, task, plan, state, protocol_state, output_msg):
        protocol_state["turn"] += 1

    def is_done(self, task, plan, state, protocol_state):
        if not plan.active_agents:
            return True
        rounds = protocol_state["turn"] // len(plan.active_agents)
        return rounds >= plan.max_rounds


class DebateProtocol(ProtocolBase):
    name = "debate"

    def prepare(self, task: TaskSpec, plan: ExecutionPlan) -> dict[str, Any]:
        return {"turn": 0}

    def next_step(self, task, plan, state, protocol_state):
        if len(plan.active_agents) < 2:
            return None
        agent_id = plan.active_agents[protocol_state["turn"] % 2]
        stance = "pro" if protocol_state["turn"] % 2 == 0 else "con"
        instructions = f"{task.goal}\nProvide a {stance} argument."
        return {
            "agent_id": agent_id,
            "instructions": instructions,
            "inbox": state["messages"],
        }

    def on_step_end(self, task, plan, state, protocol_state, output_msg):
        protocol_state["turn"] += 1

    def is_done(self, task, plan, state, protocol_state):
        rounds = protocol_state["turn"] // 2
        return rounds >= plan.max_rounds


class LoopProtocol(ProtocolBase):
    name = "loop"

    def prepare(self, task: TaskSpec, plan: ExecutionPlan) -> dict[str, Any]:
        return {"turn": 0}

    def next_step(self, task, plan, state, protocol_state):
        if not plan.active_agents:
            return None
        agent_id = plan.active_agents[0]
        if protocol_state["turn"] == 0:
            instructions = task.goal
        else:
            instructions = "Refine the previous answer with corrections and improvements."
        return {
            "agent_id": agent_id,
            "instructions": instructions,
            "inbox": state["messages"][-1:],
        }

    def on_step_end(self, task, plan, state, protocol_state, output_msg):
        protocol_state["turn"] += 1

    def is_done(self, task, plan, state, protocol_state):
        return protocol_state["turn"] >= plan.max_rounds


class HybridProtocol(ProtocolBase):
    name = "hybrid"

    def prepare(self, task: TaskSpec, plan: ExecutionPlan) -> dict[str, Any]:
        return {"phase": "pipeline", "pipeline_state": {"index": 0}, "turn": 0}

    def next_step(self, task, plan, state, protocol_state):
        if plan.subtasks and protocol_state["phase"] == "pipeline":
            idx = protocol_state["pipeline_state"]["index"]
            if idx < len(plan.subtasks):
                subtask = plan.subtasks[idx]
                return {
                    "agent_id": subtask.assigned_to,
                    "instructions": subtask.instructions,
                    "inbox": state["messages"][-1:],
                    "subtask": subtask,
                }
            protocol_state["phase"] = "roundtable"
        if not plan.active_agents:
            return None
        agent_id = plan.active_agents[protocol_state["turn"] % len(plan.active_agents)]
        return {
            "agent_id": agent_id,
            "instructions": task.goal,
            "inbox": state["messages"],
        }

    def on_step_end(self, task, plan, state, protocol_state, output_msg):
        if protocol_state["phase"] == "pipeline":
            protocol_state["pipeline_state"]["index"] += 1
        else:
            protocol_state["turn"] += 1

    def is_done(self, task, plan, state, protocol_state):
        if protocol_state["phase"] == "pipeline" and plan.subtasks:
            if protocol_state["pipeline_state"]["index"] < len(plan.subtasks):
                return False
            protocol_state["phase"] = "roundtable"
        if not plan.active_agents:
            return True
        rounds = protocol_state["turn"] // len(plan.active_agents)
        return rounds >= plan.max_rounds


def default_protocols() -> dict[str, ProtocolBase]:
    return {
        "pipeline": PipelineProtocol(),
        "roundtable": RoundtableProtocol(),
        "debate": DebateProtocol(),
        "loop": LoopProtocol(),
        "hybrid": HybridProtocol(),
    }
