"""Tests for runner command construction and NDJSON parsing."""

from __future__ import annotations

import unittest

from cc_pipeline.runners.command_builder import build_command
from cc_pipeline.runners.ndjson_parser import parse_ndjson


class TestBuildCommand(unittest.TestCase):

    def test_readonly_flags(self):
        cmd = build_command(
            bin_path="claude", model="claude-sonnet-4-5",
            system_prompt="Be helpful", effort="medium",
            max_budget_usd=1.0, fallback_model=None,
            session_id=None, persist_session=False,
            session_name="test",
            add_dirs=None,
            allowed_tools=["Read", "Glob"],
            skip_permissions=False,
            extra_flags=None,
        )
        self.assertIn("--print", cmd)
        self.assertIn("--bare", cmd)
        self.assertIn("--no-session-persistence", cmd)
        self.assertIn("--allowedTools", cmd)
        self.assertNotIn("--dangerouslySkipPermissions", cmd)

    def test_full_flags(self):
        cmd = build_command(
            bin_path="claude", model="claude-opus-4-5",
            system_prompt=None, effort="max",
            max_budget_usd=5.0, fallback_model="claude-sonnet-4-5",
            session_id="sess-1", persist_session=True,
            session_name=None,
            add_dirs=None,
            allowed_tools=None,
            skip_permissions=True,
            extra_flags=["--custom"],
        )
        self.assertIn("--dangerouslySkipPermissions", cmd)
        self.assertIn("--resume", cmd)
        self.assertIn("--fallback-model", cmd)
        self.assertNotIn("--no-session-persistence", cmd)
        self.assertIn("--custom", cmd)


class TestParseNdjson(unittest.TestCase):

    def test_result_event(self):
        ndjson = (
            '{"type":"assistant","message":{"content":[{"type":"text","text":"Hello"}]}}\n'
            '{"type":"result","result":"Hello world","session_id":"s123"}\n'
        )
        r = parse_ndjson(ndjson)
        self.assertEqual(r.text, "Hello world")
        self.assertEqual(r.session_id, "s123")

    def test_empty(self):
        r = parse_ndjson("")
        self.assertEqual(r.text, "")
        self.assertIsNone(r.session_id)
