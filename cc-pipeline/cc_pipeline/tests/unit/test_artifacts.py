"""Tests for artifact store components."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cc_pipeline.artifacts.layout import ArtifactLayout
from cc_pipeline.artifacts.store import ArtifactStore
from cc_pipeline.artifacts.input_resolver import InputResolver
from cc_pipeline.artifacts.summary import (
    generate_summary_json,
    generate_summary_md,
)
from cc_pipeline.constants.enums import PipelineStatus, StageStatus
from cc_pipeline.runners.base import RunResult
from cc_pipeline.state.models import (
    PipelineState,
    StageState,
    TokenUsage,
)


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

    def test_stages_dir_layout(self):
        """Verify stages/ dir is used instead of artifacts/ + logs/."""
        with tempfile.TemporaryDirectory() as d:
            pipe_dir = Path(d) / ".pipeline"
            store = ArtifactStore(pipe_dir)
            self.assertTrue((pipe_dir / "stages").is_dir())
            self.assertFalse((pipe_dir / "artifacts").exists())
            self.assertFalse((pipe_dir / "logs").exists())

    def test_new_file_names(self):
        """Verify response.txt, outputs.json, prompt.txt, events.ndjson."""
        with tempfile.TemporaryDirectory() as d:
            pipe_dir = Path(d) / ".pipeline"
            store = ArtifactStore(pipe_dir)
            result = RunResult(
                text="resp", session_id="s1",
                token_usage=TokenUsage(input_tokens=1, output_tokens=1),
                raw_events=[{"type": "msg"}],
            )
            store.save_result(
                "s1", result, outputs={"x": 1}, prompt="my prompt",
            )
            store.save_log("s1", result)

            stage = pipe_dir / "stages" / "s1"
            self.assertTrue((stage / "response.txt").exists())
            self.assertTrue((stage / "prompt.txt").exists())
            self.assertTrue((stage / "outputs.json").exists())
            self.assertTrue((stage / "meta.json").exists())
            self.assertTrue((stage / "events.ndjson").exists())
            # Old names must NOT exist
            self.assertFalse((stage / "raw_response.txt").exists())
            self.assertFalse((stage / "output.json").exists())

            self.assertEqual(
                (stage / "prompt.txt").read_text(), "my prompt",
            )

    def test_per_iteration_log(self):
        """Log files are per-stage/iteration, not shared."""
        with tempfile.TemporaryDirectory() as d:
            pipe_dir = Path(d) / ".pipeline"
            store = ArtifactStore(pipe_dir)
            result = RunResult(
                text="it", raw_events=[{"e": 1}],
            )
            store.save_log("opt", result, iteration=0, sub_stage="code")
            log_path = (
                pipe_dir / "stages" / "opt" / "iter_0" / "code"
                / "events.ndjson"
            )
            self.assertTrue(log_path.exists())


class TestInputResolver(unittest.TestCase):

    def test_file_not_found(self):
        resolver = InputResolver()
        with self.assertRaises(FileNotFoundError):
            resolver._resolve_file("nonexistent.txt", "/tmp")


class TestSummaryGeneration(unittest.TestCase):

    def test_summary_json(self):
        with tempfile.TemporaryDirectory() as d:
            pipe_dir = Path(d) / ".pipeline"
            pipe_dir.mkdir()
            layout = ArtifactLayout(pipe_dir)

            state = PipelineState(
                pipeline_name="my-pipe",
                status=PipelineStatus.DONE,
                started_at="2026-01-01T00:00:00+00:00",
                finished_at="2026-01-01T00:05:00+00:00",
                total_token_usage=TokenUsage(
                    input_tokens=1000, output_tokens=500,
                ),
                total_cost_usd=1.23,
                stages={
                    "analyze": StageState(
                        name="analyze",
                        status=StageStatus.DONE,
                        started_at="2026-01-01T00:00:00+00:00",
                        finished_at="2026-01-01T00:01:00+00:00",
                        token_usage=TokenUsage(
                            input_tokens=500, output_tokens=200,
                        ),
                        outputs={"result": "ok"},
                    ),
                },
            )

            generate_summary_json(state, layout)

            self.assertTrue(layout.summary_json_path.exists())
            data = json.loads(layout.summary_json_path.read_text())
            self.assertEqual(data["pipeline"], "my-pipe")
            self.assertEqual(data["status"], "done")
            self.assertEqual(data["duration_s"], 300.0)
            self.assertEqual(data["total_cost_usd"], 1.23)
            self.assertIn("analyze", data["stages"])
            self.assertEqual(
                data["stages"]["analyze"]["status"], "done",
            )

    def test_summary_md(self):
        with tempfile.TemporaryDirectory() as d:
            pipe_dir = Path(d) / ".pipeline"
            pipe_dir.mkdir()
            layout = ArtifactLayout(pipe_dir)

            state = PipelineState(
                pipeline_name="my-pipe",
                status=PipelineStatus.DONE,
                started_at="2026-01-01T00:00:00+00:00",
                finished_at="2026-01-01T00:05:00+00:00",
                total_token_usage=TokenUsage(
                    input_tokens=1000, output_tokens=500,
                ),
                stages={
                    "analyze": StageState(
                        name="analyze",
                        status=StageStatus.DONE,
                        started_at="2026-01-01T00:00:00+00:00",
                        finished_at="2026-01-01T00:01:00+00:00",
                        token_usage=TokenUsage(
                            input_tokens=500, output_tokens=200,
                        ),
                    ),
                },
            )

            generate_summary_md(state, layout)

            self.assertTrue(layout.summary_md_path.exists())
            md = layout.summary_md_path.read_text()
            self.assertIn("# Pipeline: my-pipe", md)
            self.assertIn("Status: done", md)
            self.assertIn("analyze", md)
