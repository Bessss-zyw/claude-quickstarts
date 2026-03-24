"""Coordinator brain: Opus API for planning and evaluation."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from openai import OpenAI

if TYPE_CHECKING:
    from config import AgentDef, HarnessConfig

logger = logging.getLogger(__name__)


class Planner:
    """Use Opus API to make coordinator decisions (plan, evaluate, instruct)."""

    def __init__(self, config: "HarnessConfig") -> None:
        self.config = config
        self.model = config.coordinator_model

        kwargs: dict = {}
        if config.coordinator_base_url:
            kwargs["base_url"] = config.coordinator_base_url
        if config.coordinator_api_key:
            kwargs["api_key"] = config.coordinator_api_key

        self._client = OpenAI(**kwargs)
        self._total_prompt = 0
        self._total_completion = 0

    def _call(self, system: str, user: str, temperature: float = 0.0) -> str:
        """Make a single Opus API call. Returns the text response."""
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=2000,
            )
            usage = getattr(resp, "usage", None)
            if usage:
                self._total_prompt += getattr(usage, "prompt_tokens", 0)
                self._total_completion += getattr(usage, "completion_tokens", 0)
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.error("Opus API call failed: %s", e)
            return f'{{"error": "{e}"}}'

    # ── Planning ───────────────────────────────────────────────────────

    def create_plan(self, agents: dict[str, "AgentDef"]) -> list[dict]:
        """Generate initial execution plan."""
        agent_desc = "\n".join(
            f"  - {name}: {a.role[:200]}" for name, a in agents.items()
        )
        deliverable_desc = "\n".join(
            f"  - {d['path']}: {d.get('description', '')}"
            for d in self.config.deliverables
        )

        system = """You are a project coordinator AI. Given a task and a team of specialist agents,
create an ordered execution plan. Each step should be assigned to exactly one specialist.

Output ONLY valid JSON — an array of step objects:
[
  {"description": "what to do", "assigned_to": "agent_name", "depends_on": []},
  ...
]

Keep steps concrete and actionable (not vague). 5-15 steps is typical.
depends_on is a list of step indices (0-based) that must complete before this step."""

        user = f"""Task: {self.config.task_name}

Goal:
{self.config.task_goal}

Context:
{self.config.task_context}

Deliverables:
{deliverable_desc}

Available Specialists:
{agent_desc}

Create the execution plan as JSON."""

        raw = self._call(system, user)

        # Extract JSON from response
        try:
            # Try to find JSON array in the response
            start = raw.index("[")
            end = raw.rindex("]") + 1
            steps = json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            logger.error("Failed to parse plan from Opus response: %s", raw[:500])
            steps = [{"description": "Execute the task as described in the goal.",
                       "assigned_to": list(agents.keys())[0], "depends_on": []}]

        # Normalize steps
        plan_steps = []
        for i, s in enumerate(steps):
            plan_steps.append({
                "id": i,
                "description": s.get("description", ""),
                "assigned_to": s.get("assigned_to", ""),
                "depends_on": s.get("depends_on", []),
                "status": "pending",
                "result": "",
            })
        return plan_steps

    # ── Evaluation ─────────────────────────────────────────────────────

    def evaluate_and_replan(
        self, plan_steps: list[dict], agents: dict[str, "AgentDef"]
    ) -> dict:
        """Evaluate completed steps and decide next actions."""
        plan_text = "\n".join(
            f"  Step {s['id']} [{s['status']}] ({s['assigned_to']}): {s['description']}"
            + (f"\n    Result: {s['result'][:300]}" if s.get('result') else "")
            for s in plan_steps
        )
        agent_names = list(agents.keys())

        system = """You are a project coordinator AI. Review the current plan execution state
and decide what to do next.

Output ONLY valid JSON:
{
  "next_instructions": {"agent_name": "specific instruction text", ...},
  "plan_updates": [{"step_id": N, "status": "done|failed", "findings": "..."}, ...],
  "is_complete": false,
  "reasoning": "brief explanation"
}

Rules:
- Only dispatch steps whose dependencies are all "done"
- If a step failed, decide whether to retry or skip
- Set is_complete=true only when ALL deliverables should exist
- Instructions must be specific and actionable (not vague)"""

        user = f"""Task: {self.config.task_name}
Goal: {self.config.task_goal}

Current Plan State:
{plan_text}

Available agents: {agent_names}

What should happen next?"""

        raw = self._call(system, user)

        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            result = json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError):
            logger.error("Failed to parse evaluation: %s", raw[:500])
            result = {"next_instructions": {}, "plan_updates": [],
                       "is_complete": False, "reasoning": "parse_error"}

        return result

    # ── Instruction Generation ─────────────────────────────────────────

    def generate_instruction(
        self, agent: "AgentDef", step_desc: str, pane_context: str
    ) -> str:
        """Generate a specific instruction for a specialist CC instance."""
        system = f"""You are supervising a Claude Code agent named "{agent.name}".
Role: {agent.role}

Generate ONE clear, actionable instruction for this agent. Be specific about:
- What to do
- What files to read/create/modify
- What commands to run
- What output to produce

Output ONLY the instruction text. No preamble, no explanation."""

        user = f"""Task step: {step_desc}

Recent agent output (last screen):
{pane_context[-2000:]}

Generate the instruction."""

        return self._call(system, user, temperature=0.1).strip()

    # ── Token tracking ─────────────────────────────────────────────────

    def get_token_usage(self) -> dict:
        return {
            "prompt_tokens": self._total_prompt,
            "completion_tokens": self._total_completion,
        }
