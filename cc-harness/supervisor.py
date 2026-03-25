#!/usr/bin/env python3
"""CLI entry point and coordinator main loop for CC-Native Multi-Agent Harness.

Usage:
    python supervisor.py --task task.md [--max-iterations 20]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

from cc_instance import CCInstance
from config import AgentDef, HarnessConfig, load_config
from planner import Planner
from state_detector import PaneState, detect_state, extract_last_response, get_context_pct

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


# ── History log ───────────────────────────────────────────────────────────

def append_history(config: HarnessConfig, entry: dict) -> None:
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(config.history_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── tmux session management ──────────────────────────────────────────────

def ensure_tmux_session(session_name: str) -> None:
    """Create the tmux session if it doesn't exist."""
    import subprocess
    result = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        capture_output=True, timeout=5,
    )
    if result.returncode != 0:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", session_name],
            capture_output=True, timeout=10, check=True,
        )
        log.info("Created tmux session: %s", session_name)


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
                instruction = planner.generate_instruction(
                    config.agents[agent_name], step["description"], recent_output,
                )
                instances[agent_name].send(instruction)
                step["status"] = "in_progress"
                active_agents[agent_name] = step["id"]
                dispatch_times[agent_name] = time.time()
                save_plan(config, plan)
                log.info("Dispatched step %d to %s", step["id"], agent_name)

            # ── Phase 3: Monitor loop ─────────────────────────────────
            if not active_agents:
                if all_done(plan):
                    log.info("All steps done.")
                    break
                if not get_pending_steps(plan):
                    log.warning("No active or pending steps. Forcing replan next iteration.")
                    continue

            monitor_rounds = 0
            max_monitor_rounds = 120  # ~30 min with 15s poll

            while active_agents and monitor_rounds < max_monitor_rounds:
                monitor_rounds += 1
                time.sleep(config.poll_interval)

                finished: list[str] = []
                for agent_name, step_id in list(active_agents.items()):
                    cc = instances[agent_name]
                    output = cc.capture()
                    state = detect_state(output)

                    if state == PaneState.PERMISSION:
                        cc.approve_permission()
                        log.info("[%s] auto-approved permission", agent_name)

                    elif state == PaneState.IDLE:
                        # Grace period: ignore IDLE right after dispatch
                        if time.time() - dispatch_times.get(agent_name, 0) < _DISPATCH_GRACE:
                            continue
                        # Agent finished its task
                        result_text = extract_last_response(output)
                        cc.save_log(output)

                        # Update plan step
                        for step in plan["steps"]:
                            if step["id"] == step_id:
                                step["status"] = "done"
                                step["findings"] = result_text[:500]
                                break
                        save_plan(config, plan)
                        results[agent_name] = result_text
                        finished.append(agent_name)
                        log.info("[%s] step %d completed", agent_name, step_id)

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
                        instruction = planner.generate_instruction(
                            config.agents[agent_name], step["description"], recent_output,
                        )
                        instances[agent_name].send(instruction)
                        step["status"] = "in_progress"
                        active_agents[agent_name] = step["id"]
                        dispatch_times[agent_name] = time.time()
                        save_plan(config, plan)
                        log.info("Dispatched step %d to %s (during monitor)", step["id"], agent_name)

            # ── Phase 4: Check stop conditions ────────────────────────
            if all_done(plan):
                log.info("All plan steps completed.")
                break

            if iteration >= config.max_iterations:
                log.warning("Max iterations (%d) reached.", config.max_iterations)
                break

            # Clear results for next iteration's replan
            results.clear()

    finally:
        # Save state
        planner.save_token_usage()
        if plan:
            save_plan(config, plan)

    # ── Summary ───────────────────────────────────────────────────────────
    log.info("=== DONE ===")
    log.info("Token usage: %s", planner.token_usage)
    if plan:
        done_count = sum(1 for s in plan["steps"] if s["status"] == "done")
        log.info("Plan: %d/%d steps done", done_count, len(plan["steps"]))
    for d in config.deliverables:
        path = os.path.join(config.output_path, d["path"])
        status = "OK" if os.path.exists(path) else "MISSING"
        log.info("Deliverable [%s]: %s", status, d["path"])

    # Optionally stop CC instances
    for cc in instances.values():
        cc.stop()


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
