from __future__ import annotations

from core.contracts import Message, TaskSpec


def build_system_prompt(role: str) -> str:
    return f"You are a {role} agent. Be concise, correct, and follow the task constraints."


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
        f"Task goal: {task.goal}",
        f"Instructions: {instructions}",
        f"Constraints: {', '.join(task.constraints) if task.constraints else 'None'}",
        f"Success criteria: {', '.join(task.success_criteria) if task.success_criteria else 'None'}",
    ]
    if inbox_text:
        parts.append(f"Context:\n{inbox_text}")
    return "\n".join(parts)
