"""Tests for multi-stage loop support."""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from cc_pipeline.config.loader import load_task_config
from cc_pipeline.config.models import (
    ConvergenceSpec,
    OutputSpec,
    StageConfig,
    TaskConfig,
)
from cc_pipeline.config.validator import DagValidationError
from cc_pipeline.constants.enums import (
    ConvergenceOp,
    IterationStatus,
    OutputType,
    PermissionLevel,
    StageStatus,
    StageType,
)
from cc_pipeline.state.models import (
    IterationState,
    LoopState,
    PipelineState,
    RunResult,
    StageState,
    SubStageState,
    TokenUsage,
)
from cc_pipeline.state.recovery import recover_running_stages
from cc_pipeline.state.serialization import (
    _serialize_loop,
    _deserialize_loop,
)
from cc_pipeline.strategies.convergence import build_default_operators
from cc_pipeline.strategies.loop import LoopStrategy, _extract_metric


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

class TestMultiStageConfigParsing(unittest.TestCase):

    def _write_yaml(self, content: str) -> Path:
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.write(fd, content.encode())
        os.close(fd)
        return Path(path)

    def test_parse_loop_with_sub_stages(self):
        y = """
name: t
working_dir: /tmp
stages:
  opt:
    type: loop
    max_iterations: 3
    convergence:
      metric: "test.outputs.speedup"
      threshold: 2.0
      operator: ">="
    stages:
      code:
        prompt_template: "code it"
        outputs:
          changes: {type: text}
      test:
        depends_on: [code]
        prompt_template: "test it"
        outputs:
          speedup: {type: number}
"""
        p = self._write_yaml(y)
        try:
            cfg = load_task_config(p)
            opt = cfg.stages["opt"]
            self.assertEqual(opt.type, StageType.LOOP)
            self.assertEqual(len(opt.sub_stages), 2)
            self.assertIn("code", opt.sub_stages)
            self.assertIn("test", opt.sub_stages)
            self.assertEqual(opt.sub_stages["test"].depends_on, ["code"])
        finally:
            os.unlink(p)

    def test_sub_stages_inherit_parent_defaults(self):
        y = """
name: t
working_dir: /tmp
defaults:
  effort: low
stages:
  opt:
    type: loop
    model: claude-opus-4-5
    stages:
      code:
        prompt_template: "go"
        outputs: {}
"""
        p = self._write_yaml(y)
        try:
            cfg = load_task_config(p)
            sub = cfg.stages["opt"].sub_stages["code"]
            self.assertEqual(sub.model, "claude-opus-4-5")
            self.assertEqual(sub.effort, "low")
        finally:
            os.unlink(p)

    def test_loop_without_sub_stages_unchanged(self):
        y = """
name: t
working_dir: /tmp
stages:
  opt:
    type: loop
    max_iterations: 5
    prompt_template: "go"
    outputs:
      score: {type: number}
"""
        p = self._write_yaml(y)
        try:
            cfg = load_task_config(p)
            self.assertEqual(cfg.stages["opt"].sub_stages, {})
        finally:
            os.unlink(p)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestMultiStageValidation(unittest.TestCase):

    def _write_yaml(self, content: str) -> Path:
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.write(fd, content.encode())
        os.close(fd)
        return Path(path)

    def test_sub_stage_dep_unknown_sibling(self):
        y = """
name: t
working_dir: /tmp
stages:
  opt:
    type: loop
    stages:
      code:
        depends_on: [missing]
        prompt_template: "go"
"""
        p = self._write_yaml(y)
        try:
            with self.assertRaises(DagValidationError):
                load_task_config(p)
        finally:
            os.unlink(p)

    def test_sub_stage_cycle(self):
        y = """
name: t
working_dir: /tmp
stages:
  opt:
    type: loop
    stages:
      a:
        depends_on: [b]
        prompt_template: "go"
      b:
        depends_on: [a]
        prompt_template: "go"
"""
        p = self._write_yaml(y)
        try:
            with self.assertRaises(DagValidationError):
                load_task_config(p)
        finally:
            os.unlink(p)

    def test_sub_stage_no_nested_loop(self):
        y = """
name: t
working_dir: /tmp
stages:
  opt:
    type: loop
    stages:
      inner:
        type: loop
        prompt_template: "go"
"""
        p = self._write_yaml(y)
        try:
            with self.assertRaises(DagValidationError):
                load_task_config(p)
        finally:
            os.unlink(p)


# ---------------------------------------------------------------------------
# State serialization
# ---------------------------------------------------------------------------

