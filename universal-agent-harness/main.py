#!/usr/bin/env python3
"""CLI entry point for Universal Agent Harness.

Usage:
    python main.py --task task.yaml [--max-iterations 20] [--resume]
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

import yaml
from dotenv import load_dotenv

from config import HarnessConfig
from agent_registry import AgentRegistry
from coordinator import CoordinatorLoop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("harness")


def load_config(task_file: str, max_iterations: int) -> HarnessConfig:
    """Parse task.yaml and build HarnessConfig."""
    task_file = os.path.abspath(task_file)
    with open(task_file) as f:
        data = yaml.safe_load(f)

    env = data.get("environment", {})
    working_dir = env.get("working_dir", ".")
    if not os.path.isabs(working_dir):
        working_dir = os.path.join(os.path.dirname(task_file), working_dir)
    working_dir = os.path.abspath(working_dir)

    deliverables = data.get("deliverables", [])
    if not deliverables:
        logger.warning("No deliverables defined in task.yaml.")

    return HarnessConfig(
        task_file=task_file,
        working_dir=working_dir,
        max_iterations=max_iterations,
        task_name=data.get("name", "unnamed"),
        task_goal=data.get("goal", ""),
        task_context=data.get("context", ""),
        deliverables=deliverables,
        shell_preamble=env.get("shell_preamble", []),
        allowed_paths=env.get("allowed_paths", []),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Universal Agent Harness")
    parser.add_argument("--task", required=True, help="Path to task.yaml")
    parser.add_argument("--max-iterations", type=int, default=20,
                        help="Max coordinator iterations (default 20)")
    parser.add_argument("--resume", action="store_true", default=True,
                        help="Resume from existing .harness/ state (default)")
    args = parser.parse_args()

    # Load .env (search: task.yaml dir → cwd → harness source dir)
    task_dir = os.path.dirname(os.path.abspath(args.task))
    harness_dir = os.path.dirname(os.path.abspath(__file__))
    for env_dir in (task_dir, os.getcwd(), harness_dir):
        env_file = os.path.join(env_dir, ".env")
        if os.path.isfile(env_file):
            load_dotenv(env_file, override=False)
            logger.info("Loaded .env from %s", env_dir)
            break

    # Load config
    config = load_config(args.task, args.max_iterations)
    logger.info("Task: %s", config.task_name)
    logger.info("Working dir: %s", config.working_dir)

    # Create runtime dirs (skip if resuming and already exist)
    config.ensure_dirs()

    # Load agent registry
    registry = AgentRegistry(config.task_file)
    logger.info("Agents: %s", registry.list_agent_names())

    # Build coordinator loop
    loop = CoordinatorLoop(config, registry)

    # Graceful shutdown on Ctrl+C
    def _sigint_handler(sig, frame):
        logger.warning("SIGINT received — saving state and exiting.")
        loop.token_tracker.save()
        sys.exit(1)

    signal.signal(signal.SIGINT, _sigint_handler)

    # Run
    try:
        loop.run()
    except KeyboardInterrupt:
        logger.warning("Interrupted. State preserved in %s", config.harness_path)
        loop.token_tracker.save()
        sys.exit(1)

    # Final summary
    logger.info("=== DONE ===")
    logger.info("Token usage:\n%s", loop.token_tracker.summary())

    # Check deliverables
    for d in config.deliverables:
        path = os.path.join(config.output_path, d["path"])
        exists = os.path.exists(path)
        status = "OK" if exists else "MISSING"
        logger.info("Deliverable [%s]: %s — %s", status, d["path"], d.get("description", ""))


if __name__ == "__main__":
    main()
