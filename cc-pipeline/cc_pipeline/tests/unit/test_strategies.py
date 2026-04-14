"""Tests for strategy registry and convergence."""

from __future__ import annotations

import unittest

from cc_pipeline.config.models import ConvergenceSpec
from cc_pipeline.constants.enums import ConvergenceOp, StageType
from cc_pipeline.strategies.base import StrategyRegistry, StageStrategy
from cc_pipeline.strategies.convergence import (
    OperatorRegistry,
    build_default_operators,
)


class TestConvergenceOperators(unittest.TestCase):

    def setUp(self):
        self.ops = build_default_operators()

    def test_ge_pass(self):
        spec = ConvergenceSpec(metric="x", threshold=0.9, operator=ConvergenceOp.GE)
        self.assertTrue(self.ops.check(spec, 0.95))

    def test_ge_fail(self):
        spec = ConvergenceSpec(metric="x", threshold=0.9, operator=ConvergenceOp.GE)
        self.assertFalse(self.ops.check(spec, 0.8))

    def test_lt(self):
        spec = ConvergenceSpec(metric="x", threshold=0.1, operator=ConvergenceOp.LT)
        self.assertTrue(self.ops.check(spec, 0.05))

    def test_eq(self):
        spec = ConvergenceSpec(metric="x", threshold=1.0, operator=ConvergenceOp.EQ)
        self.assertTrue(self.ops.check(spec, 1.0))

    def test_unknown_operator(self):
        ops = OperatorRegistry()  # empty
        spec = ConvergenceSpec(metric="x", threshold=1.0, operator=ConvergenceOp.GE)
        with self.assertRaises(KeyError):
            ops.check(spec, 0.5)


class TestStrategyRegistry(unittest.TestCase):

    def test_missing_strategy(self):
        reg = StrategyRegistry()
        with self.assertRaises(KeyError):
            reg.get(StageType.SUBTASK)
