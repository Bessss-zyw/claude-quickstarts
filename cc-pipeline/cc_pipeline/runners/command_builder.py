"""Command construction for Claude CLI.

Builds command-line arguments and resolves permission levels.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..constants.enums import PermissionLevel

# Permission level → CC flags
_PERMISSION_MAP: dict[PermissionLevel, dict[str, Any]] = {
    PermissionLevel.READONLY: {
        "tools": ["Read", "Glob", "Grep", "WebSearch", "WebFetch", "mcp__*"],
        "skip": False,
    },
    PermissionLevel.WRITE: {
        "tools": [
            "Read", "Write", "Edit", "Glob", "Grep",
            "WebSearch", "WebFetch",
        ],
        "skip": False,
    },
    PermissionLevel.FULL: {
        "tools": None,
        "skip": True,
    },
}


def resolve_permission(
    perm: PermissionLevel,
    tools_override: list[str] | None,
    skip_override: bool | None,
) -> tuple[list[str] | None, bool]:
    """Resolve effective tools list and skip flag from permission level."""
    defaults = _PERMISSION_MAP[perm]
    tools = tools_override if tools_override is not None else defaults["tools"]
    skip = skip_override if skip_override is not None else defaults["skip"]
    return tools, skip


def build_command(
    *,
    bin_path: str,
    model: str,
    system_prompt: str | None,
    effort: str,
    max_budget_usd: float | None,
    fallback_model: str | None,
    session_id: str | None,
    persist_session: bool,
    session_name: str | None,
    add_dirs: list[Path] | None,
    allowed_tools: list[str] | None,
    skip_permissions: bool,
    extra_flags: list[str] | None,
) -> list[str]:
    """Build the full claude CLI command."""
    cmd = [
        bin_path,
        "--print", "--bare", "--verbose",
        "--output-format", "stream-json",
        "--model", model,
        "--effort", effort,
    ]
    if system_prompt:
        cmd.extend(["--append-system-prompt", system_prompt])
    if max_budget_usd is not None:
        cmd.extend(["--max-budget-usd", str(max_budget_usd)])
    if fallback_model:
        cmd.extend(["--fallback-model", fallback_model])
    if session_id:
        cmd.extend(["--resume", session_id])
    if not persist_session:
        cmd.append("--no-session-persistence")
    if session_name:
        cmd.extend(["--name", session_name])
    if add_dirs:
        for d in add_dirs:
            cmd.extend(["--add-dir", str(d)])
    if skip_permissions:
        cmd.append("--dangerouslySkipPermissions")
    if allowed_tools:
        for t in allowed_tools:
            cmd.extend(["--allowedTools", t])
    if extra_flags:
        cmd.extend(extra_flags)
    return cmd
