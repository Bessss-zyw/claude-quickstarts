"""Claude CLI runner implementation.

Async subprocess wrapper for ``claude --print --bare``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from ..constants.enums import PermissionLevel
from ..state.models import RunResult
from .base import Runner
from .command_builder import build_command, resolve_permission
from .ndjson_parser import parse_ndjson

logger = logging.getLogger(__name__)


class ClaudeCliRunner(Runner):
    """Subprocess-based Claude CLI runner."""

    def __init__(self, claude_bin: str = "claude"):
        self._bin = claude_bin

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
        perm = PermissionLevel(permission)
        tools, skip = resolve_permission(
            perm, allowed_tools, skip_permissions,
        )
        cmd = build_command(
            bin_path=self._bin, model=model,
            system_prompt=system_prompt, effort=effort,
            max_budget_usd=max_budget_usd,
            fallback_model=fallback_model,
            session_id=session_id,
            persist_session=persist_session,
            session_name=session_name,
            add_dirs=add_dirs, allowed_tools=tools,
            skip_permissions=skip,
            extra_flags=extra_flags,
        )
        logger.info("CC cmd: %s", " ".join(cmd))

        t0 = time.monotonic()
        result = await _execute(cmd, prompt, cwd=cwd, timeout=timeout)
        result.duration_seconds = time.monotonic() - t0

        logger.info(
            "CC done in %.1fs exit=%d tokens=%d session=%s",
            result.duration_seconds, result.exit_code,
            result.token_usage.total_tokens,
            result.session_id or "none",
        )
        return result


# ---- subprocess execution ------------------------------------------------

async def _execute(
    cmd: list[str], prompt: str, *,
    cwd: Path | None = None,
    timeout: float | None = None,
) -> RunResult:
    """Run the subprocess and parse NDJSON output."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(input=prompt.encode("utf-8")),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return RunResult(
            exit_code=-1,
            stderr=f"Process timed out after {timeout}s",
        )

    stdout_text = out.decode("utf-8", errors="replace")
    stderr_text = err.decode("utf-8", errors="replace")
    result = parse_ndjson(stdout_text)
    result.exit_code = proc.returncode or 0
    result.stderr = stderr_text
    return result
