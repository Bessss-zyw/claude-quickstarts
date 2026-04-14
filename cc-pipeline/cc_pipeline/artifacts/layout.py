"""Artifact directory layout — pure path computation, no I/O."""

from __future__ import annotations

from pathlib import Path


class ArtifactLayout:
    """Computes paths under .pipeline/ for artifacts and logs."""

    def __init__(self, pipeline_dir: Path):
        self.pipeline_dir = pipeline_dir
        self.artifacts_dir = pipeline_dir / "artifacts"
        self.logs_dir = pipeline_dir / "logs"

    def stage_dir(self, stage_name: str) -> Path:
        return self.artifacts_dir / stage_name

    def iteration_dir(self, stage_name: str, iteration: int) -> Path:
        return self.artifacts_dir / stage_name / f"iter_{iteration}"

    def sub_stage_dir(
        self, stage_name: str, iteration: int, sub: str,
    ) -> Path:
        return self.iteration_dir(stage_name, iteration) / sub

    def raw_response_path(
        self, stage_name: str, iteration: int | None = None,
        sub_stage: str | None = None,
    ) -> Path:
        base = self._resolve_base(stage_name, iteration, sub_stage)
        return base / "raw_response.txt"

    def meta_path(
        self, stage_name: str, iteration: int | None = None,
        sub_stage: str | None = None,
    ) -> Path:
        base = self._resolve_base(stage_name, iteration, sub_stage)
        return base / "meta.json"

    def output_path(
        self, stage_name: str, iteration: int | None = None,
        sub_stage: str | None = None,
    ) -> Path:
        base = self._resolve_base(stage_name, iteration, sub_stage)
        return base / "output.json"

    def _resolve_base(
        self, stage_name: str, iteration: int | None = None,
        sub_stage: str | None = None,
    ) -> Path:
        if sub_stage is not None and iteration is not None:
            return self.sub_stage_dir(stage_name, iteration, sub_stage)
        if iteration is not None:
            return self.iteration_dir(stage_name, iteration)
        return self.stage_dir(stage_name)

    def log_path(self, stage_name: str) -> Path:
        return self.logs_dir / f"{stage_name}.ndjson"

    def ensure_dirs(self) -> None:
        """Create top-level directories if missing."""
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
