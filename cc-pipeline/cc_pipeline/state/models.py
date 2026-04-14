"""State data models for cc-pipeline.

Pure dataclasses — no persistence or transition logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..constants.enums import (
    IterationStatus,
    PipelineStatus,
    StageStatus,
)


@dataclass
class TokenUsage:
    """Unified token usage across all layers."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TokenUsage:
        return cls(
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
            cache_read_tokens=data.get("cache_read_tokens", 0),
            cache_creation_tokens=data.get("cache_creation_tokens", 0),
        )

    def accumulate(self, other: TokenUsage) -> None:
        """Add another usage onto this one (mutating)."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_creation_tokens += other.cache_creation_tokens


@dataclass
class SubStageState:
    """State of a sub-stage within a loop iteration."""
    name: str
    status: StageStatus = StageStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    outputs: dict[str, Any] | None = None
    session_id: str | None = None
    token_usage: TokenUsage | None = None
    error: str | None = None


@dataclass
class IterationState:
    """State of a single loop iteration."""
    status: IterationStatus = IterationStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    outputs: dict[str, Any] | None = None
    session_id: str | None = None
    token_usage: TokenUsage | None = None
    error: str | None = None
    sub_stages: dict[str, SubStageState] = field(default_factory=dict)


@dataclass
class LoopState:
    """State for loop-type stages."""
    current_iteration: int = 0
    max_iterations: int = 5
    iterations: dict[str, IterationState] = field(default_factory=dict)
    converged: bool = False
    convergence_value: float | None = None


@dataclass
class ItemState:
    """State of a single item in a map stage."""
    index: int
    status: StageStatus = StageStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    outputs: dict[str, Any] | None = None
    sub_stages: dict[str, SubStageState] = field(default_factory=dict)
    error: str | None = None


@dataclass
class MapState:
    """State for map-type stages."""
    total_items: int = 0
    items: dict[str, ItemState] = field(default_factory=dict)  # key = str(index)


@dataclass
class StageState:
    """State of a single pipeline stage."""
    name: str
    status: StageStatus = StageStatus.PENDING
    started_at: str | None = None
    finished_at: str | None = None
    session_id: str | None = None
    token_usage: TokenUsage | None = None
    error: str | None = None
    retry_count: int = 0
    loop: LoopState | None = None
    map_state: MapState | None = None
    outputs: dict[str, Any] | None = None


@dataclass
class PipelineState:
    """Global pipeline state."""
    pipeline_name: str
    task_file: str = ""
    started_at: str = ""
    finished_at: str | None = None
    status: PipelineStatus = PipelineStatus.PENDING
    stages: dict[str, StageState] = field(default_factory=dict)
    total_token_usage: TokenUsage = field(default_factory=TokenUsage)
    total_cost_usd: float = 0.0


@dataclass
class RunResult:
    """Result of a single runner invocation.

    Placed in state/models to avoid circular dependencies between
    runners/ and artifacts/.
    """
    text: str = ""
    session_id: str | None = None
    exit_code: int = 0
    duration_seconds: float = 0.0
    stderr: str = ""
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    raw_events: list[dict] = field(default_factory=list)
