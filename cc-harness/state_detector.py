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
    # CC permission dialog always ends with "Esc to cancel" — most reliable
    r"Esc to cancel"
    # Broad "Do you want to ..." catches proceed/create/edit/write/delete/run
    r"|Do you want to \w+"
    # Explicit permission header
    r"|Permission rule .+ requires confirmation"
    # Legacy / alternative prompts
    r"|Allow this action"
    r"|\[y/N\]|\[Y/n\]|Allow once|Allow always"
    r"|Yes, I trust this folder|Enter to confirm"
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


# ── Permission classification ─────────────────────────────────────────────

_DANGEROUS_KEYWORDS = re.compile(
    r"\brm\s+-r|\brm\s+/|\brmdir\b|\bdelete\b|\bremove\b|\bdrop\b"
    r"|\bformat\b|\breset\s+--hard\b|\bforce\s+push\b|\b--force\b"
    r"|\bchmod\s+777\b|\bsudo\b|\bmkfs\b|\bdd\s+if="
    r"|\bgit\s+push\s+.*--force\b|\bgit\s+clean\s+-f",
    re.IGNORECASE,
)

_SAFE_PATTERNS = re.compile(
    r"Yes, I trust this folder|Enter to confirm|"
    r"Allow once|Allow always|Allow this action|"
    r"Do you want to \w+",
)


def classify_permission(pane_output: str) -> tuple[PaneState, str, bool]:
    """Classify a permission prompt as safe or dangerous.

    Returns:
        (state, prompt_text, is_dangerous)
        - state: PaneState.PERMISSION if a permission prompt is found, else current state
        - prompt_text: the extracted permission-related lines
        - is_dangerous: True if the prompt contains destructive operation keywords
    """
    state = detect_state(pane_output)
    if state != PaneState.PERMISSION:
        return state, "", False

    # Extract permission-related lines (last 20 lines)
    tail_lines = pane_output.splitlines()[-20:]
    prompt_lines = []
    for line in tail_lines:
        stripped = line.strip()
        if stripped and (_PERMISSION_PATTERNS.search(stripped) or _DANGEROUS_KEYWORDS.search(stripped)):
            prompt_lines.append(stripped)
    # If no specific lines matched, take all non-empty tail lines for context
    if not prompt_lines:
        prompt_lines = [l.strip() for l in tail_lines if l.strip()]

    prompt_text = "\n".join(prompt_lines)
    is_dangerous = bool(_DANGEROUS_KEYWORDS.search(prompt_text))
    return state, prompt_text, is_dangerous


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
