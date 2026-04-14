"""Integration test: Crash recovery.

Simulates a crash mid-pipeline and verifies resume skips done stages.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any

from cc_pipeline.artifacts.store import ArtifactStore
from cc_pipeline.config.models import StageConfig, TaskConfig
from cc_pipeline.constants.enums import StageStatus, StageType
from cc_pipeline.extractors.base import build_default_registry
from cc_pipeline.orchestration.orchestrator import PipelineOrchestrator
from cc_pipeline.runners.base import Runner, RunResult
from cc_pipeline.state.manager import StateManager
from cc_pipeline.state.models import TokenUsage
from cc_pipeline.strategies.base import StageContext, StrategyRegistry
from cc_pipeline.strategies.convergence import build_default_operators
from cc_pipeline.strategies.loop import LoopStrategy
from cc_pipeline.strategies.single_shot import SingleShotStrategy
from cc_pipeline.templates.engine import TemplateEngine


class CountingRunner(Runner):
    def __init__(self):
        self.call_count = 0

    async def run(self, prompt: str, **kwargs: Any) -> RunResult:
        self.call_count += 1
        return RunResult(
            text='{"result": "ok"}',
            token_usage=TokenUsage(input_tokens=1, output_tokens=1),
        )


class TestCrashRecovery(unittest.TestCase):

    def _make_config(self) -> TaskConfig:
        return TaskConfig(
            name="crash-test",
            working_dir="/tmp/crash-test",
            stages={
                "s1": StageConfig(name="s1", prompt_template="s1"),
                "s2": StageConfig(
                    name="s2", depends_on=["s1"], prompt_template="s2",
                ),
            },
        )

    def _compose(self, config, tmpdir, runner=None):
        pipe_dir = Path(tmpdir) / ".pipeline"
        runner = runner or CountingRunner()
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
        )
        return orch, state_mgr, runner

    def test_resume_skips_done_stage(self):
        config = self._make_config()
        with tempfile.TemporaryDirectory() as d:
            config.working_dir = d
            pipe_dir = Path(d) / ".pipeline"

            # Simulate: s1 done, s2 was running (crashed)
            m1 = StateManager(pipe_dir)
            m1.load_or_create(config)
            m1.mark_stage_running("s1")
            m1.mark_stage_done("s1", outputs={"r": "ok"})
            m1.mark_stage_running("s2")  # "crash" here

            # Resume: s1 should be skipped, s2 re-run
            runner = CountingRunner()
            orch, state_mgr, _ = self._compose(config, d, runner)
            state = asyncio.run(orch.run())

            self.assertEqual(state.status.value, "done")
            # Only s2 should have been executed (s1 was already done)
            self.assertEqual(runner.call_count, 1)
