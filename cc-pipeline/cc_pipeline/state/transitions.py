"""State transition functions for cc-pipeline.

Pure functions that mutate state objects. Separated from persistence
so transitions can be tested without I/O.
"""

from __future__ import annotations

from typing import Any

from ..constants.enums import (
    IterationStatus,
    PipelineStatus,
    StageStatus,
)
from ..utils.time_utils import now_iso
from .models import (
    IterationState,
    PipelineState,
    StageState,
    TokenUsage,
)


# ---- stage transitions ---------------------------------------------------

def stage_to_running(stage: StageState) -> None:
    stage.status = StageStatus.RUNNING
    stage.started_at = now_iso()


def stage_to_done(
    stage: StageState,
    *,
    session_id: str | None = None,
    token_usage: TokenUsage | None = None,
    outputs: dict[str, Any] | None = None,
) -> None:
    stage.status = StageStatus.DONE
    stage.finished_at = now_iso()
    stage.session_id = session_id
    stage.token_usage = token_usage
    stage.outputs = outputs


def stage_to_failed(
    stage: StageState,
    *,
    error: str,
    token_usage: TokenUsage | None = None,
) -> None:
    stage.status = StageStatus.FAILED
    stage.finished_at = now_iso()
    stage.error = error
    stage.retry_count += 1
    if token_usage:
        stage.token_usage = token_usage


def stage_to_skipped(stage: StageState, reason: str = "") -> None:
    stage.status = StageStatus.SKIPPED
    stage.finished_at = now_iso()
    stage.error = reason


# ---- iteration transitions -----------------------------------------------

def iteration_to_running(stage: StageState, iteration: int) -> None:
    loop = _require_loop(stage)
    loop.current_iteration = iteration
    key = str(iteration)
    if key not in loop.iterations:
        loop.iterations[key] = IterationState()
    loop.iterations[key].status = IterationStatus.RUNNING
    loop.iterations[key].started_at = now_iso()


def iteration_to_done(
    stage: StageState,
    iteration: int,
    *,
    outputs: dict[str, Any] | None = None,
    session_id: str | None = None,
    token_usage: TokenUsage | None = None,
) -> None:
    loop = _require_loop(stage)
    it = loop.iterations[str(iteration)]
    it.status = IterationStatus.DONE
    it.finished_at = now_iso()
    it.outputs = outputs
    it.session_id = session_id
    it.token_usage = token_usage


def iteration_to_failed(
    stage: StageState, iteration: int, *, error: str,
) -> None:
    loop = _require_loop(stage)
    it = loop.iterations[str(iteration)]
    it.status = IterationStatus.FAILED
    it.finished_at = now_iso()
    it.error = error


def mark_loop_converged(
    stage: StageState, convergence_value: float,
) -> None:
    loop = _require_loop(stage)
    loop.converged = True
    loop.convergence_value = convergence_value


# ---- pipeline transitions ------------------------------------------------

def pipeline_to_done(state: PipelineState) -> None:
    state.status = PipelineStatus.DONE
    state.finished_at = now_iso()


def pipeline_to_failed(state: PipelineState) -> None:
    state.status = PipelineStatus.FAILED
    state.finished_at = now_iso()


# ---- helpers --------------------------------------------------------------

def _require_loop(stage: StageState):  # noqa: ANN201 (returns LoopState)
    if stage.loop is None:
        raise ValueError(f"Stage {stage.name!r} is not a loop stage")
    return stage.loop
