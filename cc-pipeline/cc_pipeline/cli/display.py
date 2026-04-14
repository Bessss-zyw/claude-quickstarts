"""CLI display helpers — wave printing, status summary, run summary.

Extracted from cli/main.py and orchestration/orchestrator.py
to keep display logic in one place.
"""

from __future__ import annotations

from typing import Any

from ..config.models import TaskConfig
from ..constants.enums import STAGE_STATUS_ICONS, StageType
from ..state.models import PipelineState


def print_waves(config: TaskConfig) -> None:
    """Print execution plan as numbered waves."""
    remaining = set(config.stages)
    waves: list[list[str]] = []
    while remaining:
        wave = [
            n for n in remaining
            if all(d not in remaining for d in config.stages[n].depends_on)
        ]
        if not wave:
            break
        waves.append(sorted(wave))
        remaining -= set(wave)

    print("Execution plan:")
    print("-" * 40)
    for idx, wave in enumerate(waves):
        tag = " (parallel)" if len(wave) > 1 else ""
        print(f"\n  Wave {idx + 1}{tag}:")
        for name in wave:
            s = config.stages[name]
            deps = (
                f" \u2190 [{', '.join(s.depends_on)}]"
                if s.depends_on else ""
            )
            loop = (
                f" (loop, max={s.max_iterations})"
                if s.type == StageType.LOOP else ""
            )
            print(
                f"    \u2022 {name} [{s.type.value}] "
                f"model={s.model} effort={s.effort} "
                f"perm={s.permissions.value}{loop}{deps}"
            )


def state_summary(state: PipelineState) -> dict[str, Any]:
    """Build a JSON-serializable status summary."""
    stages: dict[str, Any] = {}
    for name, ss in state.stages.items():
        info: dict[str, Any] = {
            "status": ss.status.value,
            "started_at": ss.started_at,
            "finished_at": ss.finished_at,
        }
        if ss.error:
            info["error"] = ss.error
        if ss.loop:
            info["loop"] = {
                "current_iteration": ss.loop.current_iteration,
                "max_iterations": ss.loop.max_iterations,
                "converged": ss.loop.converged,
            }
        stages[name] = info
    return {
        "pipeline_name": state.pipeline_name,
        "status": state.status.value,
        "stages": stages,
    }


def print_pipeline_summary(state: PipelineState, elapsed: float) -> None:
    """Print final pipeline summary to stdout."""
    total = state.total_token_usage
    print("\n" + "=" * 60)
    print(f"Pipeline: {state.pipeline_name}")
    print(f"Status:   {state.status.value}")
    print(f"Duration: {elapsed:.1f}s")
    print(f"Tokens:   {total.total_tokens:,} "
          f"(in={total.input_tokens:,}, out={total.output_tokens:,})")
    print("-" * 60)

    for name, ss in state.stages.items():
        icon = STAGE_STATUS_ICONS.get(ss.status, "?")
        line = f"  {icon} {name}: {ss.status.value}"
        if ss.error:
            line += f" — {ss.error[:60]}"
        if ss.loop:
            line += (
                f" (iter {ss.loop.current_iteration + 1}"
                f"/{ss.loop.max_iterations}"
            )
            if ss.loop.converged:
                line += f", converged={ss.loop.convergence_value}"
            line += ")"
        print(line)

    print("=" * 60)
