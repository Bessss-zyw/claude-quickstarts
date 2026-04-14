"""Tests for the template engine."""

from __future__ import annotations

import json
import os
import unittest

from cc_pipeline.templates.engine import TemplateEngine, build_template_context


class TestTemplateEngine(unittest.TestCase):

    def setUp(self):
        self.e = TemplateEngine()

    def test_simple_var(self):
        ctx = {"inputs": {"name": "world"}}
        self.assertEqual(self.e.render("Hello {{inputs.name}}!", ctx), "Hello world!")

    def test_nested_var(self):
        ctx = {"stages": {"a": {"outputs": {"plan": {"tasks": [1, 2]}}}}}
        r = self.e.render("{{stages.a.outputs.plan}}", ctx)
        self.assertIn('"tasks"', r)

    def test_if_true(self):
        ctx = {"loop": {"previous": {"outputs": {"r": 0.5}}}}
        r = self.e.render("{{#if loop.previous}}yes{{/if}}", ctx)
        self.assertIn("yes", r)

    def test_if_false(self):
        ctx = {"loop": {}}
        r = self.e.render("{{#if loop.previous}}yes{{/if}}", ctx)
        self.assertNotIn("yes", r)

    def test_unresolved(self):
        r = self.e.render("{{missing.var}}", {})
        self.assertIn("UNRESOLVED", r)

    def test_number(self):
        ctx = {"inputs": {"n": 42}}
        self.assertEqual(self.e.render("{{inputs.n}}", ctx), "42")

    def test_dict_json(self):
        ctx = {"inputs": {"d": {"a": 1}}}
        r = self.e.render("{{inputs.d}}", ctx)
        self.assertEqual(json.loads(r), {"a": 1})

    def test_env(self):
        os.environ["_CC_TEST"] = "val"
        try:
            ctx = build_template_context(
                stage_name="t", inputs={}, stage_outputs={},
            )
            r = self.e.render("{{env._CC_TEST}}", ctx)
            self.assertEqual(r, "val")
        finally:
            del os.environ["_CC_TEST"]
