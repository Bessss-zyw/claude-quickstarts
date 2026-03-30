#!/usr/bin/env python3
"""CLI entry point and coordinator main loop for CC-Native Multi-Agent Harness.

Usage:
    python supervisor.py --task task.md [--max-iterations 20]
"""

from __future__ import annotations

import os
import sys
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"   # for child processes
sys.dont_write_bytecode = True                  # for current interpreter

import argparse
import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

from cc_instance import CCInstance
from config import AgentDef, HarnessConfig, load_config
from planner import Planner
from state_detector import (
    PaneState, classify_permission, detect_state, extract_last_response, get_context_pct,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("supervisor")


# ── Plan helpers ──────────────────────────────────────────────────────────

def load_plan(config: HarnessConfig) -> dict | None:
    if os.path.exists(config.plan_file):
        with open(config.plan_file, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_plan(config: HarnessConfig, plan: dict) -> None:
    os.makedirs(os.path.dirname(config.plan_file), exist_ok=True)
    plan["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(config.plan_file, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)


def make_plan_dict(steps: list[dict]) -> dict:
    return {
        "steps": steps,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_pending_steps(plan: dict) -> list[dict]:
    return [s for s in plan["steps"] if s["status"] == "pending"]


def get_in_progress_steps(plan: dict) -> list[dict]:
    return [s for s in plan["steps"] if s["status"] == "in_progress"]


def all_done(plan: dict) -> bool:
    return all(s["status"] == "done" for s in plan["steps"])


def gather_prior_findings(plan: dict, step: dict) -> str:
    """Collect findings from a step's dependencies for context injection."""
    deps = step.get("depends_on", [])
    if not deps:
        return ""
    parts = []
    for s in plan["steps"]:
        if s["id"] in deps and s.get("findings"):
            parts.append(f"[Step {s['id']} — {s['assigned_to']}]: {s['findings'][:1000]}")
    return "\n".join(parts)


# ── History log ───────────────────────────────────────────────────────────

def append_history(config: HarnessConfig, entry: dict) -> None:
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(config.history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── tmux session management ──────────────────────────────────────────────

def ensure_tmux_session(session_name: str) -> None:
    """Create the tmux session if it doesn't exist.

    Raises RuntimeError if a session with the same name already exists
    (likely another supervisor instance).
    """
    import subprocess
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True, timeout=5,
    )
    if result.returncode == 0:
        raise RuntimeError(
            f"tmux session '{session_name}' already exists — another supervisor "
            f"may be running. Kill it first or use a different task name / "
            f"tmux_session in your task file."
        )
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", session_name],
        capture_output=True, timeout=10, check=True,
    )
    log.info("Created tmux session: %s", session_name)


def kill_tmux_session(session_name: str) -> None:
    """Kill the tmux session. Safe to call even if it doesn't exist."""
    import subprocess
    try:
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True, timeout=10,
        )
        log.info("Killed tmux session: %s", session_name)
    except Exception as e:
        log.warning("Failed to kill tmux session '%s': %s", session_name, e)


# ── Main loop ─────────────────────────────────────────────────────────────

def run(config: HarnessConfig) -> None:
    """Coordinator main loop."""
    config.ensure_dirs()
    specialists = config.get_specialists()

    if not specialists:
        log.error("No specialist agents defined. Nothing to do.")
        return

    # Init planner (Opus API)
    planner = Planner(config)

    # Create tmux session
    ensure_tmux_session(config.tmux_session)

    # Start CC instances for each specialist
    instances: dict[str, CCInstance] = {}
    for agent in specialists:
        cc = CCInstance(agent, config.tmux_session, config)
        cc.start()
        instances[agent.name] = cc

    log.info("All %d CC instances started: %s",
             len(instances), list(instances.keys()))

    # Track which agents are currently working (agent_name → step_id)
    active_agents: dict[str, int] = {}
    # Track when each agent was dispatched (for grace period)
    dispatch_times: dict[str, float] = {}
    # Minimum seconds to wait after dispatch before checking for IDLE
    _DISPATCH_GRACE = 15
    # Consecutive IDLE poll counts per agent (reset on non-IDLE states)
    idle_counts: dict[str, int] = {}
    # Consecutive UNKNOWN poll counts per agent (detect stuck state)
    unknown_counts: dict[str, int] = {}
    _UNKNOWN_STUCK_THRESHOLD = 3  # ~9s at 3s poll — escalate to coordinator
    # Collect results per agent for replan
    results: dict[str, str] = {}

    # Load existing plan if resuming
    plan = load_plan(config)
    if plan is not None:
        log.info("Resuming from existing plan (%d steps)", len(plan["steps"]))
        # Re-derive active_agents from in_progress steps
        for step in get_in_progress_steps(plan):
            active_agents[step["assigned_to"]] = step["id"]

    iteration = 0
    try:
        while True:
            iteration += 1
            log.info("=== Iteration %d ===", iteration)

            # ── Phase 1: Create or update plan via Opus API ───────────
            if plan is None:
                log.info("Creating initial plan...")
                specialists_dict = {a.name: a for a in specialists}
                steps = planner.create_plan(specialists_dict)
                plan = make_plan_dict(steps)
                save_plan(config, plan)
                append_history(config, {
                    "event": "plan_created",
                    "steps": len(steps),
                })
                log.info("Plan created with %d steps", len(steps))
            else:
                # Evaluate and replan
                log.info("Evaluating progress and replanning...")
                specialists_dict = {a.name: a for a in specialists}
                decisions = planner.evaluate_and_replan(plan["steps"], specialists_dict)
                append_history(config, {
                    "event": "replan",
                    "reasoning": decisions.get("reasoning", ""),
                    "is_complete": decisions.get("is_complete", False),
                })

                # Apply plan updates
                for upd in decisions.get("plan_updates", []):
                    for step in plan["steps"]:
                        if step["id"] == upd.get("step_id"):
                            if "status" in upd:
                                step["status"] = upd["status"]
                            if "findings" in upd:
                                step["findings"] = upd["findings"]

                # Add new steps
                for ns in decisions.get("new_steps", []):
                    new_id = max(s["id"] for s in plan["steps"]) + 1
                    plan["steps"].append({
                        "id": new_id,
                        "description": ns.get("description", ""),
                        "assigned_to": ns.get("assigned_to", ""),
                        "status": "pending",
                        "findings": "",
                    })

                save_plan(config, plan)

                if decisions.get("is_complete"):
                    log.info("Planner declared TASK_COMPLETE.")
                    break

                # Send direct instructions from replan
                for agent_name, instruction in decisions.get("next_instructions", {}).items():
                    if agent_name in instances and agent_name not in active_agents:
                        instances[agent_name].send(instruction)
                        dispatch_times[agent_name] = time.time()
                        # Find the relevant pending step for this agent
                        for step in plan["steps"]:
                            if step["assigned_to"] == agent_name and step["status"] == "pending":
                                step["status"] = "in_progress"
                                active_agents[agent_name] = step["id"]
                                break
                        save_plan(config, plan)

            # ── Phase 2: Dispatch pending steps ───────────────────────
            for step in get_pending_steps(plan):
                agent_name = step["assigned_to"]
                if agent_name not in instances:
                    log.warning("Step %d assigned to unknown agent '%s', skipping",
                                step["id"], agent_name)
                    step["status"] = "failed"
                    step["findings"] = f"Unknown agent: {agent_name}"
                    save_plan(config, plan)
                    continue
                if agent_name in active_agents:
                    continue  # agent is busy

                # Generate instruction — check depends_on first
                deps = step.get("depends_on", [])
                if deps:
                    all_deps_done = all(
                        any(s["id"] == dep_id and s["status"] == "done" for s in plan["steps"])
                        for dep_id in deps
                    )
                    if not all_deps_done:
                        continue  # dependencies not met yet

                recent_output = instances[agent_name].capture(lines=50)
                prior = gather_prior_findings(plan, step)
                instruction = planner.generate_instruction(
                    config.agents[agent_name], step["description"], recent_output,
                    output_path=config.output_path, prior_findings=prior,
                    report_path=config.report_path(step["id"]),
                )
                instances[agent_name].send(instruction)
                step["status"] = "in_progress"
                active_agents[agent_name] = step["id"]
                dispatch_times[agent_name] = time.time()
                save_plan(config, plan)
                log.info("Dispatched step %d to %s", step["id"], agent_name)

            # ── Phase 3: Monitor loop ─────────────────────────────────
            if not active_agents:
                # No work dispatched — go back to coordinator for evaluation.
                # The coordinator decides whether to add steps, retry, or declare complete.
                if not get_pending_steps(plan):
                    log.info("No active or pending steps. Returning to coordinator for evaluation.")
                    continue
                else:
                    # There are pending steps but they have unmet deps; wait for replan
                    log.info("Pending steps have unmet dependencies. Returning to coordinator.")
                    continue

            monitor_rounds = 0
            max_monitor_rounds = 600  # ~30 min with 3s poll

            while active_agents and monitor_rounds < max_monitor_rounds:
                monitor_rounds += 1
                time.sleep(config.poll_interval)

                finished: list[str] = []
                for agent_name, step_id in list(active_agents.items()):
                    cc = instances[agent_name]
                    output = cc.capture()
                    state = detect_state(output)

                    # Reset counters on state transitions
                    if state != PaneState.IDLE:
                        idle_counts.pop(agent_name, None)
                    if state != PaneState.UNKNOWN:
                        unknown_counts.pop(agent_name, None)

                    if state == PaneState.PERMISSION:
                        _, prompt_text, is_dangerous = classify_permission(output)
                        if not is_dangerous:
                            cc.approve_permission()
                            log.info("[%s] auto-approved safe permission", agent_name)
                        else:
                            # Find current step description for context
                            step_desc = ""
                            for s in plan["steps"]:
                                if s["id"] == step_id:
                                    step_desc = s["description"]
                                    break
                            log.warning("[%s] dangerous permission detected: %s",
                                        agent_name, prompt_text[:200])
                            approved = planner.review_permission(
                                agent_name, step_desc, prompt_text,
                            )
                            if approved:
                                cc.approve_permission()
                                log.info("[%s] coordinator approved dangerous permission",
                                         agent_name)
                            else:
                                cc.reject_permission()
                                log.warning("[%s] coordinator REJECTED dangerous permission",
                                            agent_name)
                            append_history(config, {
                                "event": "permission_review",
                                "agent": agent_name,
                                "step_id": step_id,
                                "prompt_text": prompt_text[:500],
                                "is_dangerous": True,
                                "approved": approved,
                            })
                        # CRITICAL: After approving/rejecting, reset the grace timer.
                        # CC needs time to process the approval and start executing.
                        # Without this, the brief IDLE state between approval and
                        # execution gets misdetected as "task completed", causing
                        # dependent tasks to start prematurely.
                        dispatch_times[agent_name] = time.time()

                    elif state == PaneState.IDLE:
                        # Grace period: ignore IDLE right after dispatch
                        if time.time() - dispatch_times.get(agent_name, 0) < _DISPATCH_GRACE:
                            continue
                        # Require consecutive IDLE polls to confirm completion
                        idle_counts[agent_name] = idle_counts.get(agent_name, 0) + 1
                        if idle_counts[agent_name] < config.idle_confirm:
                            log.debug("[%s] idle detected (%d/%d), waiting for confirmation",
                                      agent_name, idle_counts[agent_name], config.idle_confirm)
                            continue
                        # Confirmed idle — agent finished its task
                        log.info("[%s] confirmed idle after %d consecutive polls, marking done",
                                 agent_name, idle_counts[agent_name])
                        result_text = extract_last_response(output)
                        cc.save_log(output)

                        # Check for step report file (preferred over pane extraction)
                        report_file = config.report_path(step_id)
                        if os.path.isfile(report_file):
                            log.info("[%s] step report found: %s", agent_name, report_file)
                        else:
                            log.warning("[%s] NO step report written at %s — "
                                        "coordinator will rely on pane-extracted findings",
                                        agent_name, report_file)

                        # Update plan step (pane-extracted as fallback; report file
                        # is read directly by coordinator in evaluate_and_replan)
                        for step in plan["steps"]:
                            if step["id"] == step_id:
                                step["status"] = "done"
                                step["findings"] = result_text[:2000]
                                break
                        save_plan(config, plan)
                        results[agent_name] = result_text
                        finished.append(agent_name)
                        idle_counts.pop(agent_name, None)

                    elif state == PaneState.EXPIRED:
                        log.error("[%s] session expired!", agent_name)
                        for step in plan["steps"]:
                            if step["id"] == step_id:
                                step["status"] = "failed"
                                step["findings"] = "CC session expired"
                                break
                        save_plan(config, plan)
                        finished.append(agent_name)

                    elif state == PaneState.ERROR:
                        log.warning("[%s] error detected in pane", agent_name)
                        # Don't immediately fail — CC might recover

                    elif state == PaneState.UNKNOWN:
                        unknown_counts[agent_name] = unknown_counts.get(agent_name, 0) + 1
                        if unknown_counts[agent_name] == 1:
                            log.info("[%s] UNKNOWN state (will retry)", agent_name)
                        if unknown_counts[agent_name] >= _UNKNOWN_STUCK_THRESHOLD:
                            # Stuck for too long — likely an unrecognized permission prompt.
                            # Ask coordinator to review the pane content and decide.
                            tail_lines = "\n".join(output.splitlines()[-25:])
                            log.warning(
                                "[%s] UNKNOWN state for %d consecutive polls (~%ds). "
                                "Escalating to coordinator for review.",
                                agent_name, unknown_counts[agent_name],
                                unknown_counts[agent_name] * config.poll_interval,
                            )
                            log.warning("[%s] pane tail:\n%s", agent_name, tail_lines)
                            # Find current step description for context
                            step_desc = ""
                            for s in plan["steps"]:
                                if s["id"] == step_id:
                                    step_desc = s["description"]
                                    break
                            approved = planner.review_permission(
                                agent_name, step_desc,
                                f"[UNKNOWN state — possible unrecognized permission prompt]\n{tail_lines}",
                            )
                            if approved:
                                cc.approve_permission()
                                log.info("[%s] coordinator approved UNKNOWN stuck recovery",
                                         agent_name)
                            else:
                                cc.reject_permission()
                                log.warning("[%s] coordinator REJECTED UNKNOWN stuck recovery",
                                            agent_name)
                            unknown_counts[agent_name] = 0
                            # Reset grace timer — same reason as PERMISSION branch
                            dispatch_times[agent_name] = time.time()
                            append_history(config, {
                                "event": "unknown_stuck_recovery",
                                "agent": agent_name,
                                "step_id": step_id,
                                "pane_tail": tail_lines[:500],
                                "coordinator_approved": approved,
                            })

                    # Context compaction check
                    ctx_pct = get_context_pct(output)
                    if ctx_pct is not None and ctx_pct >= config.compact_threshold:
                        cc.send_compact("Focus on the current task.")
                        log.info("[%s] context at %d%%, sent /compact", agent_name, ctx_pct)

                for name in finished:
                    del active_agents[name]

                # If new steps became pending (from previous dispatch), dispatch them
                for step in get_pending_steps(plan):
                    agent_name = step["assigned_to"]
                    if agent_name in instances and agent_name not in active_agents:
                        # Check depends_on
                        deps = step.get("depends_on", [])
                        if deps and not all(
                            any(s["id"] == d and s["status"] == "done" for s in plan["steps"])
                            for d in deps
                        ):
                            continue
                        recent_output = instances[agent_name].capture(lines=50)
                        prior = gather_prior_findings(plan, step)
                        instruction = planner.generate_instruction(
                            config.agents[agent_name], step["description"], recent_output,
                            output_path=config.output_path, prior_findings=prior,
                            report_path=config.report_path(step["id"]),
                        )
                        instances[agent_name].send(instruction)
                        step["status"] = "in_progress"
                        active_agents[agent_name] = step["id"]
                        dispatch_times[agent_name] = time.time()
                        save_plan(config, plan)
                        log.info("Dispatched step %d to %s (during monitor)", step["id"], agent_name)

            # ── Phase 4: Check stop conditions ────────────────────────
            # Only two valid exit conditions:
            # 1. Coordinator declares is_complete=true (handled in Phase 1 replan)
            # 2. Max iterations reached (with coordinator summary)
            if iteration >= config.max_iterations:
                log.warning("Max iterations (%d) reached. Returning to coordinator for final summary.", config.max_iterations)
                # Do one final evaluation so coordinator can summarize
                specialists_dict = {a.name: a for a in specialists}
                decisions = planner.evaluate_and_replan(plan["steps"], specialists_dict)
                append_history(config, {
                    "event": "max_iterations_summary",
                    "reasoning": decisions.get("reasoning", ""),
                    "is_complete": decisions.get("is_complete", False),
                })
                # Apply any final plan updates
                for upd in decisions.get("plan_updates", []):
                    for step in plan["steps"]:
                        if step["id"] == upd.get("step_id"):
                            if "status" in upd:
                                step["status"] = upd["status"]
                            if "findings" in upd:
                                step["findings"] = upd["findings"]
                save_plan(config, plan)
                break

            # Clear results for next iteration's replan
            results.clear()

    finally:
        # Save state
        planner.save_token_usage()
        if plan:
            save_plan(config, plan)

        # ── Summary ───────────────────────────────────────────────────────
        log.info("=== DONE ===")
        log.info("Token usage: %s", planner.token_usage)
        if plan:
            done_count = sum(1 for s in plan["steps"] if s["status"] == "done")
            log.info("Plan: %d/%d steps done", done_count, len(plan["steps"]))
        for d in config.deliverables:
            path = os.path.join(config.output_path, d["path"])
            status = "OK" if os.path.exists(path) else "MISSING"
            log.info("Deliverable [%s]: %s", status, d["path"])

        # Stop CC instances and kill tmux session
        for cc in instances.values():
            cc.stop()
        kill_tmux_session(config.tmux_session)


# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="CC-Native Multi-Agent Harness")
    parser.add_argument("--task", required=True,
                        help="Path to task file (.yaml or .md)")
    parser.add_argument("--max-iterations", type=int, default=20,
                        help="Max coordinator iterations (default 20)")
    args = parser.parse_args()

    # Load .env
    task_dir = os.path.dirname(os.path.abspath(args.task))
    harness_dir = os.path.dirname(os.path.abspath(__file__))
    for env_dir in (task_dir, os.getcwd(), harness_dir):
        env_file = os.path.join(env_dir, ".env")
        if os.path.isfile(env_file):
            load_dotenv(env_file, override=False)
            log.info("Loaded .env from %s", env_dir)
            break

    config = load_config(args.task, max_iterations=args.max_iterations)
    log.info("Task: %s | Working dir: %s", config.task_name, config.working_dir)
    log.info("Agents: %s", list(config.agents.keys()))

    # SIGINT handler
    def _sigint(sig, frame):
        log.warning("SIGINT — saving state and exiting.")
        sys.exit(1)

    signal.signal(signal.SIGINT, _sigint)

    run(config)


if __name__ == "__main__":
    main()
