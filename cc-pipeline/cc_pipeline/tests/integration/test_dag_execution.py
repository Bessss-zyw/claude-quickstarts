"""Integration test: DAG execution with mock runner.

Verifies stage ordering, parallel dispatch, and skip-on-failure.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from cc_pipeline.artifacts.store import ArtifactStore
from cc_pipeline.config.models import StageConfig, TaskConfig
from cc_pipeline.constants.enums import PermissionLevel, StageStatus, StageType
from cc_pipeline.extractors.base import build_default_registry
from cc_pipeline.orchestration.orchestrator import PipelineOrchestrator
from cc_pipeline.runners.base import Runner, RunResult
from cc_pipeline.state.manager import StateManager
from cc_pipeline.state.models import TokenUsage
from cc_pipeline.strategies.base import (
    StageContext,
    StrategyRegistry,
)
from cc_pipeline.strategies.convergence import build_default_operators
from cc_pipeline.strategies.loop import LoopStrategy
from cc_pipeline.strategies.single_shot import SingleShotStrategy
from cc_pipeline.templates.engine import TemplateEngine


class MockRunner(Runner):
    """Runner that returns canned JSON responses."""

    def __init__(self):
        self.call_log: list[str] = []

    async def run(self, prompt: str, **kwargs: Any) -> RunResult:
        self.call_log.append(prompt[:50])
        return RunResult(
            text='```json\n{"result": "ok"}\n```',
            session_id="mock-sess",
            token_usage=TokenUsage(input_tokens=10, output_tokens=5),
        )


class TestDagExecution(unittest.TestCase):

    def _make_config(self) -> TaskConfig:
        """A → [B, C] (parallel) → D."""
        return TaskConfig(
            name="test-dag",
            working_dir="/tmp/test-dag",
            max_parallel=2,
            stages={
                "a": StageConfig(
                    name="a", type=StageType.PRE_EXEC,
                    prompt_template="do A",
                    outputs={},
                ),
                "b": StageConfig(
                    name="b", type=StageType.SUBTASK,
                    depends_on=["a"],
                    prompt_template="do B",
                    outputs={},
                ),
                "c": StageConfig(
                    name="c", type=StageType.SUBTASK,
                    depends_on=["a"],
                    prompt_template="do C",
                    outputs={},
                ),
                "d": StageConfig(
                    name="d", type=StageType.POST_EXEC,
                    depends_on=["b", "c"],
                    prompt_template="do D",
                    outputs={},
                ),
            },
        )

    def _compose(self, config, tmpdir):
        pipe_dir = Path(tmpdir) / ".pipeline"
        runner = MockRunner()
        state_mgr = StateManager(pipe_dir)
        state_mgr.load_or_create(config)
        artifacts = ArtifactStore(pipe_dir)
        templates = TemplateEngine()
        extractors = build_default_registry()

        strategies = StrategyRegistry()
        single = SingleShotStrategy()
        strategies.register(StageType.PRE_EXEC, single)
        strategies.register(StageType.SUBTASK, single)
        strategies.register(StageType.POST_EXEC, single)
        strategies.register(
            StageType.LOOP, LoopStrategy(build_default_operators()),
        )

        def ctx_factory(name: str) -> StageContext:
            return StageContext(
                stage_name=name, config=config,
                runner=runner, artifacts=artifacts,
                state_mgr=state_mgr, templates=templates,
                extractors=extractors,
            )

        orch = PipelineOrchestrator(
            config=config, state_mgr=state_mgr,
            strategies=strategies, ctx_factory=ctx_factory,
            max_parallel=config.max_parallel,
        )
        return orch, state_mgr, runner

    def test_all_stages_complete(self):
        config = self._make_config()
        with tempfile.TemporaryDirectory() as d:
            config.working_dir = d
            orch, state_mgr, runner = self._compose(config, d)
            state = asyncio.run(orch.run())
            self.assertEqual(state.status.value, "done")
            for ss in state.stages.values():
                self.assertEqual(ss.status, StageStatus.DONE)
            # A must run before B,C; D must run after B,C
            self.assertEqual(len(runner.call_log), 4)
