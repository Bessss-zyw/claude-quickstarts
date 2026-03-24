"""Planning tools: create, update, and read the execution plan."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from core.tools import register_tool

if TYPE_CHECKING:
    from config import HarnessConfig

_CONFIG: HarnessConfig | None = None  # injected by ToolRegistry


def _plan_path() -> str:
    assert _CONFIG is not None
    return _CONFIG.plan_file


# ── create_plan ────────────────────────────────────────────────────────────

def create_plan(steps: list[dict]) -> str:
    """Create a new execution plan. Fails if one already exists."""
    path = _plan_path()
    if os.path.exists(path):
        return "Error: plan already exists. Use update_plan to modify it."

    plan_steps = []
    for i, s in enumerate(steps, 1):
        plan_steps.append({
            "id": i,
            "description": s.get("description", ""),
            "assigned_to": s.get("assigned_to", ""),
            "status": "pending",
            "findings": "",
        })

    plan = {
        "steps": plan_steps,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(plan, f, indent=2)
    return f"Created plan with {len(plan_steps)} steps"


def _parse_steps(steps):
    """Accept steps as list[dict] or JSON string."""
    if isinstance(steps, str):
        steps = json.loads(steps)
    return steps


def create_plan_wrapper(steps) -> str:
    return create_plan(_parse_steps(steps))


register_tool(
    name="create_plan",
    description="Create a new execution plan with ordered steps.",
    parameters={
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string"},
                        "assigned_to": {"type": "string"},
                    },
                    "required": ["description", "assigned_to"],
                },
                "description": "List of plan steps.",
            },
        },
        "required": ["steps"],
    },
    fn=create_plan_wrapper,
)

# ── update_plan ────────────────────────────────────────────────────────────

def update_plan(step_id: int, status: str | None = None, findings: str | None = None) -> str:
    path = _plan_path()
    if not os.path.exists(path):
        return "Error: no plan exists yet."
    with open(path) as f:
        plan = json.load(f)

    step_id = int(step_id)
    target = None
    for s in plan["steps"]:
        if s["id"] == step_id:
            target = s
            break
    if target is None:
        return f"Error: step {step_id} not found."

    if status is not None:
        target["status"] = status
    if findings is not None:
        target["findings"] = findings
    plan["updated_at"] = datetime.now(timezone.utc).isoformat()

    with open(path, "w") as f:
        json.dump(plan, f, indent=2)
    return f"Updated step {step_id}: status={target['status']}"


register_tool(
    name="update_plan",
    description="Update a plan step's status and/or findings.",
    parameters={
        "type": "object",
        "properties": {
            "step_id": {"type": "integer", "description": "Step ID to update."},
            "status": {"type": "string", "description": "New status (pending|in_progress|done|failed)."},
            "findings": {"type": "string", "description": "Findings or conclusions."},
        },
        "required": ["step_id"],
    },
    fn=update_plan,
)

# ── get_plan ───────────────────────────────────────────────────────────────

def get_plan() -> str:
    path = _plan_path()
    if not os.path.exists(path):
        return "No plan exists yet."
    with open(path) as f:
        plan = json.load(f)

    lines: list[str] = []
    for s in plan["steps"]:
        findings = s.get("findings") or "(none)"
        lines.append(f"Step {s['id']} [{s['status']}] ({s['assigned_to']}): {s['description']}")
        lines.append(f"   Findings: {findings}")
    return "\n".join(lines)


register_tool(
    name="get_plan",
    description="Read the current execution plan.",
    parameters={"type": "object", "properties": {}},
    fn=get_plan,
)
