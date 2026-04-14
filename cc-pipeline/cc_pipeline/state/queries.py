"""Read-only query helpers for pipeline state.

Pure functions that inspect state without mutating it.
Extracted from StateManager to keep it under 200 lines.
"""

from __future__ import annotations

from ..constants.enums import StageStatus
from ..config.models import TaskConfig
from .models import PipelineState


def is_stage_done(state: PipelineState, name: str) -> bool:
    return state.stages[name].status == StageStatus.DONE


def is_stage_pending(state: PipelineState, name: str) -> bool:
    return state.stages[name].status == StageStatus.PENDING


def all_deps_done(
    state: PipelineState, name: str, config: TaskConfig,
) -> bool:
    deps = config.stages[name].depends_on
    return all(
        state.stages[d].status == StageStatus.DONE for d in deps
    )


def any_dep_failed(
    state: PipelineState, name: str, config: TaskConfig,
) -> bool:
    deps = config.stages[name].depends_on
    return any(
        state.stages[d].status == StageStatus.FAILED for d in deps
    )


def get_pending_stages(state: PipelineState) -> list[str]:
    return [
        n for n, s in state.stages.items()
        if s.status == StageStatus.PENDING
    ]


def get_done_stages(state: PipelineState) -> list[str]:
    return [
        n for n, s in state.stages.items()
        if s.status == StageStatus.DONE
    ]
