"""NDJSON stream parser for Claude CLI output.

Parses the stream-json output format and accumulates token usage.
"""

from __future__ import annotations

import json

from ..state.models import RunResult, TokenUsage


def parse_ndjson(output: str) -> RunResult:
    """Parse NDJSON stream output into a RunResult."""
    events: list[dict] = []
    text_parts: list[str] = []
    session_id: str | None = None
    usage = TokenUsage()

    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(event)
        etype = event.get("type", "")

        if etype == "result":
            if "result" in event:
                text_parts.append(event["result"])
            if "session_id" in event:
                session_id = event["session_id"]
            _accumulate_usage(event, usage)

        elif etype == "assistant":
            msg = event.get("message", {})
            for blk in msg.get("content", []):
                if blk.get("type") == "text":
                    text_parts.append(blk["text"])
            if "usage" in msg:
                _accumulate_usage({"usage": msg["usage"]}, usage)

        elif etype == "usage":
            _accumulate_usage(event, usage)

    return RunResult(
        text=text_parts[-1] if text_parts else "",
        session_id=session_id,
        token_usage=usage,
        raw_events=events,
    )


def _accumulate_usage(event: dict, usage: TokenUsage) -> None:
    """Extract and accumulate token usage from an NDJSON event."""
    u = event.get("usage", {})
    if not u:
        for key in ("result", "message"):
            sub = event.get(key)
            if isinstance(sub, dict):
                u = sub.get("usage", {})
                if u:
                    break
    if u:
        usage.input_tokens += u.get("input_tokens", 0)
        usage.output_tokens += u.get("output_tokens", 0)
        usage.cache_read_tokens += u.get("cache_read_input_tokens", 0)
        usage.cache_creation_tokens += u.get(
            "cache_creation_input_tokens", 0,
        )
