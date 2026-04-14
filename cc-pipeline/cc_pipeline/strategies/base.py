"""Stage strategy ABC, context, and registry.

Execution strategies are pluggable: register a new StageType → Strategy
mapping to support new stage types without modifying existing code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..artifacts.store import ArtifactStore
from ..config.models import StageConfig, TaskConfig
from ..constants.enums import StageType
from ..extractors.base import ExtractorRegistry
from ..runners.base import Runner
from ..state.manager import StateManager
from ..templates.engine import TemplateEngine


@dataclass
class StageContext:
    """All dependencies a strategy needs to execute a stage."""
    stage_name: str
    config: TaskConfig
    runner: Runner
    artifacts: ArtifactStore
    state_mgr: StateManager
    templates: TemplateEngine
    extractors: ExtractorRegistry


class StageStrategy(ABC):
    """Executes a pipeline stage of a specific type."""

    @abstractmethod
    async def execute(
        self, ctx: StageContext, stage_cfg: StageConfig,
    ) -> dict[str, Any]:
        """Run the stage and return extracted outputs."""
        ...


class StrategyRegistry:
    """Maps StageType → StageStrategy."""

    def __init__(self) -> None:
        self._registry: dict[StageType, StageStrategy] = {}

    def register(
        self, stage_type: StageType, strategy: StageStrategy,
    ) -> None:
        self._registry[stage_type] = strategy

    def get(self, stage_type: StageType) -> StageStrategy:
        strategy = self._registry.get(stage_type)
        if strategy is None:
            raise KeyError(
                f"No strategy registered for {stage_type.value!r}"
            )
        return strategy
