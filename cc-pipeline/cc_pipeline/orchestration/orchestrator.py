"""Pipeline orchestrator — DI-constructed thin coordinator.

All components are injected; orchestrator only drives the DAG loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pathlib import Path

from ..artifacts.layout import ArtifactLayout
from ..artifacts.summary import generate_summary_json, generate_summary_md
from ..config.models import TaskConfig
from ..state.manager import StateManager
from ..state.models import PipelineState
from ..strategies.base import StageContext, StageStrategy, StrategyRegistry
from .dag_scheduler import DagScheduler

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Drives pipeline execution with injected dependencies."""

    def __init__(
        self,
        config: TaskConfig,
        state_mgr: StateManager,
        strategies: StrategyRegistry,
        ctx_factory: "_ContextFactory",
        max_parallel: int = 4,
    ):
        self._config = config
        self._state_mgr = state_mgr
        self._strategies = strategies
        self._ctx_factory = ctx_factory
        self._sem = asyncio.Semaphore(max_parallel)
        self._scheduler = DagScheduler(config, state_mgr)
        self._tasks: dict[str, asyncio.Task] = {}

    async def run(self) -> PipelineState:
        logger.info(
            "Starting pipeline %r (%d stages)",
            self._config.name, len(self._config.stages),
        )
        try:
            await self._run_dag()
        except Exception as e:
            logger.error("Pipeline failed: %s", e)
            self._state_mgr.mark_pipeline_failed()
            raise
        else:
            self._state_mgr.mark_pipeline_done()
        finally:
            layout = ArtifactLayout(
                Path(self._config.working_dir) / ".pipeline",
            )
            generate_summary_json(self._state_mgr.state, layout)
            generate_summary_md(self._state_mgr.state, layout)

        return self._state_mgr.state

    async def _run_dag(self) -> None:
        dispatched: set[str] = set()

        while True:
            ready = self._scheduler.get_ready(dispatched)

            if not ready:
                skipped = self._scheduler.skip_blocked()
                if skipped:
                    continue
                if not self._scheduler.has_pending:
                    break
                if self._tasks:
                    await self._wait_one()
                    self._collect(dispatched)
                    continue
                self._scheduler.check_stuck(dispatched)

            for name in ready:
                task = asyncio.create_task(
                    self._execute(name), name=f"stage:{name}",
                )
                self._tasks[name] = task
                dispatched.add(name)

            if self._tasks:
                await self._wait_one()
                self._collect(dispatched)

    async def _execute(self, stage_name: str) -> dict[str, Any]:
        async with self._sem:
            stage_cfg = self._config.stages[stage_name]
            strategy = self._strategies.get(stage_cfg.type)
            ctx = self._ctx_factory(stage_name)

            self._state_mgr.mark_stage_running(stage_name)
            try:
                outputs = await strategy.execute(ctx, stage_cfg)
                # Strategies return outputs; orchestrator owns state done
                if not self._state_mgr.is_stage_done(stage_name):
                    self._state_mgr.mark_stage_done(
                        stage_name, outputs=outputs,
                    )
                return outputs
            except Exception as e:
                logger.error("Stage %r failed: %s", stage_name, e)
                self._state_mgr.mark_stage_failed(
                    stage_name, error=str(e),
                )
                raise

    async def _wait_one(self) -> None:
        if self._tasks:
            await asyncio.wait(
                self._tasks.values(),
                return_when=asyncio.FIRST_COMPLETED,
            )

    def _collect(self, dispatched: set[str]) -> None:
        done_names = [n for n, t in self._tasks.items() if t.done()]
        for name in done_names:
            task = self._tasks.pop(name)
            dispatched.discard(name)
            exc = task.exception()
            if exc:
                logger.error("Stage %r raised: %s", name, exc)



# Type alias for the context factory callable
_ContextFactory = Any  # Callable[[str], StageContext]
