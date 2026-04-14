"""ArtifactStore — thin facade over layout, saver, reader, resolver."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config.models import InputSpec
from ..state.models import RunResult
from .input_resolver import InputResolver
from .layout import ArtifactLayout
from .reader import ArtifactReader
from .saver import ArtifactSaver


class ArtifactStore:
    """Unified interface for artifact operations.

    Composes four focused components; delegates all work.
    """

    def __init__(self, pipeline_dir: Path):
        self.layout = ArtifactLayout(pipeline_dir)
        self.saver = ArtifactSaver(self.layout)
        self.reader = ArtifactReader(self.layout)
        self.resolver = InputResolver()
        self.layout.ensure_dirs()

    # ---- delegate to saver ------------------------------------------------
    def save_result(
        self, stage_name: str, result: RunResult,
        outputs: dict[str, Any] | None = None,
        iteration: int | None = None,
        sub_stage: str | None = None,
        prompt: str | None = None,
    ) -> None:
        self.saver.save_result(
            stage_name, result, outputs, iteration, sub_stage, prompt,
        )

    def save_log(
        self, stage_name: str, result: RunResult,
        iteration: int | None = None,
        sub_stage: str | None = None,
    ) -> None:
        self.saver.save_log(stage_name, result, iteration, sub_stage)

    # ---- delegate to reader -----------------------------------------------
    def get_stage_outputs(self, stage_name: str) -> dict[str, Any]:
        return self.reader.get_stage_outputs(stage_name)

    def get_iteration_outputs(
        self, stage_name: str, iteration: int,
    ) -> dict[str, Any]:
        return self.reader.get_iteration_outputs(stage_name, iteration)

    def get_sub_stage_outputs(
        self, stage_name: str, iteration: int, sub_stage: str,
    ) -> dict[str, Any]:
        return self.reader.get_sub_stage_outputs(
            stage_name, iteration, sub_stage,
        )

    # ---- delegate to resolver ---------------------------------------------
    def resolve_inputs(
        self, inputs: dict[str, InputSpec], working_dir: str,
    ) -> dict[str, Any]:
        return self.resolver.resolve_all(inputs, working_dir)
