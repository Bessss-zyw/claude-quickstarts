#!/usr/bin/env python3
"""CLI entry point for Universal Agent Harness.

Usage:
    python main.py --task task.yaml [--max-iterations 20]
    python main.py --task task.md   [--max-iterations 20]

Supports both YAML and Markdown (with YAML frontmatter) task files.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import sys

import yaml
from dotenv import load_dotenv

from core.config import HarnessConfig
from core.agent_registry import AgentRegistry
from core.coordinator import CoordinatorLoop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("harness")


# ── Task file parsing ──────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_markdown_task(path: str) -> dict:
    """Parse a Markdown task file with YAML frontmatter.

    Format:
        ---
        name: my-task
        deliverables: [...]
        environment: {...}
        agents: {...}
        ---

        # Goal
        Everything below the frontmatter becomes the `goal` field.
        The first paragraph (before any ## heading) is used as `context`
        if a separate `context` field is not in the frontmatter.
    """
    with open(path) as f:
        content = f.read()

    m = _FRONTMATTER_RE.match(content)
    if not m:
        raise ValueError(
            f"{path}: Markdown task file must start with YAML frontmatter "
            "(--- ... ---). See templates/task_template.md for an example."
        )

    frontmatter = yaml.safe_load(m.group(1)) or {}
    body = content[m.end():].strip()

    # The markdown body becomes the goal (unless frontmatter already has one)
    if "goal" not in frontmatter and body:
        frontmatter["goal"] = body
    elif body and "goal" in frontmatter:
        # If frontmatter has goal AND there's a body, append body as extra context
        frontmatter["goal"] = frontmatter["goal"].rstrip() + "\n\n" + body

    return frontmatter


def _parse_yaml_task(path: str) -> dict:
    """Parse a plain YAML task file."""
    with open(path) as f:
        return yaml.safe_load(f)


def parse_task_file(path: str) -> dict:
    """Auto-detect format and parse a task file (.yaml/.yml or .md)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".md", ".markdown"):
        return _parse_markdown_task(path)
    else:
        return _parse_yaml_task(path)


def load_config(task_file: str, max_iterations: int) -> HarnessConfig:
    """Parse task file and build HarnessConfig."""
    task_file = os.path.abspath(task_file)
    data = parse_task_file(task_file)

    env = data.get("environment", {})
    working_dir = env.get("working_dir", ".")
    if not os.path.isabs(working_dir):
        working_dir = os.path.join(os.path.dirname(task_file), working_dir)
    working_dir = os.path.abspath(working_dir)

    deliverables = data.get("deliverables", [])
    if not deliverables:
        logger.warning("No deliverables defined in task file.")

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


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Universal Agent Harness")
    parser.add_argument("--task", required=True,
                        help="Path to task file (.yaml or .md)")
    parser.add_argument("--max-iterations", type=int, default=20,
                        help="Max coordinator iterations (default 20)")
    parser.add_argument("--resume", action="store_true", default=True,
                        help="Resume from existing .harness/ state (default)")
    args = parser.parse_args()

    # Load .env (search: task file dir → cwd → harness source dir)
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
