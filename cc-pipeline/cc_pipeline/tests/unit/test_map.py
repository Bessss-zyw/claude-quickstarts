"""Tests for map stage support."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from cc_pipeline.config.loader import load_task_config
from cc_pipeline.config.models import (
    OutputSpec,
    StageConfig,
    TaskConfig,
)
from cc_pipeline.config.validator import DagValidationError
from cc_pipeline.constants.enums import (
    OutputType,
    PermissionLevel,
    StageStatus,
    StageType,
)
from cc_pipeline.state.models import (
    ItemState,
    MapState,
    PipelineState,
    RunResult,
    StageState,
    SubStageState,
    TokenUsage,
)
from cc_pipeline.state.recovery import recover_running_stages
from cc_pipeline.state.serialization import (
    _serialize_map,
    _deserialize_map,
)
from cc_pipeline.strategies.map import MapStrategy, _load_items


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

class TestMapConfigParsing(unittest.TestCase):

    def _write_yaml(self, content: str) -> Path:
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.write(fd, content.encode())
        os.close(fd)
        return Path(path)

    def test_parse_map_with_inline_items(self):
        y = """
name: t
working_dir: /tmp
stages:
  process:
    type: map
    items:
      - title: "A"
        url: "http://a"
      - title: "B"
        url: "http://b"
    max_parallel: 2
    stages:
      download:
        prompt_template: "download {{item.url}}"
        outputs:
          path: {type: text}
      analyze:
        depends_on: [download]
        prompt_template: "analyze {{map.stages.download.outputs.path}}"
        outputs:
          summary: {type: text}
"""
        p = self._write_yaml(y)
        try:
            cfg = load_task_config(p)
            proc = cfg.stages["process"]
            self.assertEqual(proc.type, StageType.MAP)
            self.assertEqual(len(proc.items), 2)
            self.assertEqual(proc.map_max_parallel, 2)
            self.assertEqual(len(proc.sub_stages), 2)
            self.assertIn("download", proc.sub_stages)
            self.assertIn("analyze", proc.sub_stages)
            self.assertEqual(
                proc.sub_stages["analyze"].depends_on, ["download"],
            )
        finally:
            os.unlink(p)

    def test_parse_map_with_items_file_path(self):
        y = """
name: t
working_dir: /tmp
stages:
  process:
    type: map
    items: sessions.json
    stages:
      run:
        prompt_template: "run {{item.name}}"
        outputs:
          result: {type: text}
"""
        p = self._write_yaml(y)
        try:
            cfg = load_task_config(p)
            proc = cfg.stages["process"]
            self.assertEqual(proc.items, "sessions.json")
        finally:
            os.unlink(p)

    def test_sub_stages_inherit_parent_defaults(self):
        y = """
name: t
working_dir: /tmp
defaults:
  effort: low
stages:
  process:
    type: map
    model: claude-opus-4-5
    items:
      - x: 1
    stages:
      step:
        prompt_template: "go"
        outputs: {}
"""
        p = self._write_yaml(y)
        try:
            cfg = load_task_config(p)
            sub = cfg.stages["process"].sub_stages["step"]
            self.assertEqual(sub.model, "claude-opus-4-5")
            self.assertEqual(sub.effort, "low")
        finally:
            os.unlink(p)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestMapValidation(unittest.TestCase):

    def _write_yaml(self, content: str) -> Path:
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.write(fd, content.encode())
        os.close(fd)
        return Path(path)

    def test_map_without_items_error(self):
        y = """
name: t
working_dir: /tmp
stages:
  process:
    type: map
    stages:
      step:
        prompt_template: "go"
"""
        p = self._write_yaml(y)
        try:
            with self.assertRaises(DagValidationError):
                load_task_config(p)
        finally:
            os.unlink(p)

    def test_map_without_stages_error(self):
        y = """
name: t
working_dir: /tmp
stages:
  process:
    type: map
    items:
      - x: 1
    prompt_template: "go"
"""
        p = self._write_yaml(y)
        try:
            with self.assertRaises(DagValidationError):
                load_task_config(p)
        finally:
            os.unlink(p)

    def test_sub_stage_cycle_error(self):
        y = """
