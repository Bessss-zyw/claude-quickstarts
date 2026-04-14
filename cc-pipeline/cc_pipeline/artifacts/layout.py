"""Artifact directory layout — pure path computation, no I/O."""

from __future__ import annotations

from pathlib import Path


class ArtifactLayout:
    """Computes paths under .pipeline/ for stage artifacts and logs."""

    def __init__(self, pipeline_dir: Path):
        self.pipeline_dir = pipeline_dir
        self.stages_dir = pipeline_dir / "stages"

    # ---- directory helpers ------------------------------------------------

    def stage_dir(self, stage_name: str) -> Path:
        return self.stages_dir / stage_name

    def iteration_dir(self, stage_name: str, iteration: int) -> Path:
        return self.stages_dir / stage_name / f"iter_{iteration}"

    def sub_stage_dir(
        self, stage_name: str, iteration: int, sub: str,
    ) -> Path:
        return self.iteration_dir(stage_name, iteration) / sub

    # ---- file paths -------------------------------------------------------

    def response_path(
        self, stage_name: str, iteration: int | None = None,
        sub_stage: str | None = None,
    ) -> Path:
        base = self._resolve_base(stage_name, iteration, sub_stage)
        return base / "response.txt"

    def prompt_path(
        self, stage_name: str, iteration: int | None = None,
        sub_stage: str | None = None,
    ) -> Path:
        base = self._resolve_base(stage_name, iteration, sub_stage)
        return base / "prompt.txt"

    def meta_path(
        self, stage_name: str, iteration: int | None = None,
        sub_stage: str | None = None,
    ) -> Path:
        base = self._resolve_base(stage_name, iteration, sub_stage)
        return base / "meta.json"

    def outputs_path(
        self, stage_name: str, iteration: int | None = None,
        sub_stage: str | None = None,
    ) -> Path:
        base = self._resolve_base(stage_name, iteration, sub_stage)
        return base / "outputs.json"

    def log_path(
        self, stage_name: str, iteration: int | None = None,
        sub_stage: str | None = None,
    ) -> Path:
        base = self._resolve_base(stage_name, iteration, sub_stage)
        return base / "events.ndjson"

    def _resolve_base(
        self, stage_name: str, iteration: int | None = None,
        sub_stage: str | None = None,
    ) -> Path:
        if sub_stage is not None and iteration is not None:
            return self.sub_stage_dir(stage_name, iteration, sub_stage)
        if iteration is not None:
            return self.iteration_dir(stage_name, iteration)
        return self.stage_dir(stage_name)

    # ---- summary paths ----------------------------------------------------

    @property
    def summary_json_path(self) -> Path:
        return self.pipeline_dir / "summary.json"

    @property
    def summary_md_path(self) -> Path:
        return self.pipeline_dir / "summary.md"

    # ---- directory creation -----------------------------------------------

    def ensure_dirs(self) -> None:
        """Create top-level directories if missing."""
        self.stages_dir.mkdir(parents=True, exist_ok=True)
