"""Bash command execution tool."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from core.tools import register_tool
from core.security import check_bash_safety

if TYPE_CHECKING:
    from config import HarnessConfig

_CONFIG: HarnessConfig | None = None  # injected by ToolRegistry

_MAX_OUTPUT = 10000
_HALF = 5000


def _truncate(text: str) -> str:
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_HALF] + "\n\n... [truncated] ...\n\n" + text[-_HALF:]


def bash(command: str, timeout: int = 120) -> str:
    """Execute a bash command with safety checks and output truncation."""
    assert _CONFIG is not None, "bash tool not initialised (no config)"

    # Safety check
    err = check_bash_safety(command)
    if err:
        return err

    # Prepend shell preamble
    if _CONFIG.shell_preamble:
        preamble = " && ".join(_CONFIG.shell_preamble)
        full_cmd = f"{preamble} && {command}"
    else:
        full_cmd = command

    try:
        proc = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_CONFIG.working_dir,
        )
        stdout = _truncate(proc.stdout)
        stderr = _truncate(proc.stderr)
        return f"exit_code: {proc.returncode}\nstdout:\n{stdout}\nstderr:\n{stderr}"
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"


register_tool(
    name="bash",
    description="Execute a bash command. Returns stdout, stderr, and exit code.",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The bash command to execute."},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)."},
        },
        "required": ["command"],
    },
    fn=bash,
)
