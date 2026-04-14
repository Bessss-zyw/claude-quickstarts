"""Tests for artifact store components."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cc_pipeline.artifacts.store import ArtifactStore
from cc_pipeline.artifacts.input_resolver import InputResolver
from cc_pipeline.runners.base import RunResult
from cc_pipeline.state.models import TokenUsage


class TestArtifactStore(unittest.TestCase):

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            store = ArtifactStore(Path(d) / ".pipeline")
            result = RunResult(
                text="hello",
                session_id="s1",
                token_usage=TokenUsage(input_tokens=10, output_tokens=5),
                duration_seconds=1.0,
            )
            store.save_result("stage1", result, outputs={"k": "v"})
            loaded = store.get_stage_outputs("stage1")
            self.assertEqual(loaded["k"], "v")

    def test_iteration_outputs(self):
        with tempfile.TemporaryDirectory() as d:
            store = ArtifactStore(Path(d) / ".pipeline")
            result = RunResult(text="iter")
            store.save_result("loop", result, outputs={"i": 1}, iteration=0)
            loaded = store.get_iteration_outputs("loop", 0)
            self.assertEqual(loaded["i"], 1)


class TestInputResolver(unittest.TestCase):

    def test_file_not_found(self):
        resolver = InputResolver()
        with self.assertRaises(FileNotFoundError):
            resolver._resolve_file("nonexistent.txt", "/tmp")
