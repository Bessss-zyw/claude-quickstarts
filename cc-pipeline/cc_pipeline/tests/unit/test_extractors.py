"""Tests for output extractors."""

from __future__ import annotations

import unittest

from cc_pipeline.config.models import OutputSpec
from cc_pipeline.constants.enums import OutputType
from cc_pipeline.extractors.base import build_default_registry
from cc_pipeline.utils.json_extract import extract_json


class TestExtractJson(unittest.TestCase):

    def test_code_fence(self):
        text = 'hi\n```json\n{"k": 1}\n```\nbye'
        self.assertEqual(extract_json(text), {"k": 1})

    def test_raw(self):
        self.assertEqual(extract_json('{"k": 1}'), {"k": 1})

    def test_failure(self):
        with self.assertRaises(ValueError):
            extract_json("no json here")


class TestExtractorRegistry(unittest.TestCase):

    def setUp(self):
        self.reg = build_default_registry()

    def test_text(self):
        spec = OutputSpec(name="t", type=OutputType.TEXT)
        r = self.reg.extract("hello", "t", spec)
        self.assertEqual(r, "hello")

    def test_json(self):
        spec = OutputSpec(name="j", type=OutputType.JSON)
        r = self.reg.extract('```json\n{"x": 1}\n```', "j", spec)
        self.assertEqual(r, {"x": 1})

    def test_number(self):
        spec = OutputSpec(name="score", type=OutputType.NUMBER)
        r = self.reg.extract('{"score": 0.95}', "score", spec)
        self.assertAlmostEqual(r, 0.95)

    def test_extract_all_bulk(self):
        specs = {
            "a": OutputSpec(name="a", type=OutputType.TEXT),
            "b": OutputSpec(name="b", type=OutputType.NUMBER),
        }
        text = '```json\n{"a": "hello", "b": 42}\n```'
        r = self.reg.extract_all(text, specs)
        self.assertEqual(r["a"], "hello")
        self.assertEqual(r["b"], 42)
