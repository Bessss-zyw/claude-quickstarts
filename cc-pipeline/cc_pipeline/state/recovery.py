"""Crash recovery policy for cc-pipeline.

Scans state for stages/iterations that were interrupted mid-execution
(status=running) and resets them to pending for re-execution.
"""

from __future__ import annotations

import logging

from ..constants.enums import IterationStatus, StageStatus
from .models import PipelineState

logger = logging.getLogger(__name__)


def recover_running_stages(state: PipelineState) -> int:
    """Reset any 'running' stages/iterations/sub-stages to 'pending'.

    Returns:
        Number of stages that were reset.
    """
    reset_count = 0
    for stage in state.stages.values():
        if stage.status == StageStatus.RUNNING:
            logger.warning(
                "Stage %r was running at crash — resetting to pending",
                stage.name,
            )
            stage.status = StageStatus.PENDING
            stage.started_at = None
            reset_count += 1

        if stage.loop:
            for it in stage.loop.iterations.values():
                if it.status == IterationStatus.RUNNING:
                    it.status = IterationStatus.PENDING
                    it.started_at = None

                for ss in it.sub_stages.values():
                    if ss.status == StageStatus.RUNNING:
                        ss.status = StageStatus.PENDING
                        ss.started_at = None

    return reset_count
