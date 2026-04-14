"""Map stage strategy.

Processes a list of items concurrently, each running a mini-DAG of
sub-stages. Items are independent — no ``previous`` context between them.
Concurrency is bounded by ``max_parallel``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ..config.models import StageConfig
from ..constants.enums import StageStatus
from ..state.models import ItemState, RunResult, SubStageState
from ..templates.engine import build_template_context
from .base import StageContext, StageStrategy
from .single_shot import run_cc_once

logger = logging.getLogger(__name__)


class MapStrategy(StageStrategy):
    """Execute a map stage: process items concurrently, each with a mini-DAG."""

    async def execute(
        self, ctx: StageContext, stage_cfg: StageConfig,
    ) -> dict[str, Any]:
        items = _load_items(stage_cfg.items, ctx.config.working_dir)
        map_state = ctx.state_mgr.state.stages[ctx.stage_name].map_state
        if map_state is None:
            raise RuntimeError(
                f"Stage {ctx.stage_name!r} missing map_state"
            )
        map_state.total_items = len(items)
        ctx.state_mgr.save()

        sem = asyncio.Semaphore(stage_cfg.map_max_parallel)

        async def process_one(index: int, item: dict) -> dict[str, dict]:
            async with sem:
                return await self._run_item_dag(
                    ctx, stage_cfg, index, item,
                )

        tasks = [
            asyncio.create_task(process_one(i, item))
            for i, item in enumerate(items)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results, handle failures
        all_outputs: dict[str, Any] = {}
        errors: list[str] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                errors.append(f"Item {i}: {result}")
            else:
                all_outputs[str(i)] = result

        if errors:
            logger.error(
                "Map stage %r: %d/%d items failed",
                ctx.stage_name, len(errors), len(items),
            )
            if len(errors) == len(items):
                raise RuntimeError(
                    f"All {len(items)} items failed: {errors[0]}"
                )

        # Save aggregated outputs at stage level
        ctx.artifacts.save_result(
            ctx.stage_name,
            RunResult(text="(map final)"),
            outputs=all_outputs,
        )
        return all_outputs

    async def _run_item_dag(
        self,
        ctx: StageContext,
        stage_cfg: StageConfig,
        index: int,
        item: dict,
    ) -> dict[str, dict]:
        """Run sub-stages mini-DAG for a single item."""
        sub_cfgs = stage_cfg.sub_stages
        completed: dict[str, dict] = {}

        # External upstream outputs
        upstream = {
            dep: ctx.artifacts.get_stage_outputs(dep)
            for dep in stage_cfg.depends_on
        }

        self._init_item_state(ctx, stage_cfg, index)
        self._mark_item_running(ctx, index)

        try:
            while len(completed) < len(sub_cfgs):
                ready = [
                    name for name in sub_cfgs
                    if name not in completed
                    and all(
                        d in completed
                        for d in sub_cfgs[name].depends_on
                    )
                ]
                if not ready:
                    remaining = set(sub_cfgs) - set(completed)
                    raise RuntimeError(
                        f"Deadlock in item {index} sub-stage DAG: "
                        f"{remaining}"
                    )

                tasks = [
                    self._run_sub_stage(
                        ctx, stage_cfg, sub_cfgs[name],
                        index, item, completed, upstream,
                    )
                    for name in ready
                ]
                results = await asyncio.gather(*tasks)
                for name, outputs in zip(ready, results):
                    completed[name] = outputs

            self._mark_item_done(ctx, index, completed)
            return completed

        except Exception as e:
            self._mark_item_failed(ctx, index, str(e))
            raise

    async def _run_sub_stage(
        self,
        ctx: StageContext,
        parent_cfg: StageConfig,
        sub_cfg: StageConfig,
        index: int,
        item: dict,
        completed: dict[str, dict[str, Any]],
        upstream: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Run a single sub-stage for one item."""
        # Build map-specific template context
        map_ctx: dict[str, Any] = {
            "stages": {
                n: {"outputs": o} for n, o in completed.items()
            },
        }

        resolved = ctx.artifacts.resolve_inputs(
            sub_cfg.inputs, ctx.config.working_dir,
        )
        context = build_template_context(
            stage_name=sub_cfg.name,
            inputs=resolved,
            stage_outputs=upstream,
            config_values={
                "name": ctx.config.name, **ctx.config.defaults,
            },
        )
        # Add map-specific context
        context["item"] = item
        context["item_index"] = index
        context["map"] = map_ctx

        prompt = ctx.templates.render(sub_cfg.prompt_template, context)

        # Mark sub-stage running
        self._mark_sub_stage_running(ctx, index, sub_cfg.name)

        result = await ctx.runner.run(
            prompt=prompt,
            model=sub_cfg.model or parent_cfg.model,
            effort=sub_cfg.effort or parent_cfg.effort,
            max_budget_usd=(
                sub_cfg.max_budget_usd or parent_cfg.max_budget_usd
            ),
            fallback_model=(
                sub_cfg.fallback_model or parent_cfg.fallback_model
            ),
            system_prompt=sub_cfg.system_prompt,
            permission=(
                sub_cfg.permissions or parent_cfg.permissions
            ).value,
            add_dirs=[
                Path(d)
                for d in (sub_cfg.add_dirs or parent_cfg.add_dirs)
            ],
            persist_session=False,
            session_name=(
                f"{ctx.config.name}/{ctx.stage_name}"
                f"/item{index}/{sub_cfg.name}"
            ),
            cwd=Path(ctx.config.working_dir),
            extra_flags=sub_cfg.extra_flags or parent_cfg.extra_flags,
        )

        outputs = ctx.extractors.extract_all(
            result.text, sub_cfg.outputs,
        )

        # Save artifacts — reuse iteration param for item index
        ctx.artifacts.save_result(
            ctx.stage_name, result, outputs=outputs,
            iteration=index, sub_stage=sub_cfg.name,
            prompt=prompt,
        )
        ctx.artifacts.save_log(
            ctx.stage_name, result,
            iteration=index, sub_stage=sub_cfg.name,
        )

        # Mark sub-stage done
        self._mark_sub_stage_done(
            ctx, index, sub_cfg.name, outputs,
            result.session_id, result.token_usage,
        )

        # Accumulate token usage
        if result.token_usage:
            ctx.state_mgr.state.total_token_usage.accumulate(
                result.token_usage,
            )

        return outputs

    # ---- state helpers ---------------------------------------------------

    def _init_item_state(
        self, ctx: StageContext, stage_cfg: StageConfig, index: int,
    ) -> None:
        map_state = ctx.state_mgr.state.stages[ctx.stage_name].map_state
        key = str(index)
        if key not in map_state.items:
            item_state = ItemState(index=index)
            for sub_name in stage_cfg.sub_stages:
                item_state.sub_stages[sub_name] = SubStageState(
                    name=sub_name,
                )
            map_state.items[key] = item_state
        ctx.state_mgr.save()

    def _mark_item_running(
        self, ctx: StageContext, index: int,
    ) -> None:
        ms = ctx.state_mgr.state.stages[ctx.stage_name].map_state
        ms.items[str(index)].status = StageStatus.RUNNING
        ctx.state_mgr.save()

    def _mark_item_done(
        self, ctx: StageContext, index: int,
        outputs: dict[str, dict],
    ) -> None:
        ms = ctx.state_mgr.state.stages[ctx.stage_name].map_state
        item = ms.items[str(index)]
        item.status = StageStatus.DONE
        item.outputs = outputs
        ctx.state_mgr.save()

    def _mark_item_failed(
        self, ctx: StageContext, index: int, error: str,
    ) -> None:
        ms = ctx.state_mgr.state.stages[ctx.stage_name].map_state
        item = ms.items[str(index)]
        item.status = StageStatus.FAILED
        item.error = error
        ctx.state_mgr.save()

    def _mark_sub_stage_running(
        self, ctx: StageContext, index: int, sub_name: str,
    ) -> None:
        ms = ctx.state_mgr.state.stages[ctx.stage_name].map_state
        ss = ms.items[str(index)].sub_stages.get(sub_name)
        if ss:
            ss.status = StageStatus.RUNNING
        ctx.state_mgr.save()

    def _mark_sub_stage_done(
        self, ctx: StageContext, index: int, sub_name: str,
        outputs: dict[str, Any],
        session_id: str | None = None,
        token_usage=None,
    ) -> None:
        ms = ctx.state_mgr.state.stages[ctx.stage_name].map_state
        ss = ms.items[str(index)].sub_stages.get(sub_name)
        if ss:
            ss.status = StageStatus.DONE
            ss.outputs = outputs
            ss.session_id = session_id
            ss.token_usage = token_usage
        ctx.state_mgr.save()


def _load_items(
    items_spec: str | list | None, working_dir: str,
) -> list[dict]:
    """Load items from inline list or JSON file."""
    if items_spec is None:
        raise ValueError("Map stage requires 'items'")
    if isinstance(items_spec, list):
        return items_spec
    # It's a file path
    path = Path(working_dir) / items_spec
    if not path.exists():
        path = Path(items_spec)
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(
            f"items file must contain a JSON array, "
            f"got {type(data).__name__}"
        )
    return data
