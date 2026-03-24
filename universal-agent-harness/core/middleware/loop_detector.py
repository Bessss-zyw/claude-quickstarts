"""Detect when an agent is stuck in a repetitive tool-call loop."""

from __future__ import annotations

import hashlib
import json


class LoopDetector:
    """Track recent tool calls and flag repetition."""

    def __init__(self, window: int = 10, repeat_threshold: int = 3, max_warnings: int = 2) -> None:
        self._window = window
        self._threshold = repeat_threshold
        self._max_warnings = max_warnings
        self._history: list[str] = []   # hashes
        self._warnings_given = 0

    def record(self, tool_name: str, arguments: dict) -> None:
        """Record a tool call."""
        key = tool_name + ":" + hashlib.md5(
            json.dumps(arguments, sort_keys=True).encode()
        ).hexdigest()
        self._history.append(key)
        if len(self._history) > self._window:
            self._history = self._history[-self._window:]

    def check(self) -> str | None:
        """Return a warning message if a loop is detected, or None.

        Returns ``"FORCE_STOP"`` if warnings have been exhausted.
        """
        if len(self._history) < self._threshold:
            return None

        tail = self._history[-self._threshold:]
        if len(set(tail)) == 1:
            self._warnings_given += 1
            if self._warnings_given > self._max_warnings:
                return "FORCE_STOP"
            return (
                "WARNING: You appear to be repeating the same action. "
                "Try a different approach."
            )
        return None

    def reset(self) -> None:
        self._history.clear()
        self._warnings_given = 0
