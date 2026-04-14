"""Loop stage strategy.

Iterates until convergence or max_iterations. Each iteration uses
a fresh CC session. Crash recovery resumes from last completed iteration.

Supports multi-stage loops: when `sub_stages` is defined, each iteration
runs a mini-DAG of sub-stages instead of a single CC invocation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config.models import StageConfig
from ..constants.enums import IterationStatus, StageStatus
from ..state.models import RunResult, SubStageState, TokenUsage
from .base import StageContext, StageStrategy
from .convergence import OperatorRegistry
from .single_shot import run_cc_once

logger = logging.getLogger(__name__)


class LoopStrategy(StageStrategy):
    """Execute a loop stage with iteration and convergence."""

    def __init__(self, operators: OperatorRegistry):
        self._operators = operators

    async def execute(
        self, ctx: StageContext, stage_cfg: StageConfig,
    ) -> dict[str, Any]:
        loop = ctx.state_mgr.state.stages[ctx.stage_name].loop
        if loop is None:
            raise RuntimeError(f"Stage {ctx.stage_name!r} missing loop state")

        multi = bool(stage_cfg.sub_stages)
        start_iter = _find_resume_point(loop)
        prev_outputs = _load_previous_outputs(ctx, start_iter, multi)
        final_outputs: dict[str, Any] = {}

        for iteration in range(start_iter, stage_cfg.max_iterations):
            logger.info(
                "Stage %r: iteration %d/%d",
                ctx.stage_name, iteration + 1, stage_cfg.max_iterations,
            )
            ctx.state_mgr.mark_iteration_running(ctx.stage_name, iteration)

            try:
                if multi:
                    outputs = await self._run_multi_iteration(
                        ctx, stage_cfg, iteration, prev_outputs,
                    )
                    ctx.state_mgr.mark_iteration_done(
                        ctx.stage_name, iteration, outputs=outputs,
                    )
                else:
                    outputs, result = await self._run_iteration(
                        ctx, stage_cfg, iteration, prev_outputs,
                    )
                    ctx.state_mgr.mark_iteration_done(
                        ctx.stage_name, iteration,
                        outputs=outputs,
                        session_id=result.session_id,
                        token_usage=result.token_usage,
                    )

                final_outputs = outputs
                prev_outputs = outputs

                if self._check_convergence(
                    ctx, stage_cfg, outputs, multi=multi,
                ):
                    break

            except Exception as e:
                ctx.state_mgr.mark_iteration_failed(
                    ctx.stage_name, iteration, error=str(e),
                )
                raise

        # Save final outputs at stage level
        ctx.artifacts.save_result(
            ctx.stage_name,
            RunResult(text="(loop final)"),
            outputs=final_outputs,
        )
        return final_outputs

    async def _run_iteration(
        self,
        ctx: StageContext,
        stage_cfg: StageConfig,
        iteration: int,
        prev_outputs: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], RunResult]:
        loop_ctx: dict[str, Any] = {"iteration": iteration}
        if prev_outputs:
            loop_ctx["previous"] = {"outputs": prev_outputs}

        return await run_cc_once(
            ctx, stage_cfg,
            session_name=(
                f"{ctx.config.name}/{ctx.stage_name}/iter{iteration}"
            ),
            persist_session=False,
            loop_context=loop_ctx,
            iteration=iteration,
        )

    async def _run_multi_iteration(
        self,
        ctx: StageContext,
        stage_cfg: StageConfig,
        iteration: int,
        prev_iter_outputs: dict[str, dict[str, Any]] | None,
    ) -> dict[str, dict[str, Any]]:
        """Run a mini-DAG of sub-stages for one iteration."""
        sub_cfgs = stage_cfg.sub_stages
        completed: dict[str, dict[str, Any]] = {}
        all_sub_names = set(sub_cfgs)

        # Gather upstream outputs from the parent's external dependencies
        upstream: dict[str, dict[str, Any]] = {
            dep: ctx.artifacts.get_stage_outputs(dep)
            for dep in stage_cfg.depends_on
        }

        # Initialize sub-stage state for this iteration
        iter_key = str(iteration)
        loop_state = ctx.state_mgr.state.stages[ctx.stage_name].loop
        if loop_state and iter_key in loop_state.iterations:
            iter_state = loop_state.iterations[iter_key]
            for sn in sub_cfgs:
                if sn not in iter_state.sub_stages:
                    iter_state.sub_stages[sn] = SubStageState(name=sn)

        while len(completed) < len(all_sub_names):
            ready = [
                name for name in all_sub_names
                if name not in completed
                and all(d in completed for d in sub_cfgs[name].depends_on)
            ]
            if not ready:
                remaining = all_sub_names - set(completed)
                raise RuntimeError(
                    f"Deadlock in sub-stage DAG: {remaining}"
                )

            tasks = [
                self._run_sub_stage(
                    ctx, stage_cfg, sub_cfgs[name],
                    iteration, completed, prev_iter_outputs,
                    upstream,
                )
                for name in ready
            ]
            results = await asyncio.gather(*tasks)

            for name, sub_outputs in zip(ready, results):
                completed[name] = sub_outputs

        return completed

    async def _run_sub_stage(
        self,
        ctx: StageContext,
        parent_cfg: StageConfig,
        sub_cfg: StageConfig,
        iteration: int,
        completed: dict[str, dict[str, Any]],
        prev_iter_outputs: dict[str, dict[str, Any]] | None,
        upstream: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Execute a single sub-stage within a multi-stage iteration."""
        loop_ctx: dict[str, Any] = {
            "iteration": iteration,
            "stages": {
                n: {"outputs": o} for n, o in completed.items()
            },
        }
        if prev_iter_outputs:
            loop_ctx["previous"] = {
                "stages": {
                    n: {"outputs": o}
                    for n, o in prev_iter_outputs.items()
                },
            }

        # Mark sub-stage running
        iter_key = str(iteration)
        loop_state = ctx.state_mgr.state.stages[ctx.stage_name].loop
        if loop_state and iter_key in loop_state.iterations:
            ss = loop_state.iterations[iter_key].sub_stages.get(sub_cfg.name)
            if ss:
                ss.status = StageStatus.RUNNING
            ctx.state_mgr.save()

        outputs, result = await run_cc_once(
            ctx, sub_cfg,
            session_name=(
                f"{ctx.config.name}/{ctx.stage_name}"
                f"/iter{iteration}/{sub_cfg.name}"
            ),
            persist_session=False,
            loop_context=loop_ctx,
            iteration=iteration,
            upstream=upstream,
            sub_stage=sub_cfg.name,
        )

        # Mark sub-stage done
        if loop_state and iter_key in loop_state.iterations:
            ss = loop_state.iterations[iter_key].sub_stages.get(sub_cfg.name)
            if ss:
                ss.status = StageStatus.DONE
                ss.outputs = outputs
                ss.session_id = result.session_id
                ss.token_usage = result.token_usage
            ctx.state_mgr.save()

        # Accumulate token usage
        if result.token_usage:
            ctx.state_mgr.state.total_token_usage.accumulate(
                result.token_usage,
            )

        return outputs

    def _check_convergence(
        self,
        ctx: StageContext,
        stage_cfg: StageConfig,
        outputs: dict[str, Any],
        *,
        multi: bool = False,
    ) -> bool:
        if not stage_cfg.convergence:
            return False
        value = _extract_metric(
            stage_cfg.convergence.metric, outputs, multi=multi,
        )
        if value is None:
            logger.warning(
                "Could not extract metric %r", stage_cfg.convergence.metric,
            )
            return False
        converged = self._operators.check(stage_cfg.convergence, value)
        if converged:
            ctx.state_mgr.mark_loop_converged(ctx.stage_name, value)
            logger.info(
                "Stage %r converged (value=%.4f)", ctx.stage_name, value,
            )
        return converged


