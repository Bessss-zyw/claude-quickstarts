"""Tests for state management, transitions, and recovery."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cc_pipeline.config.models import StageConfig, TaskConfig
from cc_pipeline.constants.enums import StageStatus, StageType
from cc_pipeline.state.manager import StateManager
from cc_pipeline.state.models import StageState, TokenUsage
from cc_pipeline.state import transitions as tx


class TestTransitions(unittest.TestCase):

    def test_stage_to_running(self):
        s = StageState(name="s1")
        tx.stage_to_running(s)
        self.assertEqual(s.status, StageStatus.RUNNING)
        self.assertIsNotNone(s.started_at)

    def test_stage_to_done(self):
        s = StageState(name="s1")
        tx.stage_to_done(s, outputs={"x": 1}, session_id="abc")
        self.assertEqual(s.status, StageStatus.DONE)
        self.assertEqual(s.outputs, {"x": 1})
        self.assertEqual(s.session_id, "abc")

    def test_stage_to_failed(self):
        s = StageState(name="s1")
        tx.stage_to_failed(s, error="boom")
        self.assertEqual(s.status, StageStatus.FAILED)
        self.assertEqual(s.error, "boom")
        self.assertEqual(s.retry_count, 1)


class TestStateManager(unittest.TestCase):

    def _make_config(self):
        return TaskConfig(
            name="test",
            stages={
                "s1": StageConfig(name="s1"),
                "s2": StageConfig(name="s2", depends_on=["s1"]),
            },
        )

    def test_create_and_persist(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = StateManager(Path(d) / ".pipeline")
            cfg = self._make_config()
            state = mgr.load_or_create(cfg)
            self.assertEqual(len(state.stages), 2)
            self.assertTrue((Path(d) / ".pipeline" / "state.json").exists())

    def test_stage_transitions(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = StateManager(Path(d) / ".pipeline")
            mgr.load_or_create(self._make_config())
            mgr.mark_stage_running("s1")
            self.assertEqual(mgr.state.stages["s1"].status, StageStatus.RUNNING)
            mgr.mark_stage_done("s1", outputs={"r": "ok"})
            self.assertEqual(mgr.state.stages["s1"].status, StageStatus.DONE)

    def test_crash_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            pipe_dir = Path(d) / ".pipeline"
            cfg = self._make_config()
            m1 = StateManager(pipe_dir)
            m1.load_or_create(cfg)
            m1.mark_stage_running("s1")
            m1.mark_stage_done("s2", outputs={"x": 1})

            m2 = StateManager(pipe_dir)
            state = m2.load_or_create(cfg)
            self.assertEqual(state.stages["s1"].status, StageStatus.PENDING)
            self.assertEqual(state.stages["s2"].status, StageStatus.DONE)

    def test_token_accumulation(self):
        with tempfile.TemporaryDirectory() as d:
            mgr = StateManager(Path(d) / ".pipeline")
            mgr.load_or_create(self._make_config())
            mgr.mark_stage_running("s1")
            mgr.mark_stage_done(
                "s1",
                token_usage=TokenUsage(input_tokens=100, output_tokens=50),
            )
            self.assertEqual(mgr.state.total_token_usage.input_tokens, 100)
            self.assertEqual(mgr.state.total_token_usage.total_tokens, 150)
