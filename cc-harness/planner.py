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
    """Use Opus API to make coordinator decisions (plan, evaluate, instruct).

    Maintains a conversation history so the coordinator retains memory across
    planning, evaluation, and instruction calls within a single harness run.
    This lets it remember its own reasoning, past decisions, and specialist
    results instead of starting from scratch each time.
    """

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

        # Persistent conversation history for the coordinator.
        # The system message is set once; subsequent calls append user/assistant turns.
        self._history: list[dict] = []
        self._system_msg: str = ""
        # Max history turns to keep (user+assistant = 1 turn). Oldest turns
        # are dropped when exceeded to stay within context window limits.
        self._max_history_turns: int = 40  # ~20 round-trips

    @staticmethod
    def _strip_code_fences(text: str) -> str:
        """Strip markdown code fences (```json ... ```) from LLM output."""
        import re
        stripped = re.sub(r"```\w*\s*\n?", "", text)
        return stripped.strip()

    def _call(self, system: str, user: str, temperature: float = 0.0) -> str:
        """Make an Opus API call, appending to conversation history.

        The first call sets the system message. Subsequent calls reuse the
        same system message and accumulate user/assistant turns.
        """
        # Set system message on first call; update if changed (different phase)
        if not self._system_msg:
            self._system_msg = system
        elif system != self._system_msg:
            # New phase (e.g. plan → evaluate → instruct): update system msg
            self._system_msg = system

        # Append the new user turn
        self._history.append({"role": "user", "content": user})

        # Truncate history if too long (keep most recent turns)
        if len(self._history) > self._max_history_turns:
            # Keep first 2 messages (initial plan context) + latest turns
            keep_first = 2
            keep_last = self._max_history_turns - keep_first
            trimmed = self._history[:keep_first] + self._history[-keep_last:]
            logger.info("Trimming coordinator history: %d → %d messages",
                        len(self._history), len(trimmed))
            self._history = trimmed

        # Build full messages: system + history
        messages = [{"role": "system", "content": self._system_msg}] + self._history

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=4096,
            )
            usage = getattr(resp, "usage", None)
            if usage:
                self._total_prompt += getattr(usage, "prompt_tokens", 0)
                self._total_completion += getattr(usage, "completion_tokens", 0)
            reply = resp.choices[0].message.content or ""

            # Append assistant reply to history
            self._history.append({"role": "assistant", "content": reply})

            return reply
        except Exception as e:
            logger.error("Opus API call failed: %s", e)
            error_msg = json.dumps({"error": str(e)})
            # Still append to history so context stays consistent
            self._history.append({"role": "assistant", "content": error_msg})
            return error_msg

    def _call_stateless(self, system: str, user: str, temperature: float = 0.0) -> str:
        """Make a one-shot API call WITHOUT affecting conversation history.

        Use for side-channel calls like permission review or instruction
        generation that shouldn't pollute the coordinator's main context.
        """
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temperature,
                max_tokens=4096,
            )
            usage = getattr(resp, "usage", None)
            if usage:
                self._total_prompt += getattr(usage, "prompt_tokens", 0)
                self._total_completion += getattr(usage, "completion_tokens", 0)
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.error("Opus API call failed: %s", e)
            return json.dumps({"error": str(e)})

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
plan ONLY the immediate next phase of work (2-4 concrete steps).

DO NOT plan the entire task upfront. You will be called again after these steps
complete, and you can plan the next phase based on actual results.

Output ONLY valid JSON — an array of step objects:
[
  {"description": "what to do", "assigned_to": "agent_name", "depends_on": []},
  ...
]

