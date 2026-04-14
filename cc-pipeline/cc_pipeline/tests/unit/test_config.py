"""Tests for config parsing and DAG validation."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from cc_pipeline.config.loader import load_task_config
from cc_pipeline.config.validator import DagValidationError
from cc_pipeline.constants.enums import PermissionLevel, StageType


class TestConfigLoader(unittest.TestCase):

    def _write_yaml(self, content: str) -> Path:
        fd, path = tempfile.mkstemp(suffix=".yaml")
        os.write(fd, content.encode())
        os.close(fd)
        return Path(path)

    def test_minimal(self):
        p = self._write_yaml("name: t\nworking_dir: /tmp\nstages:\n  s1:\n    prompt_template: hi\n")
        try:
            cfg = load_task_config(p)
            self.assertEqual(cfg.name, "t")
            self.assertIn("s1", cfg.stages)
        finally:
            os.unlink(p)

    def test_defaults_applied(self):
        y = "name: t\nworking_dir: /tmp\ndefaults:\n  model: claude-opus-4-5\n  effort: high\nstages:\n  s1:\n    prompt_template: hi\n  s2:\n    model: claude-sonnet-4-5\n    prompt_template: bye\n"
        p = self._write_yaml(y)
        try:
            cfg = load_task_config(p)
            self.assertEqual(cfg.stages["s1"].model, "claude-opus-4-5")
            self.assertEqual(cfg.stages["s2"].model, "claude-sonnet-4-5")
            self.assertEqual(cfg.stages["s1"].effort, "high")
        finally:
            os.unlink(p)

    def test_cycle_detection(self):
        y = "name: t\nworking_dir: /tmp\nstages:\n  a:\n    depends_on: [b]\n    prompt_template: x\n  b:\n    depends_on: [a]\n    prompt_template: y\n"
        p = self._write_yaml(y)
        try:
            with self.assertRaises(DagValidationError):
                load_task_config(p)
        finally:
            os.unlink(p)

    def test_unknown_dep(self):
        y = "name: t\nworking_dir: /tmp\nstages:\n  a:\n    depends_on: [missing]\n    prompt_template: x\n"
        p = self._write_yaml(y)
        try:
            with self.assertRaises(DagValidationError):
                load_task_config(p)
        finally:
            os.unlink(p)

    def test_loop_config(self):
        y = """
name: t
working_dir: /tmp
stages:
  opt:
    type: loop
    max_iterations: 10
    convergence:
      metric: outputs.score
      threshold: 0.95
      operator: ">="
    prompt_template: go
    outputs:
      score: {type: number}
"""
        p = self._write_yaml(y)
        try:
            cfg = load_task_config(p)
            s = cfg.stages["opt"]
            self.assertEqual(s.type, StageType.LOOP)
            self.assertEqual(s.max_iterations, 10)
            self.assertIsNotNone(s.convergence)
            self.assertAlmostEqual(s.convergence.threshold, 0.95)
        finally:
            os.unlink(p)

    def test_permission_enum(self):
        y = "name: t\nworking_dir: /tmp\nstages:\n  s:\n    permissions: full\n    prompt_template: x\n"
        p = self._write_yaml(y)
        try:
            cfg = load_task_config(p)
            self.assertEqual(cfg.stages["s"].permissions, PermissionLevel.FULL)
        finally:
            os.unlink(p)

    def test_invalid_yaml(self):
        p = self._write_yaml("{ invalid yaml [[[")
        try:
            with self.assertRaises(ValueError):
                load_task_config(p)
        finally:
            os.unlink(p)
