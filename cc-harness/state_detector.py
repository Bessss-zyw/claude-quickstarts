"""Detect Claude Code pane state from tmux capture output."""

from __future__ import annotations

import re
from enum import Enum


class PaneState(Enum):
    ACTIVE = "active"
    IDLE = "idle"
    PERMISSION = "permission"
    ERROR = "error"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


# ── Patterns ───────────────────────────────────────────────────────────────

_ACTIVE_PATTERNS = re.compile(
    r"Shimmying|Doodling|Thinking|Plotting|Pondering|Cogitating|"
    r"Bunning|Scheming|Mulling|Pouncing|Noodling|Schlepping|"
    r"[✢✽]|\* [A-Z][a-z]"
)

_PERMISSION_PATTERNS = re.compile(
    r"Do you want to proceed|Allow this action|"
    r"\[y/N\]|\[Y/n\]|Allow once|Allow always|"
    r"Yes, I trust this folder|Enter to confirm"
)

_IDLE_PATTERN = re.compile(r"^\s*❯\s*$", re.MULTILINE)

_EXPIRED_PATTERNS = re.compile(
    r"Timed out waiting for job step|End crun session|"
    r"Connection closed by remote host|Connection reset by peer|"
    r"srun: error:"
)

_CTX_PCT_PATTERN = re.compile(r"(\d+)%")

_COST_PATTERN = re.compile(r"\$[\d.]+")


def detect_state(pane_output: str) -> PaneState:
    """Analyze tmux pane output to determine CC state."""
    if not pane_output.strip():
        return PaneState.UNKNOWN

    # Check last ~30 lines for most detections
    tail = "\n".join(pane_output.splitlines()[-30:])

    if _EXPIRED_PATTERNS.search(tail):
        return PaneState.EXPIRED

    if _PERMISSION_PATTERNS.search(tail):
        return PaneState.PERMISSION

    if _ACTIVE_PATTERNS.search(tail):
        return PaneState.ACTIVE

    if _IDLE_PATTERN.search(tail):
        return PaneState.IDLE

    return PaneState.UNKNOWN


def get_context_pct(pane_output: str) -> int | None:
    """Extract context usage % from the status line area."""
    # Status line is typically in the last few lines
    tail = "\n".join(pane_output.splitlines()[-5:])
    matches = _CTX_PCT_PATTERN.findall(tail)
    if matches:
        return int(matches[-1])
    return None


def get_cost(pane_output: str) -> str | None:
    """Extract cost from the status line."""
    tail = "\n".join(pane_output.splitlines()[-5:])
    m = _COST_PATTERN.search(tail)
    return m.group(0) if m else None


def extract_last_response(pane_output: str) -> str:
    """Extract the last CC response (text between the last two ❯ prompts).

    Returns at most ~3000 chars of the last response.
    """
    lines = pane_output.splitlines()
    prompt_indices = [i for i, l in enumerate(lines) if re.match(r"^\s*❯\s*$", l)]

    if len(prompt_indices) < 2:
        # Can't find two prompts; return last 50 lines
        return "\n".join(lines[-50:])

    # Text between second-to-last prompt and last prompt
    start = prompt_indices[-2] + 1
    end = prompt_indices[-1]
    response = "\n".join(lines[start:end]).strip()

    # Truncate if too long
    if len(response) > 3000:
        response = response[:1500] + "\n...[truncated]...\n" + response[-1500:]
    return response
