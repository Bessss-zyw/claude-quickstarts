"""Coordinator main loop — orchestrates the multi-agent workflow."""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING

from core.agent_runner import AgentRunner, AgentResult
from core.client import LLMClient
from core.message_bus import MessageBus
from core.middleware.token_tracker import TokenTracker
from core.tools import ToolRegistry
from core.tools.journal import compress_journal_if_needed, read_journal
from core.tools.messaging import set_agent_names

if TYPE_CHECKING:
    from core.agent_registry import AgentRegistry
    from core.config import HarnessConfig

logger = logging.getLogger(__name__)


class CoordinatorLoop:
    """Top-level loop driving the entire task execution."""

    def __init__(self, config: "HarnessConfig", registry: "AgentRegistry") -> None:
        self.config = config
        self.registry = registry

        self.message_bus = MessageBus(config.messages_dir)
        self.token_tracker = TokenTracker(config.token_usage_file)
        self.tool_registry = ToolRegistry(config, self.message_bus)

        # Let messaging tools know valid specialist names
        specialist_names = [a.name for a in registry.get_specialists()]
        set_agent_names(specialist_names)

    def run(self) -> None:
        """Main entry point."""
        iteration = 0
        while True:
            iteration += 1
            logger.info("=== Coordinator iteration %d ===", iteration)

            # ── Phase 1: Run coordinator session ───────────────────────
            coord_def = self.registry.get_coordinator()
            coord_client = LLMClient(coord_def.model_provider, coord_def.model_name)

            # Compress journal if needed
            compress_journal_if_needed("coordinator", coord_client)

            system_prompt = self.build_coordinator_system_prompt()
            user_message = self.build_coordinator_user_message(iteration)

            runner = AgentRunner(
                agent_def=coord_def,
                client=coord_client,
                tool_registry=self.tool_registry,
                config=self.config,
                token_tracker=self.token_tracker,
            )
            result = runner.run(system_prompt, user_message)
            logger.info("Coordinator result: %d tool calls, error=%s",
                        result.tool_calls_count, result.error)

            # ── Phase 2: Collect pending tasks ─────────────────────────
            pending = self.message_bus.get_pending_tasks()
            if not pending:
                if self.is_all_done():
                    logger.info("All plan steps done. Exiting.")
                    break
                if "TASK_COMPLETE" in result.final_text:
                    logger.info("Coordinator signalled TASK_COMPLETE.")
                    break
                logger.warning("No pending tasks and plan not done. Continuing loop.")
            else:
                # ── Phase 3: Execute specialist agents ─────────────────
                for task_msg in pending:
                    agent_name = task_msg["to"]
                    task_desc = task_msg["content"]
                    logger.info("Dispatching task %s to %s", task_msg["id"], agent_name)

                    try:
                        agent_def = self.registry.get_agent(agent_name)
                    except KeyError:
                        logger.error("Unknown agent %s, skipping task %s", agent_name, task_msg["id"])
                        self.message_bus.complete_task(task_msg["id"], f"Error: unknown agent '{agent_name}'")
                        continue

                    agent_client = LLMClient(agent_def.model_provider, agent_def.model_name)

                    # Compress specialist journal if needed
                    compress_journal_if_needed(agent_name, agent_client)

                    # Ensure workspace dir
                    ws = os.path.join(self.config.workspace_path, agent_name)
                    os.makedirs(ws, exist_ok=True)

                    sp_system = self.build_specialist_system_prompt(agent_name, task_desc)
                    sp_user = "You have been assigned a task by the coordinator. Execute it now."

                    sp_runner = AgentRunner(
                        agent_def=agent_def,
                        client=agent_client,
                        tool_registry=self.tool_registry,
                        config=self.config,
                        token_tracker=self.token_tracker,
                    )
                    sp_result = sp_runner.run(sp_system, sp_user)
                    self.message_bus.complete_task(task_msg["id"], sp_result.final_text)
                    logger.info("Task %s completed by %s (%d tool calls)",
                                task_msg["id"], agent_name, sp_result.tool_calls_count)

            # ── Phase 4: Check stop conditions ─────────────────────────
            if self.check_stop_conditions(iteration, result):
                break

        # Save final token usage
        self.token_tracker.save()
        logger.info("Coordinator loop finished after %d iterations.", iteration)

    # ── Prompt builders ────────────────────────────────────────────────

    def build_coordinator_system_prompt(self) -> str:
        cfg = self.config
        specialists = self.registry.get_specialists()

        team_lines = "\n".join(
            f"- {a.name}: {a.role[:100]}" for a in specialists
        )
        deliverable_lines = "\n".join(
            f"- {d['path']}: {d.get('description', '')}" for d in cfg.deliverables
        )
        journal_content = read_journal("coordinator") or "(empty — first session)"

        return f"""You are the coordinator of a multi-agent team.

## Task
Name: {cfg.task_name}
Goal: {cfg.task_goal}
Context: {cfg.task_context}

## Deliverables
{deliverable_lines}

## Your Team
{team_lines}

## Your Tools
- get_plan: read the current execution plan
- create_plan(steps): create a new execution plan
- update_plan(step_id, status, findings): update a plan step
- assign_task(agent, task_description): assign a task to a specialist
- check_result(task_id): check the result of a specific task
- check_all_results(): check all task statuses
- write_journal(content): record your decisions for future sessions
- read_file(path): read a file

## Your Decision History
{journal_content}

## Rules
1. You do NOT execute tasks yourself. Always assign to a specialist.
2. First session: create a plan. Assign the first tasks.
3. Subsequent sessions: check results, update plan, assign next tasks.
4. Keep task descriptions clear and specific. Include:
   - What to do
   - What files to read/write
   - What output format you expect
   - Maximum 3-5 sentences summary required at the end
5. When all plan steps are done, verify all deliverables exist.
6. BEFORE ENDING EVERY SESSION: call write_journal to record:
   - What you decided this session and why
   - Key data points that informed your decision
   - Backup plans if current approach fails
   This is critical — your next session has NO memory of this conversation."""

    def build_specialist_system_prompt(self, agent_name: str, task_desc: str) -> str:
        cfg = self.config
        agent_def = self.registry.get_agent(agent_name)
        journal_content = read_journal(agent_name) or "(empty — first session)"

        allowed = "\n".join(cfg.allowed_paths) if cfg.allowed_paths else "(none)"

        # Build tool list text
        tools = list(agent_def.tool_names)
        if "write_journal" not in tools:
            tools.append("write_journal")
        tool_text = "\n".join(f"- {t}" for t in tools)

        return f"""You are {agent_name}, a specialist agent in a team.

## Your Role
{agent_def.role}

## Your Task
{task_desc}

## Your Work History
{journal_content}

## File Organization
- Intermediate files: save to workspace/{agent_name}/
- Final deliverables: save to output/
- Your journal: use write_journal tool (do NOT write to .harness/ directly)

## Accessible External Paths
{allowed}

You can read and write files at these paths using their absolute paths.
Files outside these paths and outside the working directory will be blocked.

## Available Tools
{tool_text}

## Rules
1. Execute the task described above.
2. Save all outputs to files in the working directory.
3. BEFORE ENDING: call write_journal to record:
   - What you did and what files you changed
   - What worked and what didn't (especially failed attempts)
   - Any useful observations for future sessions
4. Provide a concise summary (3-5 sentences) as your final message.
   This summary will be sent back to the coordinator."""

    def build_coordinator_user_message(self, iteration: int) -> str:
        if iteration == 1:
            return ("New session. No plan exists yet. "
                    "Read the task description, create a plan, and assign the first batch of tasks.")
        return ("New session. Check completed task results and current plan status. "
                "Update the plan, then assign next tasks or verify deliverables.")

    def check_stop_conditions(self, iteration: int, result: AgentResult) -> bool:
        """Return True if the loop should stop."""
        if self.is_all_done():
            logger.info("Stop: all plan steps done.")
            return True
        if iteration >= self.config.max_iterations:
            logger.warning("Stop: max iterations (%d) reached.", self.config.max_iterations)
            return True
        if "TASK_COMPLETE" in result.final_text:
            logger.info("Stop: coordinator said TASK_COMPLETE.")
            return True
        return False

    def is_all_done(self) -> bool:
        """Check if all plan steps have status 'done'."""
        if not os.path.exists(self.config.plan_file):
            return False
        with open(self.config.plan_file) as f:
            plan = json.load(f)
        steps = plan.get("steps", [])
        if not steps:
            return False
        return all(s["status"] == "done" for s in steps)
