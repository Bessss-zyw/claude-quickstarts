"""Artifact saver — writes CC results to disk."""

from __future__ import annotations

import json
import logging
from typing import Any

from ..state.models import RunResult
from .layout import ArtifactLayout

logger = logging.getLogger(__name__)


class ArtifactSaver:
    """Writes raw response, meta, and structured outputs."""

    def __init__(self, layout: ArtifactLayout):
        self._layout = layout

    def save_result(
        self,
        stage_name: str,
        result: RunResult,
        outputs: dict[str, Any] | None = None,
        iteration: int | None = None,
        sub_stage: str | None = None,
        prompt: str | None = None,
    ) -> None:
        """Save CC result as stage artifacts."""
        resp_path = self._layout.response_path(
            stage_name, iteration, sub_stage,
        )
        resp_path.parent.mkdir(parents=True, exist_ok=True)
        resp_path.write_text(result.text, encoding="utf-8")

        meta = {
            "session_id": result.session_id,
            "token_usage": result.token_usage.to_dict(),
            "duration_seconds": result.duration_seconds,
            "exit_code": result.exit_code,
        }
        meta_path = self._layout.meta_path(
            stage_name, iteration, sub_stage,
        )
        meta_path.write_text(
            json.dumps(meta, indent=2), encoding="utf-8",
        )

        if outputs:
            out_path = self._layout.outputs_path(
                stage_name, iteration, sub_stage,
            )
            out_path.write_text(
                json.dumps(outputs, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        if prompt is not None:
            prompt_path = self._layout.prompt_path(
                stage_name, iteration, sub_stage,
            )
            prompt_path.write_text(prompt, encoding="utf-8")

        logger.debug("Saved artifacts for %r", stage_name)

    def save_log(
        self,
        stage_name: str,
        result: RunResult,
        iteration: int | None = None,
        sub_stage: str | None = None,
    ) -> None:
        """Save raw NDJSON stream events."""
        log_path = self._layout.log_path(stage_name, iteration, sub_stage)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(e) for e in result.raw_events]
        log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
