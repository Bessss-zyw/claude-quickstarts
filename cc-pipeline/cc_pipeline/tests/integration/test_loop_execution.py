"""Integration test: Loop stage execution with mock runner.

Verifies convergence, max_iterations, and iteration state tracking.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from cc_pipeline.artifacts.store import ArtifactStore
from cc_pipeline.config.models import (
    ConvergenceSpec,
    OutputSpec,
    StageConfig,
    TaskConfig,
)
from cc_pipeline.constants.enums import (
    ConvergenceOp,
    OutputType,
    PermissionLevel,
    StageStatus,
    StageType,
)
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


class ConvergingRunner(Runner):
    """Returns increasing score until convergence."""

    def __init__(self):
        self._call_count = 0

    async def run(self, prompt: str, **kwargs: Any) -> RunResult:
        self._call_count += 1
        score = min(0.3 * self._call_count, 1.0)
        return RunResult(
            text=f'{{"score": {score}}}',
            token_usage=TokenUsage(input_tokens=5, output_tokens=3),
        )

    @property
    def call_count(self) -> int:
        return self._call_count


class TestLoopExecution(unittest.TestCase):

    def _make_config(self) -> TaskConfig:
        return TaskConfig(
            name="test-loop",
            working_dir="/tmp/test-loop",
            max_parallel=1,
            stages={
                "opt": StageConfig(
                    name="opt",
                    type=StageType.LOOP,
                    max_iterations=10,
                    convergence=ConvergenceSpec(
                        metric="outputs.score",
                        threshold=0.9,
                        operator=ConvergenceOp.GE,
                    ),
                    prompt_template="optimize",
                    outputs={
                        "score": OutputSpec(name="score", type=OutputType.NUMBER),
                    },
                ),
            },
        )

    def _compose(self, config, tmpdir):
        pipe_dir = Path(tmpdir) / ".pipeline"
        runner = ConvergingRunner()
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
            max_parallel=1,
        )
        return orch, state_mgr, runner

    def test_converges(self):
        config = self._make_config()
        with tempfile.TemporaryDirectory() as d:
            config.working_dir = d
            orch, state_mgr, runner = self._compose(config, d)
            state = asyncio.run(orch.run())
            self.assertEqual(state.status.value, "done")
            loop = state.stages["opt"].loop
            self.assertTrue(loop.converged)
            # 0.3*3=0.8999 (float), 0.3*4=1.2→min=1.0 → converges at iter 4
            self.assertEqual(runner.call_count, 4)

    def test_max_iterations(self):
        config = self._make_config()
        # Set threshold impossibly high
        config.stages["opt"].convergence = ConvergenceSpec(
            metric="outputs.score",
            threshold=999.0,
            operator=ConvergenceOp.GE,
        )
        config.stages["opt"].max_iterations = 3
        with tempfile.TemporaryDirectory() as d:
            config.working_dir = d
            orch, state_mgr, runner = self._compose(config, d)
            state = asyncio.run(orch.run())
            loop = state.stages["opt"].loop
            self.assertFalse(loop.converged)
            self.assertEqual(runner.call_count, 3)
