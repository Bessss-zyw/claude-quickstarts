"""Artifact reader — reads previously saved outputs."""

from __future__ import annotations

import json
from typing import Any

from .layout import ArtifactLayout


class ArtifactReader:
    """Reads stage outputs and raw responses from disk."""

    def __init__(self, layout: ArtifactLayout):
        self._layout = layout

    def get_stage_outputs(self, stage_name: str) -> dict[str, Any]:
        path = self._layout.output_path(stage_name)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    def get_iteration_outputs(
        self, stage_name: str, iteration: int,
    ) -> dict[str, Any]:
        path = self._layout.output_path(stage_name, iteration)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    def get_sub_stage_outputs(
        self, stage_name: str, iteration: int, sub_stage: str,
    ) -> dict[str, Any]:
        path = self._layout.output_path(stage_name, iteration, sub_stage)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    def get_raw_response(self, stage_name: str) -> str:
        path = self._layout.raw_response_path(stage_name)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""
