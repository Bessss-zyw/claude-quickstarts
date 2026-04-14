"""Runner abstract base class.

All CC invocation backends must implement the Runner ABC.
RunResult lives in state/models to avoid circular deps (artifacts → runners).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..state.models import RunResult  # re-export for convenience

__all__ = ["Runner", "RunResult"]


class Runner(ABC):
    """Abstract base for CC invocation backends."""

    @abstractmethod
    async def run(
        self,
        prompt: str,
        *,
        model: str = "claude-sonnet-4-5",
        cwd: Path | None = None,
        timeout: float | None = None,
        permission: str = "readonly",
        system_prompt: str | None = None,
        effort: str = "medium",
        max_budget_usd: float | None = None,
        fallback_model: str | None = None,
        session_id: str | None = None,
        persist_session: bool = False,
        session_name: str | None = None,
        add_dirs: list[Path] | None = None,
        allowed_tools: list[str] | None = None,
        skip_permissions: bool | None = None,
        extra_flags: list[str] | None = None,
    ) -> RunResult:
        """Run a single invocation. Must be implemented by subclass."""
        ...