name: t
working_dir: /tmp
stages:
  process:
    type: map
    items:
      - x: 1
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

    def test_nested_map_error(self):
        y = """
name: t
working_dir: /tmp
stages:
  process:
    type: map
    items:
      - x: 1
    stages:
      inner:
        type: map
        items:
          - y: 2
        prompt_template: "go"
"""
        p = self._write_yaml(y)
        try:
            with self.assertRaises(DagValidationError):
                load_task_config(p)
        finally:
            os.unlink(p)

    def test_nested_loop_in_map_error(self):
        y = """
name: t
working_dir: /tmp
stages:
  process:
    type: map
    items:
      - x: 1
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

class TestMapStateSerialization(unittest.TestCase):

    def test_round_trip(self):
        ss = SubStageState(
            name="download",
            status=StageStatus.DONE,
            outputs={"path": "/tmp/video.mp4"},
            session_id="s1",
            token_usage=TokenUsage(input_tokens=100, output_tokens=50),
        )
        ms = MapState(
            total_items=2,
            items={
                "0": ItemState(
                    index=0,
                    status=StageStatus.DONE,
                    outputs={
                        "download": {"path": "/tmp/video.mp4"},
                    },
                    sub_stages={"download": ss},
                ),
                "1": ItemState(
                    index=1,
                    status=StageStatus.PENDING,
                ),
            },
        )
        serialized = _serialize_map(ms)
        deserialized = _deserialize_map(serialized)

        self.assertEqual(deserialized.total_items, 2)
        self.assertIn("0", deserialized.items)
        self.assertIn("1", deserialized.items)

        item0 = deserialized.items["0"]
        self.assertEqual(item0.index, 0)
        self.assertEqual(item0.status, StageStatus.DONE)
        self.assertIn("download", item0.sub_stages)
        ds = item0.sub_stages["download"]
        self.assertEqual(ds.status, StageStatus.DONE)
        self.assertEqual(ds.outputs["path"], "/tmp/video.mp4")
        self.assertEqual(ds.token_usage.input_tokens, 100)

        item1 = deserialized.items["1"]
        self.assertEqual(item1.status, StageStatus.PENDING)

    def test_none_map_state(self):
        self.assertIsNone(_serialize_map(None))
        self.assertIsNone(_deserialize_map(None))

    def test_empty_map_state(self):
        ms = MapState()
        serialized = _serialize_map(ms)
        deserialized = _deserialize_map(serialized)
        self.assertEqual(deserialized.total_items, 0)
        self.assertEqual(deserialized.items, {})


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

class TestMapRecovery(unittest.TestCase):

    def test_running_items_reset(self):
        ss = SubStageState(name="download", status=StageStatus.RUNNING)
        item = ItemState(
            index=0,
            status=StageStatus.RUNNING,
            sub_stages={"download": ss},
        )
        state = PipelineState(
            pipeline_name="t",
            stages={
                "process": StageState(
                    name="process",
                    status=StageStatus.RUNNING,
                    map_state=MapState(
                        total_items=1,
                        items={"0": item},
                    ),
                ),
            },
        )
        reset = recover_running_stages(state)
        self.assertEqual(reset, 1)
        self.assertEqual(item.status, StageStatus.PENDING)
        self.assertEqual(ss.status, StageStatus.PENDING)

    def test_done_items_preserved(self):
        item = ItemState(index=0, status=StageStatus.DONE)
        state = PipelineState(
            pipeline_name="t",
            stages={
                "process": StageState(
                    name="process",
                    status=StageStatus.DONE,
                    map_state=MapState(
                        total_items=1,
                        items={"0": item},
                    ),
                ),
            },
        )
        reset = recover_running_stages(state)
        self.assertEqual(reset, 0)
        self.assertEqual(item.status, StageStatus.DONE)


# ---------------------------------------------------------------------------
# _load_items
# ---------------------------------------------------------------------------

class TestLoadItems(unittest.TestCase):

    def test_inline_list(self):
        items = _load_items(
            [{"a": 1}, {"a": 2}], "/tmp",
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["a"], 1)

    def test_file_path(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            data = [{"name": "x"}, {"name": "y"}]
            os.write(fd, json.dumps(data).encode())
            os.close(fd)
            items = _load_items(path, "/tmp")
            self.assertEqual(len(items), 2)
        finally:
            os.unlink(path)

    def test_file_not_array_error(self):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            os.write(fd, b'{"not": "array"}')
            os.close(fd)
            with self.assertRaises(ValueError):
                _load_items(path, "/tmp")
        finally:
            os.unlink(path)

    def test_none_items_error(self):
        with self.assertRaises(ValueError):
            _load_items(None, "/tmp")


# ---------------------------------------------------------------------------
# Map execution
# ---------------------------------------------------------------------------

class TestMapExecution(unittest.TestCase):

    def _make_sub_stages(self):
        return {
            "download": StageConfig(
                name="download",
                prompt_template="download {{item.url}}",
                outputs={
                    "path": OutputSpec(
                        name="path", type=OutputType.TEXT,
                    ),
                },
            ),
            "analyze": StageConfig(
                name="analyze",
                depends_on=["download"],
                prompt_template=(
                    "analyze {{map.stages.download.outputs.path}}"
                ),
                outputs={
                    "summary": OutputSpec(
                        name="summary", type=OutputType.TEXT,
                    ),
                },
            ),
        }

    def _make_context(self, sub_stages, items):
        stage_cfg = StageConfig(
            name="process",
            type=StageType.MAP,
            items=items,
            map_max_parallel=2,
            sub_stages=sub_stages,
            depends_on=[],
        )
        config = TaskConfig(
            name="test-pipeline",
            working_dir="/tmp",
            stages={"process": stage_cfg},
        )

        map_state = MapState()
        stage_state = StageState(
            name="process", map_state=map_state,
        )
        pipeline_state = PipelineState(
            pipeline_name="test-pipeline",
            stages={"process": stage_state},
        )

        state_mgr = MagicMock()
        state_mgr.state = pipeline_state
        state_mgr.save = MagicMock()

        artifacts = MagicMock()
        artifacts.get_stage_outputs.return_value = {}
        artifacts.resolve_inputs.return_value = {}

        runner = AsyncMock()
        templates = MagicMock()
        templates.render.side_effect = lambda t, c: t
        extractors = MagicMock()

        ctx = MagicMock()
        ctx.stage_name = "process"
        ctx.config = config
        ctx.runner = runner
        ctx.artifacts = artifacts
        ctx.state_mgr = state_mgr
        ctx.templates = templates
        ctx.extractors = extractors

        return ctx, stage_cfg

    def test_items_processed_with_dependency_ordering(self):
        """Sub-stages within each item respect dependency ordering."""
        sub_stages = self._make_sub_stages()
        items = [
            {"url": "http://a", "title": "A"},
            {"url": "http://b", "title": "B"},
        ]
        ctx, stage_cfg = self._make_context(sub_stages, items)

        call_log: list[tuple[int, str]] = []

        async def mock_run(prompt, **kwargs):
            # Extract item index and sub-stage from session_name
            sn = kwargs.get("session_name", "")
            # Format: test-pipeline/process/item{N}/{sub}
            parts = sn.split("/")
            item_idx = int(parts[2].replace("item", ""))
            sub_name = parts[3]
            call_log.append((item_idx, sub_name))
            return RunResult(
                text="ok", token_usage=TokenUsage(),
            )

        ctx.runner.run = mock_run

        def mock_extract(text, output_specs):
            return {name: f"val_{name}" for name in output_specs}

        ctx.extractors.extract_all.side_effect = mock_extract

        strategy = MapStrategy()
        result = asyncio.run(
            strategy.execute(ctx, stage_cfg),
        )

        # Both items should be processed
        self.assertIn("0", result)
        self.assertIn("1", result)

        # For each item, download must come before analyze
        for item_idx in [0, 1]:
            item_calls = [
                name for idx, name in call_log if idx == item_idx
            ]
            self.assertIn("download", item_calls)
            self.assertIn("analyze", item_calls)
            self.assertLess(
                item_calls.index("download"),
                item_calls.index("analyze"),
            )

    def test_single_item_failure_doesnt_block_others(self):
        """One failing item should not prevent others from completing."""
        sub_stages = self._make_sub_stages()
        items = [
            {"url": "http://a", "title": "A"},
            {"url": "http://fail", "title": "Fail"},
            {"url": "http://c", "title": "C"},
        ]
        ctx, stage_cfg = self._make_context(sub_stages, items)

        async def mock_run(prompt, **kwargs):
            sn = kwargs.get("session_name", "")
            parts = sn.split("/")
            item_idx = int(parts[2].replace("item", ""))
            if item_idx == 1:
                raise RuntimeError("Simulated failure")
            return RunResult(
                text="ok", token_usage=TokenUsage(),
            )

        ctx.runner.run = mock_run

        def mock_extract(text, output_specs):
            return {name: f"val_{name}" for name in output_specs}

        ctx.extractors.extract_all.side_effect = mock_extract

        strategy = MapStrategy()
        result = asyncio.run(
            strategy.execute(ctx, stage_cfg),
        )

        # Items 0 and 2 succeed, item 1 fails
        self.assertIn("0", result)
        self.assertIn("2", result)
        self.assertNotIn("1", result)

    def test_all_items_fail_raises(self):
        """When all items fail, the stage should raise."""
        sub_stages = self._make_sub_stages()
        items = [{"url": "http://a"}]
        ctx, stage_cfg = self._make_context(sub_stages, items)

        async def mock_run(prompt, **kwargs):
            raise RuntimeError("boom")

        ctx.runner.run = mock_run

        strategy = MapStrategy()
        with self.assertRaises(RuntimeError) as cm:
            asyncio.run(strategy.execute(ctx, stage_cfg))
        self.assertIn("All 1 items failed", str(cm.exception))

    def test_template_context_has_item_and_index(self):
        """Verify item and item_index are passed to template context."""
        sub_stages = {
            "step": StageConfig(
                name="step",
                prompt_template="Process {{item.name}} at {{item_index}}",
                outputs={
                    "result": OutputSpec(
                        name="result", type=OutputType.TEXT,
                    ),
                },
            ),
        }
        items = [{"name": "alpha"}]
        ctx, stage_cfg = self._make_context(sub_stages, items)

        captured_contexts: list[dict] = []
        original_render = ctx.templates.render

        def capture_render(template, context):
            captured_contexts.append(context)
            return template

        ctx.templates.render.side_effect = capture_render

        async def mock_run(prompt, **kwargs):
            return RunResult(
                text="ok", token_usage=TokenUsage(),
            )

        ctx.runner.run = mock_run

        def mock_extract(text, output_specs):
            return {name: "v" for name in output_specs}

        ctx.extractors.extract_all.side_effect = mock_extract

        strategy = MapStrategy()
        asyncio.run(strategy.execute(ctx, stage_cfg))

        self.assertEqual(len(captured_contexts), 1)
        c = captured_contexts[0]
        self.assertEqual(c["item"], {"name": "alpha"})
        self.assertEqual(c["item_index"], 0)
        self.assertIn("map", c)

    def test_map_state_updated_correctly(self):
        """Map state reflects item statuses after execution."""
        sub_stages = {
            "step": StageConfig(
                name="step",
                prompt_template="go",
                outputs={
                    "r": OutputSpec(name="r", type=OutputType.TEXT),
                },
            ),
        }
        items = [{"x": 1}, {"x": 2}]
        ctx, stage_cfg = self._make_context(sub_stages, items)

        async def mock_run(prompt, **kwargs):
            return RunResult(
                text="ok", token_usage=TokenUsage(),
            )

        ctx.runner.run = mock_run

        def mock_extract(text, output_specs):
            return {name: "v" for name in output_specs}

        ctx.extractors.extract_all.side_effect = mock_extract

        strategy = MapStrategy()
        asyncio.run(strategy.execute(ctx, stage_cfg))

        ms = ctx.state_mgr.state.stages["process"].map_state
        self.assertEqual(ms.total_items, 2)
        self.assertEqual(ms.items["0"].status, StageStatus.DONE)
        self.assertEqual(ms.items["1"].status, StageStatus.DONE)


if __name__ == "__main__":
    unittest.main()
