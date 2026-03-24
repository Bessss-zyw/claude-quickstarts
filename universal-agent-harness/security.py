"""Path whitelist enforcement and bash command safety checks."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import HarnessConfig


# ── Dangerous bash patterns (blacklist) ────────────────────────────────────

_DANGEROUS_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\s+-[^\s]*r[^\s]*f[^\s]*\s+/\s"),
    re.compile(r"\brm\s+-[^\s]*f[^\s]*r[^\s]*\s+/\s"),
    re.compile(r"\bchmod\s+777\b"),
    re.compile(r"\bcurl\b.*\|\s*\bbash\b"),
    re.compile(r"\bwget\b.*\|\s*\bbash\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\s+.*of=/dev/"),
    re.compile(r">\s*/etc/"),
    re.compile(r">\s*/dev/sd"),
]


def is_path_allowed(path: str, config: "HarnessConfig") -> bool:
    """Return True if *path* is within the working dir or any allowed_path."""
    real = os.path.realpath(path)
    allowed_roots = [os.path.realpath(config.working_dir)]
    for ap in config.allowed_paths:
        allowed_roots.append(os.path.realpath(ap))
    return any(real == root or real.startswith(root + os.sep) for root in allowed_roots)


def check_bash_safety(command: str) -> str | None:
    """Return error message if *command* matches a dangerous pattern, else None."""
    for pat in _DANGEROUS_PATTERNS:
        if pat.search(command):
            return f"Command blocked by safety check: matched dangerous pattern '{pat.pattern}'"
    return None