def _find_resume_point(loop) -> int:  # noqa: ANN001
    """Find the first iteration that needs (re-)execution."""
    for i in range(loop.max_iterations):
        key = str(i)
        it = loop.iterations.get(key)
        if it is None or it.status != IterationStatus.DONE:
            return i
    return loop.max_iterations


def _load_previous_outputs(
    ctx: StageContext, start_iter: int, multi: bool = False,
) -> dict[str, Any] | None:
    """Load outputs from the previous iteration.

    For multi-stage loops, loads the iteration outputs dict which
    contains per-sub-stage outputs: {sub_name: {field: value}}.
    """
    if start_iter > 0:
        return ctx.artifacts.get_iteration_outputs(
            ctx.stage_name, start_iter - 1,
        )
    return None


def _extract_metric(
    metric: str, outputs: dict[str, Any], *, multi: bool = False,
) -> float | None:
    """Extract a numeric metric from outputs.

    For single-stage loops, metric path is like "outputs.speedup"
    and outputs is a flat dict.

    For multi-stage loops, metric path is like "test.outputs.speedup"
    and outputs is {sub_name: {field: value}}.
    """
    parts = metric.split(".")
    if multi:
        # Multi-stage: navigate sub_name.outputs.field within
        # {sub_name: {field: value}} structure
        current: Any = outputs
        for part in parts:
            if part == "outputs" and isinstance(current, dict):
                continue  # skip the "outputs" level — it's implicit
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
    else:
        current = {"outputs": outputs}
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
    if isinstance(current, (int, float)):
        return float(current)
    return None
