"""Coordinator-only messaging tools: assign tasks and check results."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tools import register_tool

if TYPE_CHECKING:
    from config import HarnessConfig
    from message_bus import MessageBus

_CONFIG: HarnessConfig | None = None
_MESSAGE_BUS: MessageBus | None = None  # injected by ToolRegistry

# We also need a registry reference to validate agent names.
# This is set by coordinator.py before the loop starts.
_AGENT_NAMES: list[str] = []


def set_agent_names(names: list[str]) -> None:
    global _AGENT_NAMES
    _AGENT_NAMES = names


# ── assign_task ────────────────────────────────────────────────────────────

def assign_task(agent: str, task_description: str) -> str:
    assert _MESSAGE_BUS is not None
    if _AGENT_NAMES and agent not in _AGENT_NAMES:
        return f"Error: '{agent}' is not a registered specialist. Available: {_AGENT_NAMES}"
    if agent == "coordinator":
        return "Error: cannot assign task to the coordinator."
    task_id = _MESSAGE_BUS.create_task(from_agent="coordinator", to_agent=agent, content=task_description)
    return f"Assigned task {task_id} to {agent}"


register_tool(
    name="assign_task",
    description="Assign a task to a specialist agent.",
    parameters={
        "type": "object",
        "properties": {
            "agent": {"type": "string", "description": "Target specialist agent name."},
            "task_description": {"type": "string", "description": "Detailed task description."},
        },
        "required": ["agent", "task_description"],
    },
    fn=assign_task,
)

# ── check_result ───────────────────────────────────────────────────────────

def check_result(task_id: str) -> str:
    assert _MESSAGE_BUS is not None
    try:
        msg = _MESSAGE_BUS.get_task(task_id)
    except FileNotFoundError:
        return f"Error: task {task_id} not found."

    content_preview = msg["content"][:200]
    result = msg.get("result") or "pending"
    return (
        f"Task {msg['id']} [{msg['status']}] -> {msg['to']}:\n"
        f"  Content: {content_preview}\n"
        f"  Result: {result}"
    )


register_tool(
    name="check_result",
    description="Check the result of a specific task.",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "The task ID (e.g. msg_001)."},
        },
        "required": ["task_id"],
    },
    fn=check_result,
)

# ── check_all_results ─────────────────────────────────────────────────────

def check_all_results() -> str:
    assert _MESSAGE_BUS is not None
    msgs = _MESSAGE_BUS.get_all_tasks()
    if not msgs:
        return "No tasks have been assigned yet."
    lines: list[str] = []
    for msg in msgs:
        result_preview = (msg.get("result") or "pending")[:300]
        lines.append(
            f"  {msg['id']} [{msg['status']}] -> {msg['to']}: "
            f"{msg['content'][:100]}... | Result: {result_preview}"
        )
    return "All tasks:\n" + "\n".join(lines)


register_tool(
    name="check_all_results",
    description="Check status and results of all assigned tasks.",
    parameters={"type": "object", "properties": {}},
    fn=check_all_results,
)
