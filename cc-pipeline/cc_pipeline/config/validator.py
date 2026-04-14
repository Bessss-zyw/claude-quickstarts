"""DAG validation for pipeline stage dependencies.

Separated from parsing so validation rules can be tested independently.
"""

from __future__ import annotations

import logging

from ..constants.enums import StageType
from .models import TaskConfig

logger = logging.getLogger(__name__)


class DagValidationError(ValueError):
    """Raised when the stage dependency graph is invalid."""


def validate_dag(config: TaskConfig) -> None:
    """Validate that stage dependencies form a valid DAG.

    Checks:
    1. All depends_on entries reference existing stages.
    2. No cycles (Kahn's algorithm).
    3. Sub-stage constraints for loop stages.

    Raises:
        DagValidationError: On missing reference or cycle.
    """
    stage_names = set(config.stages)
    _check_references(config, stage_names)
    _check_acyclic(config, stage_names)

    for stage in config.stages.values():
        if stage.sub_stages:
            _validate_sub_stages(stage.name, stage.sub_stages)

    logger.debug("DAG validation passed: %d stages", len(stage_names))


def _check_references(config: TaskConfig, names: set[str]) -> None:
    for stage in config.stages.values():
        for dep in stage.depends_on:
            if dep not in names:
                raise DagValidationError(
                    f"Stage {stage.name!r} depends on "
                    f"unknown stage {dep!r}"
                )


def _check_acyclic(config: TaskConfig, names: set[str]) -> None:
    """Kahn's algorithm — topological sort to detect cycles."""
    # Build adjacency list + in-degree map
    in_degree: dict[str, int] = {n: 0 for n in names}
    children: dict[str, list[str]] = {n: [] for n in names}

    for stage in config.stages.values():
        in_degree[stage.name] = len(stage.depends_on)
        for dep in stage.depends_on:
            children[dep].append(stage.name)

    queue = [n for n, d in in_degree.items() if d == 0]
    visited = 0

    while queue:
        node = queue.pop(0)
        visited += 1
        for child in children[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if visited != len(names):
        raise DagValidationError(
            f"Stage dependency graph has a cycle! "
            f"Visited {visited}/{len(names)} stages."
        )


def _validate_sub_stages(
    parent_name: str, sub_stages: dict,
) -> None:
    """Validate sub-stage constraints within a loop stage.

    Checks:
    1. Sub-stages must not be empty.
    2. Sub-stage depends_on must reference sibling sub-stages only.
    3. No cycles in the sub-stage DAG.
    4. Sub-stages cannot themselves be loops.
    """
    sub_names = set(sub_stages)

    if not sub_names:
        raise DagValidationError(
            f"Loop stage {parent_name!r} has empty sub_stages"
        )

    for sub in sub_stages.values():
        # No nested loops
        if sub.type == StageType.LOOP:
            raise DagValidationError(
                f"Sub-stage {sub.name!r} in {parent_name!r} "
                f"cannot be a loop (no nesting)"
            )
        # depends_on must reference siblings only
        for dep in sub.depends_on:
            if dep not in sub_names:
                raise DagValidationError(
                    f"Sub-stage {sub.name!r} in {parent_name!r} "
                    f"depends on {dep!r} which is not a sibling sub-stage"
                )

    # Cycle detection via Kahn's algorithm on sub-stages
    in_degree: dict[str, int] = {n: 0 for n in sub_names}
    children: dict[str, list[str]] = {n: [] for n in sub_names}

    for sub in sub_stages.values():
        in_degree[sub.name] = len(sub.depends_on)
        for dep in sub.depends_on:
            children[dep].append(sub.name)

    queue = [n for n, d in in_degree.items() if d == 0]
    visited = 0

    while queue:
        node = queue.pop(0)
        visited += 1
        for child in children[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    if visited != len(sub_names):
        raise DagValidationError(
            f"Sub-stage DAG in {parent_name!r} has a cycle! "
            f"Visited {visited}/{len(sub_names)} sub-stages."
        )
