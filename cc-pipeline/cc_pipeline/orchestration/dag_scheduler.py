"""DAG scheduler — determines ready/stuck/dispatch state.

Pure scheduling logic with no I/O or subprocess management.
"""

from __future__ import annotations

import logging

from ..config.models import TaskConfig
from ..constants.enums import StageStatus
from ..state.manager import StateManager

logger = logging.getLogger(__name__)


class PipelineStuckError(RuntimeError):
    """Raised when the scheduler detects an unresolvable state."""


class DagScheduler:
    """Determines which stages are ready for execution."""

    def __init__(self, config: TaskConfig, state_mgr: StateManager):
        self._config = config
        self._state = state_mgr

    def get_ready(self, dispatched: set[str]) -> list[str]:
        """Return pending stages whose deps are all done."""
        return [
            name for name in self._config.stages
            if (
                self._state.is_stage_pending(name)
                and name not in dispatched
                and self._state.all_deps_done(name, self._config)
            )
        ]

    def skip_blocked(self) -> list[str]:
        """Skip pending stages whose deps have failed.

        Returns list of stage names that were skipped.
        """
        skipped: list[str] = []
        for name in self._state.get_pending_stages():
            if self._state.any_dep_failed(name, self._config):
                logger.warning(
                    "Skipping %r — dependency failed", name,
                )
                self._state.mark_stage_skipped(
                    name, reason="dependency failed",
                )
                skipped.append(name)
        return skipped

    def check_stuck(self, dispatched: set[str]) -> None:
        """Raise if pipeline is stuck (pending but no progress possible)."""
        pending = self._state.get_pending_stages()
        if not pending:
            return
        if dispatched:
            return  # in-flight tasks may unblock
        raise PipelineStuckError(
            f"Pipeline stuck: {len(pending)} pending "
            f"({', '.join(pending)}), no ready or in-flight stages."
        )

    @property
    def has_pending(self) -> bool:
        return bool(self._state.get_pending_stages())
