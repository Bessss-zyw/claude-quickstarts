"""Pipeline summary generation — JSON and Markdown reports."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..state.models import PipelineState
from .layout import ArtifactLayout

_TICK = "\u2713"
_CROSS = "\u2717"


def _parse_ts(ts: str | None) -> datetime | None:
    """Parse an ISO timestamp string, returning None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _duration_seconds(started: str | None, finished: str | None) -> float:
    """Compute elapsed seconds between two ISO timestamps."""
    s = _parse_ts(started)
    f = _parse_ts(finished)
    if s and f:
        return (f - s).total_seconds()
    return 0.0


def _format_duration(seconds: float) -> str:
    """Format seconds as a human-readable duration string."""
    if seconds < 1:
        return "<1s"
    m, s = divmod(int(seconds), 60)
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _format_tokens(n: int) -> str:
    """Format token count with comma separators."""
    return f"{n:,}"


def generate_summary_json(
    state: PipelineState, layout: ArtifactLayout,
) -> None:
    """Write summary.json with pipeline results."""
    pipeline_dur = _duration_seconds(state.started_at, state.finished_at)

    stages_data: dict[str, Any] = {}
    for name, ss in state.stages.items():
        stage_dur = _duration_seconds(ss.started_at, ss.finished_at)
        entry: dict[str, Any] = {
            "status": ss.status.value,
            "duration_s": round(stage_dur, 2),
        }
        if ss.token_usage:
            entry["tokens"] = ss.token_usage.to_dict()
        if ss.outputs:
            entry["outputs"] = ss.outputs

        # Loop-specific info
        if ss.loop is not None:
            entry["iterations"] = len(ss.loop.iterations)
            entry["converged"] = ss.loop.converged
            if ss.loop.convergence_value is not None:
                entry["convergence_value"] = ss.loop.convergence_value

        stages_data[name] = entry

    summary: dict[str, Any] = {
        "pipeline": state.pipeline_name,
        "status": state.status.value,
        "started_at": state.started_at or None,
        "finished_at": state.finished_at or None,
        "duration_s": round(pipeline_dur, 2),
        "total_tokens": state.total_token_usage.to_dict(),
        "total_cost_usd": round(state.total_cost_usd, 4),
        "stages": stages_data,
    }

    layout.summary_json_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def generate_summary_md(
    state: PipelineState, layout: ArtifactLayout,
) -> None:
    """Write summary.md with human-readable report."""
    pipeline_dur = _duration_seconds(state.started_at, state.finished_at)
    total_tok = state.total_token_usage.total_tokens

    lines: list[str] = []
    lines.append(f"# Pipeline: {state.pipeline_name}")
    lines.append("")
    parts = [
        f"Status: {state.status.value}",
        f"Duration: {_format_duration(pipeline_dur)}",
        f"Tokens: {_format_tokens(total_tok)}",
        f"Cost: ${state.total_cost_usd:.2f}",
    ]
    lines.append(" | ".join(parts))
    lines.append("")
    lines.append("## Stages")
    lines.append("")

    for name, ss in state.stages.items():
        stage_dur = _duration_seconds(ss.started_at, ss.finished_at)
        tok = ss.token_usage.total_tokens if ss.token_usage else 0
        icon = _TICK if ss.status.value == "done" else _CROSS

        if ss.loop is not None:
            conv = ""
            if ss.loop.converged and ss.loop.convergence_value is not None:
                conv = f" (value={ss.loop.convergence_value:.2f})"
            status_desc = (
                f"converged at iter {len(ss.loop.iterations)}"
                if ss.loop.converged
                else f"{len(ss.loop.iterations)} iterations"
            )
            lines.append(
                f"{icon} {name} \u2014 {status_desc}{conv}"
            )
            for key, it in ss.loop.iterations.items():
                it_status = it.status.value
                if it.sub_stages:
                    subs = " | ".join(
                        f"{sn} {_TICK if sv.status.value == 'done' else _CROSS}"
                        for sn, sv in it.sub_stages.items()
                    )
                    lines.append(f"  - iter {key}: {subs}")
                else:
                    lines.append(f"  - iter {key}: {it_status}")
        else:
            lines.append(
                f"{icon} {name} \u2014 {ss.status.value} in "
                f"{_format_duration(stage_dur)} "
                f"({_format_tokens(tok)} tokens)"
            )

    lines.append("")

    layout.summary_md_path.write_text(
        "\n".join(lines), encoding="utf-8",
    )
