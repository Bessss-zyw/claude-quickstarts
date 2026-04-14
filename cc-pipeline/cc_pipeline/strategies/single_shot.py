"""Single-shot stage strategy.

Used for pre-exec, subtask, and post-exec stages.
Resolve inputs → render prompt → run CC → extract outputs → save.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..config.models import StageConfig
from ..runners.base import RunResult
from ..templates.engine import build_template_context
from .base import StageContext, StageStrategy

logger = logging.getLogger(__name__)


class SingleShotStrategy(StageStrategy):
    """Execute a stage with one CC invocation."""

    async def execute(
        self, ctx: StageContext, stage_cfg: StageConfig,
    ) -> dict[str, Any]:
        outputs, _result = await run_cc_once(
            ctx, stage_cfg,
            session_name=f"{ctx.config.name}/{ctx.stage_name}",
            persist_session=stage_cfg.persist_session,
        )
        return outputs


def _gather_upstream(
    ctx: StageContext, stage_cfg: StageConfig,
) -> dict[str, dict[str, Any]]:
    """Collect outputs from dependency stages only."""
    return {
        dep: ctx.artifacts.get_stage_outputs(dep)
        for dep in stage_cfg.depends_on
    }


def _check_result(stage_name: str, result: RunResult) -> None:
    if result.exit_code != 0:
        if not result.text:
            raise RuntimeError(
                f"CC exited with code {result.exit_code}: "
                f"{result.stderr[:500]}"
            )
        logger.warning(
            "Stage %r: CC exit=%d but produced text (%d chars)",
            stage_name, result.exit_code, len(result.text),
        )


async def run_cc_once(
    ctx: StageContext,
    stage_cfg: StageConfig,
    *,
    session_name: str,
    persist_session: bool = False,
    loop_context: dict[str, Any] | None = None,
    iteration: int | None = None,
    upstream: dict[str, dict[str, Any]] | None = None,
    sub_stage: str | None = None,
) -> tuple[dict[str, Any], RunResult]:
    """Resolve → render → run CC → check → extract → save.

    Shared by SingleShotStrategy and LoopStrategy.
    """
    resolved = ctx.artifacts.resolve_inputs(
        stage_cfg.inputs, ctx.config.working_dir,
    )
    ups = upstream if upstream is not None else _gather_upstream(ctx, stage_cfg)
    context = build_template_context(
        stage_name=ctx.stage_name,
        inputs=resolved,
        stage_outputs=ups,
        loop_context=loop_context,
        config_values={"name": ctx.config.name, **ctx.config.defaults},
    )
    prompt = ctx.templates.render(stage_cfg.prompt_template, context)

    result = await ctx.runner.run(
        prompt=prompt,
        model=stage_cfg.model,
        effort=stage_cfg.effort,
        max_budget_usd=stage_cfg.max_budget_usd,
        fallback_model=stage_cfg.fallback_model,
        system_prompt=stage_cfg.system_prompt,
        permission=stage_cfg.permissions.value,
        add_dirs=[Path(d) for d in stage_cfg.add_dirs],
        persist_session=persist_session,
        session_name=session_name,
        cwd=Path(ctx.config.working_dir),
        extra_flags=stage_cfg.extra_flags,
    )

    _check_result(ctx.stage_name, result)

    outputs = ctx.extractors.extract_all(result.text, stage_cfg.outputs)
    ctx.artifacts.save_result(
        ctx.stage_name, result, outputs=outputs, iteration=iteration,
        sub_stage=sub_stage,
    )
    ctx.artifacts.save_log(ctx.stage_name, result)
    return outputs, result