class TestSubStageStateSerialization(unittest.TestCase):

    def test_round_trip(self):
        ss = SubStageState(
            name="code",
            status=StageStatus.DONE,
            outputs={"changes": "refactored loop"},
            session_id="s1",
            token_usage=TokenUsage(input_tokens=100, output_tokens=50),
        )
        loop = LoopState(
            current_iteration=0,
            max_iterations=3,
            iterations={
                "0": IterationState(
                    status=IterationStatus.DONE,
                    outputs={"code": {"changes": "refactored loop"}},
                    sub_stages={"code": ss},
                ),
            },
        )
        serialized = _serialize_loop(loop)
        deserialized = _deserialize_loop(serialized)
        self.assertIn("code", deserialized.iterations["0"].sub_stages)
        ds = deserialized.iterations["0"].sub_stages["code"]
        self.assertEqual(ds.status, StageStatus.DONE)
        self.assertEqual(ds.outputs["changes"], "refactored loop")
        self.assertEqual(ds.token_usage.input_tokens, 100)

    def test_empty_sub_stages(self):
        """Backward compatibility: no sub_stages key in serialized data."""
        loop = LoopState(
            iterations={
                "0": IterationState(status=IterationStatus.DONE),
            },
        )
        serialized = _serialize_loop(loop)
        deserialized = _deserialize_loop(serialized)
        self.assertEqual(deserialized.iterations["0"].sub_stages, {})


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

class TestSubStageRecovery(unittest.TestCase):

    def test_running_sub_stage_reset(self):
        ss = SubStageState(name="code", status=StageStatus.RUNNING)
        state = PipelineState(
            pipeline_name="t",
            stages={
                "opt": StageState(
                    name="opt",
                    status=StageStatus.RUNNING,
                    loop=LoopState(iterations={
                        "0": IterationState(
                            status=IterationStatus.RUNNING,
                            sub_stages={"code": ss},
                        ),
                    }),
                ),
            },
        )
        reset = recover_running_stages(state)
        self.assertEqual(reset, 1)
        self.assertEqual(ss.status, StageStatus.PENDING)


# ---------------------------------------------------------------------------
# Metric extraction
# ---------------------------------------------------------------------------

class TestMetricExtraction(unittest.TestCase):

    def test_single_stage_metric(self):
        outputs = {"speedup": 1.8}
        val = _extract_metric("outputs.speedup", outputs, multi=False)
        self.assertAlmostEqual(val, 1.8)

    def test_multi_stage_metric(self):
        outputs = {"test": {"speedup": 2.1}}
        val = _extract_metric("test.outputs.speedup", outputs, multi=True)
        self.assertAlmostEqual(val, 2.1)

    def test_multi_stage_metric_missing(self):
        outputs = {"test": {"speedup": 2.1}}
        val = _extract_metric("review.outputs.score", outputs, multi=True)
        self.assertIsNone(val)


# ---------------------------------------------------------------------------
# Multi-stage loop execution
# ---------------------------------------------------------------------------