Rules:
- Plan only 2-4 steps for the current phase
- Steps must be concrete and actionable
- depends_on is a list of step indices (0-based) that must complete before this step
- You will see results from completed steps in future calls and can plan accordingly
- Do NOT include speculative steps like "if tests fail, fix them" — wait for actual results"""

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

        # Extract JSON from response (strip code fences first)
        raw = self._strip_code_fences(raw)
        try:
            start = raw.index("[")
            end = raw.rindex("]") + 1
            steps = json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError) as e:
            logger.error("Failed to parse plan: %s | Response (last 200): ...%s", e, raw[-200:])
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
        import os

        # Build plan text with step reports as primary info source
        plan_parts = []
        for s in plan_steps:
            part = f"  Step {s['id']} [{s['status']}] ({s['assigned_to']}): {s['description']}"
            # Prefer step report file over pane-extracted findings
            report_path = self.config.report_path(s['id'])
            if os.path.isfile(report_path):
                try:
                    with open(report_path, encoding="utf-8") as f:
                        report_content = f.read()
                    # Use report content (truncate to keep prompt manageable)
                    part += f"\n    [Step Report ({len(report_content)} chars)]:\n{report_content[:3000]}"
                except Exception as e:
                    logger.warning("Failed to read report for step %d: %s", s['id'], e)
                    if s.get('findings'):
                        part += f"\n    Findings (pane-extracted): {s['findings'][:1500]}"
            elif s.get('findings'):
                part += f"\n    Findings (pane-extracted, NO step report written): {s['findings'][:1500]}"
            plan_parts.append(part)
        plan_text = "\n".join(plan_parts)
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
- Review completed step findings/reports carefully
- If findings indicate failure, update that step's status to "failed"
- Add new_steps for the NEXT phase of work (2-4 steps) based on actual results
  - Do NOT plan far ahead — only plan what logically follows from current results
  - If code was changed, the next step should be testing
  - If tests failed, the next step should be fixing, then re-testing
  - If code was changed by code agent, review agent should verify the changes
- Set is_complete=true ONLY when ALL deliverables exist on filesystem AND
  findings/reports confirm successful execution with no outstanding issues
- Instructions must be specific and actionable
- new_steps format: [{"description": "...", "assigned_to": "agent_name", "depends_on": [step_ids]}]
  - depends_on is a list of step IDs (integers) that must complete before this step can start
  - Use depends_on to enforce ordering (e.g. test step depends on code step)
  - If the step has no dependencies, use an empty list: "depends_on": []"""

        # Check deliverable existence on disk so coordinator has ground truth
        import os
        deliverable_status = []
        for d in self.config.deliverables:
            path = os.path.join(self.config.output_path, d["path"])
            exists = os.path.isfile(path)
            size = os.path.getsize(path) if exists else 0
            deliverable_status.append(
                f"  - {d['path']}: {'EXISTS (' + str(size) + ' bytes)' if exists else 'MISSING'}"
            )
        deliverable_text = "\n".join(deliverable_status) if deliverable_status else "  (none defined)"

        user = f"""Task: {self.config.task_name}
Goal: {self.config.task_goal}

Current Plan State:
{plan_text}

Deliverable Files (ground truth from filesystem):
{deliverable_text}

Available agents: {agent_names}

What should happen next?"""

        raw = self._call(system, user)
        raw = self._strip_code_fences(raw)

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
        self,
        agent: "AgentDef",
        step_desc: str,
        pane_context: str,
        *,
        output_path: str = "",
        prior_findings: str = "",
        report_path: str = "",
    ) -> str:
        """Generate a specific instruction for a specialist CC instance."""
        path_info = ""
        if output_path:
            path_info = f"""
The agent's project directory (where CC runs): {agent.project_dir}
Output directory (ABSOLUTE path): {output_path}
IMPORTANT: All deliverable files MUST be saved using the ABSOLUTE output path above.
For example, to save "result.md", write it to "{output_path}/result.md".
Do NOT use relative paths like "output/result.md" — the output directory is NOT
inside the agent's project directory."""

        report_info = ""
        if report_path:
            report_info = f"""

MANDATORY STEP REPORT: When the task is complete, the agent MUST write a step report to:
  {report_path}
The report must be honest and comprehensive. Include:
- What was done (actions taken, files read/modified, commands run)
- Key results (test pass/fail counts, performance numbers, error messages)
- Files created or modified (with absolute paths)
- Any issues encountered and how they were resolved
- Conclusion (success/failure, what the next step should do)
This report is how the coordinator understands your work. If you don't write it,
the coordinator cannot evaluate progress or make correct decisions.
WRITE THE REPORT AS YOUR VERY LAST ACTION before going idle. This is not optional."""

        prior_section = ""
        if prior_findings:
            prior_section = f"""

Results from prior steps (use these to inform your instruction):
{prior_findings[:1500]}"""

        # Role-specific rules
        role_rules = ""
        agent_lower = agent.name.lower()
        if "code" in agent_lower or "coder" in agent_lower:
            role_rules = """
ROLE RULES (code agent):
- You do NOT have a GPU or test environment. NEVER run pytest, tests, or benchmarks.
- Edit source files IN-PLACE in the project directory. Do NOT save code files to the output directory.
- The output directory is ONLY for reports and summaries, not for code or diffs.
- After editing, verify syntax with 'python -c "import ast; ast.parse(open(FILE).read())"' only."""
        elif "test" in agent_lower or "tester" in agent_lower:
            role_rules = """
ROLE RULES (test agent):
- You are the ONLY agent with access to GPU test environments.
- All test execution happens on remote GPU nodes via SSH.
- Report test results clearly: each test PASS/FAIL, perf numbers with ratios."""
        elif "review" in agent_lower or "reviewer" in agent_lower:
            role_rules = """
ROLE RULES (review agent):
- You read and analyze code. You do NOT modify source files.
- Produce detailed comparison reports and change recommendations.
- Save reports to the output directory using absolute paths."""

        system = f"""You are supervising a Claude Code agent named "{agent.name}".
Role: {agent.role}
{path_info}{report_info}{role_rules}
Generate ONE clear, actionable instruction for this agent. Be specific about:
- What to do
- What files to read/create/modify (use correct paths!)
- What commands to run
- What output to produce
- MUST include the step report requirement (write report to the specified path when done)

Output ONLY the instruction text. No preamble, no explanation."""

        user = f"""Task step: {step_desc}
{prior_section}

Recent agent output (last screen):
{pane_context[-2000:]}

Generate the instruction."""

        return self._call(system, user, temperature=0.1).strip()

    # ── Permission review ────────────────────────────────────────────────

    def review_permission(
        self, agent_name: str, step_desc: str, prompt_text: str
    ) -> bool:
        """Ask the coordinator whether a dangerous permission should be allowed.

        Returns True to approve, False to reject.
        """
        system = """You are a security reviewer for an automated coding system.
A specialist agent is requesting permission to perform a potentially dangerous
operation. Based on the task context, decide whether to ALLOW or DENY.

Output ONLY valid JSON: {"allow": true/false, "reason": "brief explanation"}

Guidelines:
- ALLOW if the operation is clearly needed for the task (e.g. removing a temp
  file the agent created, cleaning build artifacts)
- DENY if the operation could cause data loss, affect files outside the
  working directory, or is disproportionate to the task
- When in doubt, DENY"""

        user = f"""Agent: {agent_name}
Current task step: {step_desc}

Permission prompt:
{prompt_text[:1000]}

Should this be allowed?"""

        raw = self._call_stateless(system, user, temperature=0.0)
        raw = self._strip_code_fences(raw)

        try:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            result = json.loads(raw[start:end])
            return bool(result.get("allow", False))
        except (ValueError, json.JSONDecodeError):
            logger.warning("Failed to parse permission review response: %s", raw[:200])
            return False  # deny on parse failure

    # ── Token tracking ─────────────────────────────────────────────────

    def get_token_usage(self) -> dict:
        return {
            "prompt_tokens": self._total_prompt,
            "completion_tokens": self._total_completion,
        }

    @property
    def token_usage(self) -> dict:
        return self.get_token_usage()

    def save_token_usage(self) -> None:
        """Persist accumulated token usage to disk."""
        import os
        os.makedirs(os.path.dirname(self.config.token_usage_file), exist_ok=True)
        with open(self.config.token_usage_file, "w", encoding="utf-8") as f:
            json.dump(self.get_token_usage(), f, indent=2)
