from __future__ import annotations

from core.contracts import AgentSpec, Message, TaskSpec


def build_system_prompt(role: str) -> str:
    return (
        f"You are a {role} agent. "
        "Follow instructions in order: system > task > constraints > success criteria. "
        "If instructions conflict, pick the higher-priority instruction and note the conflict briefly."
    )


def format_inbox(inbox: list[Message]) -> str:
    if not inbox:
        return ""
    lines = []
    for msg in inbox:
        lines.append(f"[{msg.sender}] {msg.content}")
    return "\n".join(lines)


def build_task_prompt(role: str, task: TaskSpec, instructions: str, inbox: list[Message]) -> str:
    inbox_text = format_inbox(inbox)
    parts = [
        "### Task",
        f"Goal: {task.goal}",
        f"Instructions: {instructions}",
        f"Constraints: {', '.join(task.constraints) if task.constraints else 'None'}",
        f"Success criteria: {', '.join(task.success_criteria) if task.success_criteria else 'None'}",
        "### Execution",
        "Depth: focus only on required items; avoid tangents.",
        "Stop when the deliverable is complete; do not ask clarifying questions.",
        "If assumptions are needed, state them briefly.",
    ]
    if inbox_text:
        parts.append(f"### Context\n{inbox_text}")
    return "\n".join(parts)


def build_plan_prompt(task: TaskSpec, candidates: list[AgentSpec]) -> str:
    agent_lines = []
    for spec in candidates:
        agent_lines.append(
            f"- {spec.agent_id}: caps={spec.capabilities}, inputs={spec.input_types}, outputs={spec.output_types}"
        )
    agent_text = "\n".join(agent_lines) if agent_lines else "None"
    return "\n".join(
        [
            "### Planner Instructions",
            "Produce an ExecutionPlan JSON only. Do not include extra text.",
            "Choose a protocol from: pipeline, roundtable, debate, loop, hybrid.",
            "Use only the agent IDs listed below in active_agents and subtasks.",
            "Prefer minimal, executable steps; avoid redundant subtasks.",
            "### Task",
            f"Goal: {task.goal}",
            f"Constraints: {', '.join(task.constraints) if task.constraints else 'None'}",
            f"Success criteria: {', '.join(task.success_criteria) if task.success_criteria else 'None'}",
            f"Budget tokens: {task.budget_tokens}",
            f"Input modalities: {', '.join(task.input_modalities)}",
            f"Output modalities: {', '.join(task.output_modalities)}",
            "### Available agents",
            agent_text,
        ]
    )


def build_judge_prompt(task: TaskSpec, plan, messages: list[Message]) -> str:
    transcript = format_inbox(messages)
    return "\n".join(
        [
            "### Judge Instructions",
            "Return JudgeReport JSON only. No extra commentary.",
            "Be strict: check success criteria and constraints; list gaps explicitly.",
            "### Task",
            f"Goal: {task.goal}",
            f"Constraints: {', '.join(task.constraints) if task.constraints else 'None'}",
            f"Success criteria: {', '.join(task.success_criteria) if task.success_criteria else 'None'}",
            f"Protocol: {getattr(plan, 'protocol', 'unknown')}",
            "### Agent outputs",
            transcript or "None",
        ]
    )


def build_aggregate_prompt(task: TaskSpec, plan, messages: list[Message]) -> str:
    transcript = format_inbox(messages)
    return "\n".join(
        [
            "### Aggregator Instructions",
            "Synthesize a final response aligned with constraints and success criteria.",
            "Resolve conflicts; if uncertainty remains, add a short Notes section.",
            "### Task",
            f"Goal: {task.goal}",
            f"Constraints: {', '.join(task.constraints) if task.constraints else 'None'}",
            f"Success criteria: {', '.join(task.success_criteria) if task.success_criteria else 'None'}",
            "### Agent outputs",
            transcript or "None",
        ]
    )
