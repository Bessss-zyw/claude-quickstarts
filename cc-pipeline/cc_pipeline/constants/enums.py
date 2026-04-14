"""Enums as single source of truth for all cc-pipeline constants.

Every string literal that serves as a type discriminator, status value,
or configuration key MUST be defined here and referenced by enum member,
never by raw string.
"""

from __future__ import annotations

from enum import Enum, unique


@unique
class StageStatus(str, Enum):
    """Lifecycle status of a pipeline stage."""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@unique
class IterationStatus(str, Enum):
    """Lifecycle status of a single loop iteration."""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@unique
class PipelineStatus(str, Enum):
    """Lifecycle status of the overall pipeline."""
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@unique
class StageType(str, Enum):
    """Type of pipeline stage — determines execution strategy."""
    PRE_EXEC = "pre-exec"
    SUBTASK = "subtask"
    LOOP = "loop"
    MAP = "map"
    POST_EXEC = "post-exec"


@unique
class OutputType(str, Enum):
    """Type of a stage output artifact."""
    TEXT = "text"
    JSON = "json"
    NUMBER = "number"
    FILE = "file"


@unique
class InputType(str, Enum):
    """Type of a stage input source."""
    LITERAL = "literal"
    GLOB = "glob"
    FILE = "file"
    STAGE_OUTPUT = "stage_output"


@unique
class PermissionLevel(str, Enum):
    """CC invocation permission level."""
    READONLY = "readonly"
    WRITE = "write"
    FULL = "full"


@unique
class ConvergenceOp(str, Enum):
    """Convergence comparison operator for loop stages."""
    GE = ">="
    LE = "<="
    GT = ">"
    LT = "<"
    EQ = "=="


# Status icon mapping for summary display
STAGE_STATUS_ICONS: dict[StageStatus, str] = {
    StageStatus.DONE: "\u2713",      # ✓
    StageStatus.FAILED: "\u2717",    # ✗
    StageStatus.SKIPPED: "\u2298",   # ⊘
    StageStatus.PENDING: "\u00b7",   # ·
    StageStatus.RUNNING: "\u25b8",   # ▸
}
