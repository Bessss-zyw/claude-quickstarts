"""Configuration data models for cc-pipeline.

Pure dataclasses with no parsing or validation logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..constants.enums import (
    ConvergenceOp,
    InputType,
    OutputType,
    PermissionLevel,
    StageType,
)


@dataclass(frozen=True)
class OutputSpec:
    """Specification for a stage output artifact."""
    name: str
    type: OutputType = OutputType.TEXT
    schema: dict | None = None
    path: str | None = None


@dataclass(frozen=True)
class InputSpec:
    """Specification for a stage input."""
    name: str
    type: InputType = InputType.LITERAL
    value: str | None = None
    pattern: str | None = None
    path: str | None = None


@dataclass(frozen=True)
class ConvergenceSpec:
    """Convergence criteria for loop stages."""
    metric: str
    threshold: float = 1.0
    operator: ConvergenceOp = ConvergenceOp.GE


@dataclass
class StageConfig:
    """Configuration for a single pipeline stage."""
    name: str
    type: StageType = StageType.SUBTASK
    description: str = ""
    depends_on: list[str] = field(default_factory=list)

    # CC invocation parameters
    model: str = "claude-sonnet-4-5"
    effort: str = "medium"
    max_budget_usd: float | None = None
    fallback_model: str | None = None
    permissions: PermissionLevel = PermissionLevel.READONLY
    system_prompt: str | None = None
    prompt_template: str = ""
    add_dirs: list[str] = field(default_factory=list)
    persist_session: bool = False

    # Input/output specs
    inputs: dict[str, InputSpec] = field(default_factory=dict)
    outputs: dict[str, OutputSpec] = field(default_factory=dict)

    # Loop-specific
    max_iterations: int = 5
    convergence: ConvergenceSpec | None = None
    sub_stages: dict[str, StageConfig] = field(default_factory=dict)

    # Map-specific
    items: str | list | None = None      # JSON file path or inline list
    map_max_parallel: int = 4            # max concurrent items for map

    # Extra flags
    extra_flags: list[str] = field(default_factory=list)


@dataclass
class TaskConfig:
    """Parsed task.yaml configuration."""
    name: str
    description: str = ""
    working_dir: str = "."
    max_parallel: int = 4
    defaults: dict = field(default_factory=dict)
    stages: dict[str, StageConfig] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
