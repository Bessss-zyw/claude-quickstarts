"""Journal tool — every agent can record work history."""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from tools import register_tool

if TYPE_CHECKING:
    from config import HarnessConfig

_CONFIG: HarnessConfig | None = None  # injected by ToolRegistry


def _journal_path(agent_name: str) -> str:
    assert _CONFIG is not None
    return os.path.join(_CONFIG.journals_dir, f"{agent_name}.md")


def write_journal(content: str, _agent_name: str = "") -> str:
    """Append a timestamped session entry to the agent's journal."""
    assert _CONFIG is not None
    if not _agent_name:
        return "Error: agent name not set for journal."

    path = _journal_path(_agent_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Count existing sessions
    existing = ""
    if os.path.exists(path):
        with open(path) as f:
            existing = f.read()
    session_num = len(re.findall(r"^## Session ", existing, re.MULTILINE)) + 1

    timestamp = datetime.now(timezone.utc).isoformat()
    entry = f"\n## Session {session_num} ({timestamp})\n{content}\n"

    with open(path, "a") as f:
        f.write(entry)

    return "Journal updated"


def read_journal(agent_name: str) -> str:
    """Read the full journal for an agent. Returns empty string if none."""
    path = _journal_path(agent_name)
    if not os.path.exists(path):
        return ""
    with open(path) as f:
        return f.read()


def compress_journal_if_needed(agent_name: str, client=None) -> None:
    """Compress journal if it exceeds threshold. Called before each session."""
    assert _CONFIG is not None
    path = _journal_path(agent_name)
    if not os.path.exists(path):
        return
    with open(path) as f:
        content = f.read()
    if len(content) <= _CONFIG.journal_compress_threshold:
        return

    # Archive original
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    archive_path = os.path.join(_CONFIG.journal_archive_dir, f"{agent_name}_{ts}.md")
    os.makedirs(os.path.dirname(archive_path), exist_ok=True)
    shutil.copy2(path, archive_path)

    # If we have an LLM client, compress via LLM
    if client is not None:
        try:
            resp = client.chat(
                messages=[
                    {"role": "system", "content": "Compress the following agent work journal. "
                     "Preserve: 1) key decisions and reasoning, 2) failed attempts (high priority), "
                     "3) important data points and numbers. Remove redundant narrative."},
                    {"role": "user", "content": content},
                ],
                temperature=0.0,
            )
            compressed = resp.choices[0].message.content
            with open(path, "w") as f:
                f.write(compressed)
        except Exception:
            # On failure, just truncate to keep the last portion
            with open(path, "w") as f:
                f.write("[compressed — see archive]\n" + content[-1500:])
    else:
        # No client available, simple truncation
        with open(path, "w") as f:
            f.write("[compressed — see archive]\n" + content[-1500:])


register_tool(
    name="write_journal",
    description="Record your work, decisions, and findings for future sessions.",
    parameters={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Markdown-formatted work record."},
        },
        "required": ["content"],
    },
    fn=write_journal,
)
