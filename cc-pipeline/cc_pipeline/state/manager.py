"""State persistence manager for cc-pipeline.

Handles atomic JSON serialization (tmp + os.replace) and delegates
state transitions to the transitions module.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from ..config.models import TaskConfig
from .models import PipelineState, TokenUsage
from .recovery import recover_running_stages
from .serialization import fresh_state, serialize, deserialize
from . import queries as qry
from . import transitions as tx

logger = logging.getLogger(__name__)


class StateManager:
    """Atomic state persistence + transition methods."""

    def __init__(self, pipeline_dir: Path):
        self.pipeline_dir = pipeline_dir
        self.state_file = pipeline_dir / "state.json"
        self._state: PipelineState | None = None

    @property
    def state(self) -> PipelineState:
        if self._state is None:
            raise RuntimeError("State not loaded; call load_or_create()")
        return self._state

    # ---- lifecycle --------------------------------------------------------

    def load_or_create(
        self, config: TaskConfig, *,
        resume: bool = True, task_file: str = "",
    ) -> PipelineState:
        if self.state_file.exists() and resume:
            self._state = deserialize(self.state_file)
            recover_running_stages(self._state)
            self.save()
            return self._state

        self._state = fresh_state(config, task_file=task_file)
        self.save()
        return self._state

    def load_readonly(self) -> PipelineState | None:
        """Load state without creating or modifying it."""
        if self.state_file.exists():
            return deserialize(self.state_file)
        return None

    def save(self) -> None:
        self.pipeline_dir.mkdir(parents=True, exist_ok=True)
        data = serialize(self.state)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.pipeline_dir), suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, str(self.state_file))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---- stage transitions ------------------------------------------------

    def mark_stage_running(self, name: str) -> None:
        tx.stage_to_running(self.state.stages[name])
        self.save()

    def mark_stage_done(
        self, name: str, *,
        session_id: str | None = None,
        token_usage: TokenUsage | None = None,
        outputs: dict[str, Any] | None = None,
    ) -> None:
        tx.stage_to_done(
            self.state.stages[name],
            session_id=session_id,
            token_usage=token_usage,
            outputs=outputs,
        )
        if token_usage:
            self.state.total_token_usage.accumulate(token_usage)
        self.save()

    def mark_stage_failed(
        self, name: str, *, error: str,
        token_usage: TokenUsage | None = None,
    ) -> None:
        tx.stage_to_failed(
            self.state.stages[name], error=error,
            token_usage=token_usage,
        )
        if token_usage:
            self.state.total_token_usage.accumulate(token_usage)
        self.save()

    def mark_stage_skipped(self, name: str, reason: str = "") -> None:
        tx.stage_to_skipped(self.state.stages[name], reason)
        self.save()

    # ---- iteration transitions --------------------------------------------

    def mark_iteration_running(self, name: str, iteration: int) -> None:
        tx.iteration_to_running(self.state.stages[name], iteration)
        self.save()

    def mark_iteration_done(
        self, name: str, iteration: int, *,
        outputs: dict[str, Any] | None = None,
        session_id: str | None = None,
        token_usage: TokenUsage | None = None,
    ) -> None:
        tx.iteration_to_done(
            self.state.stages[name], iteration,
            outputs=outputs, session_id=session_id,
            token_usage=token_usage,
        )
        if token_usage:
            self.state.total_token_usage.accumulate(token_usage)
        self.save()

    def mark_iteration_failed(
        self, name: str, iteration: int, *, error: str,
    ) -> None:
        tx.iteration_to_failed(
            self.state.stages[name], iteration, error=error,
        )
        self.save()

    def mark_loop_converged(
        self, name: str, value: float,
    ) -> None:
        tx.mark_loop_converged(self.state.stages[name], value)
        self.save()

    def mark_pipeline_done(self) -> None:
        tx.pipeline_to_done(self.state)
        self.save()

    def mark_pipeline_failed(self) -> None:
        tx.pipeline_to_failed(self.state)
        self.save()

    # ---- query helpers (delegated to state.queries) -------------------------

    def is_stage_done(self, name: str) -> bool:
        return qry.is_stage_done(self.state, name)

    def is_stage_pending(self, name: str) -> bool:
        return qry.is_stage_pending(self.state, name)

    def all_deps_done(self, name: str, config: TaskConfig) -> bool:
        return qry.all_deps_done(self.state, name, config)

    def any_dep_failed(self, name: str, config: TaskConfig) -> bool:
        return qry.any_dep_failed(self.state, name, config)

    def get_pending_stages(self) -> list[str]:
        return qry.get_pending_stages(self.state)

    def get_done_stages(self) -> list[str]:
        return qry.get_done_stages(self.state)