class TestMultiStageLoopExecution(unittest.TestCase):

    def _make_sub_stages(self):
        return {
            "code": StageConfig(
                name="code",
                prompt_template="code it",
                outputs={
                    "changes": OutputSpec(
                        name="changes", type=OutputType.TEXT,
                    ),
                },
            ),
            "test": StageConfig(
                name="test",
                depends_on=["code"],
                prompt_template="test it",
                outputs={
                    "speedup": OutputSpec(
                        name="speedup", type=OutputType.NUMBER,
                    ),
                },
            ),
            "review": StageConfig(
                name="review",
                depends_on=["code", "test"],
                prompt_template="review it",
                outputs={
                    "feedback": OutputSpec(
                        name="feedback", type=OutputType.TEXT,
                    ),
                },
            ),
        }

    def _make_context(self, sub_stages):
        stage_cfg = StageConfig(
            name="optimize",
            type=StageType.LOOP,
            max_iterations=2,
            convergence=ConvergenceSpec(
                metric="test.outputs.speedup",
                threshold=1.5,
                operator=ConvergenceOp.GE,
            ),
            sub_stages=sub_stages,
            depends_on=["analyze"],
        )
        config = TaskConfig(
            name="test-pipeline",
            working_dir="/tmp",
            stages={"optimize": stage_cfg},
        )

        loop_state = LoopState(max_iterations=2)
        stage_state = StageState(name="optimize", loop=loop_state)
        pipeline_state = PipelineState(
            pipeline_name="test-pipeline",
            stages={"optimize": stage_state},
        )

        state_mgr = MagicMock()
        state_mgr.state = pipeline_state
        state_mgr.save = MagicMock()

        artifacts = MagicMock()
        artifacts.get_stage_outputs.return_value = {"bottlenecks": ["a"]}
        artifacts.get_iteration_outputs.return_value = None

        runner = AsyncMock()
        templates = MagicMock()
        templates.render.side_effect = lambda t, c: t
        extractors = MagicMock()

        ctx = MagicMock()
        ctx.stage_name = "optimize"
        ctx.config = config
        ctx.runner = runner
        ctx.artifacts = artifacts
        ctx.state_mgr = state_mgr
        ctx.templates = templates
        ctx.extractors = extractors

        return ctx, stage_cfg

    def test_sub_stage_dependency_ordering(self):
        """Sub-stages execute in dependency order: code -> test -> review."""
        sub_stages = self._make_sub_stages()
        ctx, stage_cfg = self._make_context(sub_stages)

        call_order = []

        async def mock_run_cc_once(
            ctx, sub_cfg, *, session_name, persist_session,
            loop_context, iteration, upstream, sub_stage,
        ):
            call_order.append(sub_cfg.name)
            if sub_cfg.name == "code":
                outputs = {"changes": "optimized"}
            elif sub_cfg.name == "test":
                outputs = {"speedup": 2.0}
            else:
                outputs = {"feedback": "lgtm"}
            return outputs, RunResult(
                text="ok", token_usage=TokenUsage(),
            )

        strategy = LoopStrategy(build_default_operators())

        with patch(
            "cc_pipeline.strategies.loop.run_cc_once",
            side_effect=mock_run_cc_once,
        ):
            result = asyncio.run(
                strategy.execute(ctx, stage_cfg),
            )

        # code must come before test; test must come before review
        self.assertLess(call_order.index("code"), call_order.index("test"))
        self.assertLess(call_order.index("test"), call_order.index("review"))

    def test_convergence_with_multi_stage(self):
        """Converge on first iteration when speedup >= 1.5."""
        sub_stages = self._make_sub_stages()
        ctx, stage_cfg = self._make_context(sub_stages)

        async def mock_run_cc_once(
            ctx, sub_cfg, *, session_name, persist_session,
            loop_context, iteration, upstream, sub_stage,
        ):
            if sub_cfg.name == "code":
                outputs = {"changes": "done"}
            elif sub_cfg.name == "test":
                outputs = {"speedup": 2.0}
            else:
                outputs = {"feedback": "good"}
            return outputs, RunResult(
                text="ok", token_usage=TokenUsage(),
            )

        strategy = LoopStrategy(build_default_operators())

        with patch(
            "cc_pipeline.strategies.loop.run_cc_once",
            side_effect=mock_run_cc_once,
        ):
            result = asyncio.run(
                strategy.execute(ctx, stage_cfg),
            )

        # Should converge on first iteration
        self.assertIn("test", result)
        self.assertAlmostEqual(result["test"]["speedup"], 2.0)
        # mark_loop_converged should have been called
        ctx.state_mgr.mark_loop_converged.assert_called_once()

    def test_backward_compat_single_stage_loop(self):
        """Loop without sub_stages still works."""
        stage_cfg = StageConfig(
            name="opt",
            type=StageType.LOOP,
            max_iterations=2,
            convergence=ConvergenceSpec(
                metric="outputs.score",
                threshold=0.9,
                operator=ConvergenceOp.GE,
            ),
            prompt_template="optimize",
            outputs={
                "score": OutputSpec(name="score", type=OutputType.NUMBER),
            },
        )
        config = TaskConfig(
            name="t", working_dir="/tmp",
            stages={"opt": stage_cfg},
        )
        loop_state = LoopState(max_iterations=2)
        stage_state = StageState(name="opt", loop=loop_state)
        pipeline_state = PipelineState(
            pipeline_name="t",
            stages={"opt": stage_state},
        )

        state_mgr = MagicMock()
        state_mgr.state = pipeline_state

        artifacts = MagicMock()
        artifacts.get_iteration_outputs.return_value = None

        ctx = MagicMock()
        ctx.stage_name = "opt"
        ctx.config = config
        ctx.state_mgr = state_mgr
        ctx.artifacts = artifacts

        async def mock_run_cc_once(
            ctx, cfg, *, session_name, persist_session,
            loop_context, iteration, **kwargs,
        ):
            return {"score": 0.95}, RunResult(
                text="ok", token_usage=TokenUsage(),
            )

        strategy = LoopStrategy(build_default_operators())

        with patch(
            "cc_pipeline.strategies.loop.run_cc_once",
            side_effect=mock_run_cc_once,
        ):
            result = asyncio.run(
                strategy.execute(ctx, stage_cfg),
            )

        self.assertAlmostEqual(result["score"], 0.95)
        ctx.state_mgr.mark_loop_converged.assert_called_once()
