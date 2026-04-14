"""YAML loader and parser for task configuration.

Converts raw YAML into typed TaskConfig / StageConfig dataclasses.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from ..constants.enums import (
    ConvergenceOp,
    InputType,
    OutputType,
    PermissionLevel,
    StageType,
)
from .models import (
    ConvergenceSpec,
    InputSpec,
    OutputSpec,
    StageConfig,
    TaskConfig,
)
from .validator import validate_dag

logger = logging.getLogger(__name__)


def load_task_config(path: Path | str) -> TaskConfig:
    """Load, parse, and validate a task.yaml file.

    Raises:
        FileNotFoundError: Task file not found.
        ValueError: Invalid YAML or configuration.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Task file not found: {path}")

    raw = _load_yaml(path)
    defaults = raw.get("defaults", {})
    stages = _parse_stages(raw.get("stages", {}), defaults)

    if not stages:
        raise ValueError("Task file must define at least one stage")

    working_dir = _resolve_working_dir(raw.get("working_dir", "."), path)

    config = TaskConfig(
        name=raw.get("name", path.stem),
        description=raw.get("description", ""),
        working_dir=working_dir,
        max_parallel=raw.get("max_parallel", 4),
        defaults=defaults,
        stages=stages,
        raw=raw,
    )

    validate_dag(config)

    # Apply working_dir to add_dirs where not set
    for stage in config.stages.values():
        if not stage.add_dirs:
            stage.add_dirs = defaults.get("add_dirs", [working_dir])

    logger.info(
        "Loaded task %r: %d stages, max_parallel=%d",
        config.name, len(config.stages), config.max_parallel,
    )
    return config


def _load_yaml(path: Path) -> dict:
    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse YAML {path}: {e}") from e

    if not isinstance(raw, dict):
        raise ValueError(
            f"Task file must be a YAML mapping, got {type(raw)}"
        )
    return raw


def _resolve_working_dir(raw_dir: str, yaml_path: Path) -> str:
    if not Path(raw_dir).is_absolute():
        return str(yaml_path.parent / raw_dir)
    return raw_dir


def _parse_stages(
    raw_stages: dict, defaults: dict,
) -> dict[str, StageConfig]:
    stages: dict[str, StageConfig] = {}
    for name, raw in raw_stages.items():
        if not isinstance(raw, dict):
            raise ValueError(f"Stage {name!r} must be a mapping")
        stages[name] = _parse_stage(name, raw, defaults)
    return stages


def _parse_stage(
    name: str, raw: dict, defaults: dict,
) -> StageConfig:
    def get(key: str, fallback: Any = None) -> Any:
        return raw.get(key, defaults.get(key, fallback))

    stage_type = StageType(raw.get("type", "subtask"))

    # Parse sub-stages for loop stages with a "stages" key
    sub_stages: dict[str, StageConfig] = {}
    if stage_type == StageType.LOOP and "stages" in raw:
        parent_defaults = {
            "model": get("model", "claude-sonnet-4-5"),
            "effort": get("effort", "medium"),
            "max_budget_usd": get("max_budget_usd"),
            "fallback_model": get("fallback_model"),
            "permissions": get("permissions", "readonly"),
            "add_dirs": get("add_dirs", []),
            "persist_session": get("persist_session", False),
        }
        merged_defaults = {**defaults, **{k: v for k, v in parent_defaults.items() if v is not None}}
        for sub_name, sub_raw in raw["stages"].items():
            if not isinstance(sub_raw, dict):
                raise ValueError(
                    f"Sub-stage {sub_name!r} in {name!r} must be a mapping"
                )
            sub_stages[sub_name] = _parse_stage(
                sub_name, sub_raw, merged_defaults,
            )

    return StageConfig(
        name=name,
        type=stage_type,
        description=raw.get("description", ""),
        depends_on=raw.get("depends_on", []),
        model=get("model", "claude-sonnet-4-5"),
        effort=get("effort", "medium"),
        max_budget_usd=get("max_budget_usd"),
        fallback_model=get("fallback_model"),
        permissions=PermissionLevel(get("permissions", "readonly")),
        system_prompt=raw.get("system_prompt"),
        prompt_template=raw.get("prompt_template", ""),
        add_dirs=get("add_dirs", []),
        persist_session=get("persist_session", False),
        inputs=_parse_inputs(raw.get("inputs", {})),
        outputs=_parse_outputs(raw.get("outputs", {})),
        max_iterations=raw.get("max_iterations", 5),
        convergence=_parse_convergence(raw.get("convergence")),
        sub_stages=sub_stages,
        extra_flags=raw.get("extra_flags", []),
    )


def _parse_inputs(raw: dict) -> dict[str, InputSpec]:
    inputs: dict[str, InputSpec] = {}
    for iname, iraw in raw.items():
        if isinstance(iraw, str):
            inputs[iname] = InputSpec(
                name=iname, type=InputType.LITERAL, value=iraw,
            )
        elif isinstance(iraw, dict):
            inputs[iname] = InputSpec(
                name=iname,
                type=InputType(iraw.get("type", "literal")),
                value=iraw.get("value"),
                pattern=iraw.get("pattern"),
                path=iraw.get("path"),
            )
    return inputs


def _parse_outputs(raw: dict) -> dict[str, OutputSpec]:
    outputs: dict[str, OutputSpec] = {}
    for oname, oraw in raw.items():
        if isinstance(oraw, str):
            outputs[oname] = OutputSpec(
                name=oname, type=OutputType(oraw),
            )
        elif isinstance(oraw, dict):
            outputs[oname] = OutputSpec(
                name=oname,
                type=OutputType(oraw.get("type", "text")),
                schema=oraw.get("schema"),
                path=oraw.get("path"),
            )
    return outputs


def _parse_convergence(raw: dict | None) -> ConvergenceSpec | None:
    if not raw or not isinstance(raw, dict):
        return None
    return ConvergenceSpec(
        metric=raw["metric"],
        threshold=raw.get("threshold", 1.0),
        operator=ConvergenceOp(raw.get("operator", ">=")),
    )
