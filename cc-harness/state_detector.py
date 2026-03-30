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
#
# CC has three primary states, distinguished by the bottom of the pane:
#
#   PERMISSION:  ❯ followed by a numbered choice menu (❯ 1. Yes / 2. No)
#                or other permission prompts (Esc to cancel, [y/N], etc.)
#
#   ACTIVE:      A spinner line like "Zigzagging… (17m 28s · ↓ 2.5k tokens)"
#                The verb changes randomly (Thinking, Pondering, Noodling, …)
#                Pattern: <Capitalized-verb>… or <Capitalized-verb>ing…
#                Also: tool-use indicators (✢, ✽, * Verb)
#
#   IDLE:        A bare ❯ prompt on its own line (no menu items after it)
#
# Detection priority: EXPIRED > PERMISSION > ACTIVE > IDLE > UNKNOWN
#

# ❯ followed by numbered Yes choice — CC is waiting for permission approval
_PERMISSION_MENU = re.compile(r"❯\s*1\.\s*Yes")

# Other permission prompt indicators (legacy and alternative UI)
_PERMISSION_OTHER = re.compile(
    r"Esc to cancel"
    r"|Do you want to \w+"
    r"|Permission rule .+ requires confirmation"
    r"|Allow this action"
    r"|\[y/N\]|\[Y/n\]|Allow once|Allow always"
    r"|Yes, I trust this folder|Enter to confirm"
)

# Active spinner: a capitalized word ending in "…" (e.g. "Thinking…", "Zigzagging…")
# Also matches tool-execution indicators (✢, ✽, * Verb)
_ACTIVE_SPINNER = re.compile(
    r"[A-Z][a-z]+(?:ing)?…"       # "Thinking…", "Zigzagging…", etc.
    r"|[✢✽]"                       # tool-use glyphs
    r"|\*\s[A-Z][a-z]"             # "* Envisioning…" etc.
)

# Bare ❯ prompt on its own line — CC is idle, waiting for user input
_IDLE_PROMPT = re.compile(r"^\s*❯\s*$", re.MULTILINE)

_EXPIRED_PATTERNS = re.compile(
    r"Timed out waiting for job step|End crun session|"
    r"Connection closed by remote host|Connection reset by peer|"
    r"srun: error:"
)

_CTX_PCT_PATTERN = re.compile(r"(\d+)%")

_COST_PATTERN = re.compile(r"\$[\d.]+")


def detect_state(pane_output: str) -> PaneState:
    """Analyze tmux pane output to determine CC state.

    Uses a clean three-state model:
      PERMISSION — ❯ is followed by "1. Yes" (numbered choice menu)
      ACTIVE     — spinner verb with "…" (e.g. "Thinking…")
      IDLE       — bare ❯ prompt, nothing after it

    Plus EXPIRED for session death and UNKNOWN as fallback.
    """
    if not pane_output.strip():
        return PaneState.UNKNOWN

    # Check last ~50 lines (enough to see permission prompts after long output)
    tail = "\n".join(pane_output.splitlines()[-50:])

    # 1. Session expired — highest priority
    if _EXPIRED_PATTERNS.search(tail):
        return PaneState.EXPIRED

    # 2. Permission — ❯ with numbered Yes/No menu, or other permission prompts
    if _PERMISSION_MENU.search(tail) or _PERMISSION_OTHER.search(tail):
        return PaneState.PERMISSION

    # 3. Active — spinner verb (Xxxing…) or tool-use glyphs
    if _ACTIVE_SPINNER.search(tail):
        return PaneState.ACTIVE

    # 4. Idle — bare ❯ prompt
    if _IDLE_PROMPT.search(tail):
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
    r"|\bformat\b|\breset\s+--hard\b|\bforce\s+push\b"
    r"|\bchmod\s+777\b|\bmkfs\b|\bdd\s+if="
    r"|\bgit\s+push\s+.*--force\b|\bgit\s+clean\s+-f"
    # sudo: only flag interactive/privileged sudo, not safe checks like "sudo -n true"
    r"|\bsudo\s+(?!-n\b)",
    re.IGNORECASE,
)

# Patterns that look dangerous but are actually safe in our workflow.
# If ALL dangerous matches are covered by these overrides, classify as safe.
_DANGEROUS_OVERRIDES = re.compile(
    # dangerouslyDisableSandbox is routine for SSH-based GPU work
    r"dangerouslyDisableSandbox"
    # --force in pip install --force-reinstall is safe
    r"|pip\s+install\s+.*--force",
    re.IGNORECASE,
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
        if stripped and (
            _PERMISSION_MENU.search(stripped)
            or _PERMISSION_OTHER.search(stripped)
            or _DANGEROUS_KEYWORDS.search(stripped)
        ):
            prompt_lines.append(stripped)
    # If no specific lines matched, take all non-empty tail lines for context
    if not prompt_lines:
        prompt_lines = [l.strip() for l in tail_lines if l.strip()]

    prompt_text = "\n".join(prompt_lines)
    is_dangerous = bool(_DANGEROUS_KEYWORDS.search(prompt_text))
    # Check if all "dangerous" matches are actually safe overrides
    if is_dangerous and _DANGEROUS_OVERRIDES.search(prompt_text):
        # Re-check: strip the overridden parts and see if anything dangerous remains
        cleaned = _DANGEROUS_OVERRIDES.sub("", prompt_text)
        is_dangerous = bool(_DANGEROUS_KEYWORDS.search(cleaned))
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
