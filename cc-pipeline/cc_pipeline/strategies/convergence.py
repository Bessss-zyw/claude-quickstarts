"""Convergence operators for loop stages.

ABC + registry pattern — trivially extensible with new operators.
"""

from __future__ import annotations

import operator as op_mod
from abc import ABC, abstractmethod
from typing import Any, Callable

from ..config.models import ConvergenceSpec
from ..constants.enums import ConvergenceOp


class ConvergenceOperator(ABC):
    """Evaluates whether a metric meets a convergence threshold."""

    @abstractmethod
    def check(self, value: float, threshold: float) -> bool: ...


class ThresholdOperator(ConvergenceOperator):
    """Generic threshold operator backed by a comparison function."""

    def __init__(self, op_func: Callable[[float, float], bool]):
        self._op = op_func

    def check(self, value: float, threshold: float) -> bool:
        return self._op(value, threshold)


class OperatorRegistry:
    """Maps ConvergenceOp → ConvergenceOperator."""

    def __init__(self) -> None:
        self._registry: dict[ConvergenceOp, ConvergenceOperator] = {}

    def register(
        self, op: ConvergenceOp, operator: ConvergenceOperator,
    ) -> None:
        self._registry[op] = operator

    def check(self, spec: ConvergenceSpec, value: float) -> bool:
        op = self._registry.get(spec.operator)
        if op is None:
            raise KeyError(
                f"No operator registered for {spec.operator.value!r}"
            )
        return op.check(value, spec.threshold)


def build_default_operators() -> OperatorRegistry:
    """Create registry with all built-in comparison operators."""
    reg = OperatorRegistry()
    reg.register(ConvergenceOp.GE, ThresholdOperator(op_mod.ge))
    reg.register(ConvergenceOp.LE, ThresholdOperator(op_mod.le))
    reg.register(ConvergenceOp.GT, ThresholdOperator(op_mod.gt))
    reg.register(ConvergenceOp.LT, ThresholdOperator(op_mod.lt))
    reg.register(ConvergenceOp.EQ, ThresholdOperator(op_mod.eq))
    return reg
