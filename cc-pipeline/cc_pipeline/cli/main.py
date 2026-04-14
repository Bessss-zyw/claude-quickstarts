"""CLI entry point for cc-pipeline.

Thin layer: parse args → compose components → call orchestrator.
No business logic here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from ..artifacts.store import ArtifactStore
from ..config.loader import load_task_config
from ..config.models import TaskConfig
from ..constants.enums import StageType
from ..extractors.base import build_default_registry
from ..orchestration.orchestrator import PipelineOrchestrator
from ..runners.claude_cli import ClaudeCliRunner
from ..state.manager import StateManager
from ..strategies.base import StageContext, StrategyRegistry
from ..strategies.convergence import build_default_operators
from ..strategies.loop import LoopStrategy
from ..strategies.map import MapStrategy
from ..strategies.single_shot import SingleShotStrategy
from ..templates.engine import TemplateEngine
from .display import print_pipeline_summary, print_waves, state_summary


def main() -> None:
    args = _parse_args()
    _setup_logging(
        os.environ.get("CC_PIPELINE_LOG_LEVEL", args.log_level),
    )

    if args.status:
        _cmd_status(args.task)
    elif args.dry_run:
        _cmd_dry_run(args.task)
    else:
        code = asyncio.run(
            _cmd_run(args.task, args.resume, args.claude_bin),
        )
        sys.exit(code)


# ---- commands -------------------------------------------------------------

def _cmd_status(task_path: Path) -> None:
    config = load_task_config(task_path)
    pipeline_dir = Path(config.working_dir) / ".pipeline"
    mgr = StateManager(pipeline_dir)
    state = mgr.load_readonly()
    if state is None:
        print("No pipeline state found.")
        return
    print(json.dumps(state_summary(state), indent=2))


def _cmd_dry_run(task_path: Path) -> None:
    config = load_task_config(task_path)
    print(f"Pipeline: {config.name}")
    print(f"Description: {config.description}")
    print(f"Working dir: {config.working_dir}")
    print(f"Max parallel: {config.max_parallel}")
    print(f"Stages: {len(config.stages)}")
    print()
    print_waves(config)
    print("\nConfig valid \u2713")


async def _cmd_run(
    task_path: Path, resume: bool, claude_bin: str | None,
) -> int:
    config = load_task_config(task_path)
    orch, state_mgr = _compose(config, task_path, claude_bin, resume)
    t0 = time.monotonic()
    try:
        state = await orch.run()
        print_pipeline_summary(state, time.monotonic() - t0)
        return 0 if state.status.value == "done" else 1
    except Exception as e:
        logging.getLogger(__name__).error("Pipeline failed: %s", e)
        print_pipeline_summary(state_mgr.state, time.monotonic() - t0)
        return 1


# ---- composition ---------------------------------------------------------

def _compose(
    config: TaskConfig,
    task_path: Path,
    claude_bin: str | None,
    resume: bool,
) -> tuple[PipelineOrchestrator, StateManager]:
    pipeline_dir = Path(config.working_dir) / ".pipeline"
    runner = ClaudeCliRunner(claude_bin or "claude")
    state_mgr = StateManager(pipeline_dir)
    state_mgr.load_or_create(
        config, resume=resume, task_file=str(task_path),
    )

    artifacts = ArtifactStore(pipeline_dir)
    templates = TemplateEngine()
    extractors = build_default_registry()

    # Strategy registry
    operators = build_default_operators()
    strategies = StrategyRegistry()
    single = SingleShotStrategy()
    strategies.register(StageType.PRE_EXEC, single)
    strategies.register(StageType.SUBTASK, single)
    strategies.register(StageType.POST_EXEC, single)
    strategies.register(StageType.LOOP, LoopStrategy(operators))
    strategies.register(StageType.MAP, MapStrategy())

    def ctx_factory(stage_name: str) -> StageContext:
        return StageContext(
            stage_name=stage_name,
            config=config,
            runner=runner,
            artifacts=artifacts,
            state_mgr=state_mgr,
            templates=templates,
            extractors=extractors,
        )

    orch = PipelineOrchestrator(
        config=config,
        state_mgr=state_mgr,
        strategies=strategies,
        ctx_factory=ctx_factory,
        max_parallel=config.max_parallel,
    )
    return orch, state_mgr


# ---- helpers --------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="cc-pipeline")
    p.add_argument("--task", "-t", required=True, type=Path)
    p.add_argument("--resume", "-r", action="store_true")
    p.add_argument("--status", "-s", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--claude-bin", default=None)
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p.parse_args()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("asyncio").setLevel(logging.WARNING)


if __name__ == "__main__":
    main()
