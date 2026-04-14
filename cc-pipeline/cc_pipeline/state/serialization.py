"""State serialization and deserialization.

Converts between PipelineState dataclasses and JSON-safe dicts.
Extracted from manager.py to keep it under 200 lines.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..constants.enums import (
    IterationStatus,
    PipelineStatus,
    StageStatus,
    StageType,
)
from ..utils.time_utils import now_iso
from ..config.models import TaskConfig
from .models import (
    ItemState,
    IterationState,
    LoopState,
    MapState,
    PipelineState,
    StageState,
    SubStageState,
    TokenUsage,
)


def fresh_state(
    config: TaskConfig, task_file: str = "",
) -> PipelineState:
    """Create a brand-new PipelineState from config."""
    stages: dict[str, StageState] = {}
    for name, cfg in config.stages.items():
        loop = None
        map_st = None
        if cfg.type == StageType.LOOP:
            loop = LoopState(max_iterations=cfg.max_iterations)
        elif cfg.type == StageType.MAP:
            map_st = MapState()
        stages[name] = StageState(name=name, loop=loop, map_state=map_st)

    return PipelineState(
        pipeline_name=config.name,
        task_file=task_file,
        started_at=now_iso(),
        status=PipelineStatus.RUNNING,
        stages=stages,
    )


def serialize(state: PipelineState) -> dict:
    """Convert PipelineState → JSON-safe dict."""
    stages = {}
    for name, ss in state.stages.items():
        sd: dict[str, Any] = {
            "name": ss.name,
            "status": ss.status.value,
            "started_at": ss.started_at,
            "finished_at": ss.finished_at,
            "session_id": ss.session_id,
            "token_usage": (
                ss.token_usage.to_dict() if ss.token_usage else None
            ),
            "error": ss.error,
            "retry_count": ss.retry_count,
            "outputs": ss.outputs,
            "loop": _serialize_loop(ss.loop),
            "map_state": _serialize_map(ss.map_state),
        }
        stages[name] = sd

    return {
        "pipeline_name": state.pipeline_name,
        "task_file": state.task_file,
        "started_at": state.started_at,
        "finished_at": state.finished_at,
        "status": state.status.value,
        "stages": stages,
        "total_token_usage": state.total_token_usage.to_dict(),
        "total_cost_usd": state.total_cost_usd,
    }


def _serialize_loop(loop: LoopState | None) -> dict | None:
    if loop is None:
        return None
    iters = {}
    for k, it in loop.iterations.items():
        sub_stages = {}
        for sn, ss in it.sub_stages.items():
            sub_stages[sn] = {
                "name": ss.name,
                "status": ss.status.value,
                "started_at": ss.started_at,
                "finished_at": ss.finished_at,
                "outputs": ss.outputs,
                "session_id": ss.session_id,
                "token_usage": (
                    ss.token_usage.to_dict() if ss.token_usage else None
                ),
                "error": ss.error,
            }
        iters[k] = {
            "status": it.status.value,
            "started_at": it.started_at,
            "finished_at": it.finished_at,
            "outputs": it.outputs,
            "session_id": it.session_id,
            "token_usage": (
                it.token_usage.to_dict() if it.token_usage else None
            ),
            "error": it.error,
            "sub_stages": sub_stages,
        }
    return {
        "current_iteration": loop.current_iteration,
        "max_iterations": loop.max_iterations,
        "iterations": iters,
        "converged": loop.converged,
        "convergence_value": loop.convergence_value,
    }


def _serialize_map(map_state: MapState | None) -> dict | None:
    if map_state is None:
        return None
    items = {}
    for k, item in map_state.items.items():
        sub_stages = {}
        for sn, ss in item.sub_stages.items():
            sub_stages[sn] = {
                "name": ss.name,
                "status": ss.status.value,
                "started_at": ss.started_at,
                "finished_at": ss.finished_at,
                "outputs": ss.outputs,
                "session_id": ss.session_id,
                "token_usage": (
                    ss.token_usage.to_dict() if ss.token_usage else None
                ),
                "error": ss.error,
            }
        items[k] = {
            "index": item.index,
            "status": item.status.value,
            "started_at": item.started_at,
            "finished_at": item.finished_at,
            "outputs": item.outputs,
            "sub_stages": sub_stages,
            "error": item.error,
        }
    return {
        "total_items": map_state.total_items,
        "items": items,
    }


def _deserialize_map(raw: dict | None) -> MapState | None:
    if not raw:
        return None
    items: dict[str, ItemState] = {}
    for k, idata in raw.get("items", {}).items():
        sub_stages: dict[str, SubStageState] = {}
        for sn, sdata in idata.get("sub_stages", {}).items():
            sub_stages[sn] = SubStageState(
                name=sdata.get("name", sn),
                status=StageStatus(sdata.get("status", "pending")),
                started_at=sdata.get("started_at"),
                finished_at=sdata.get("finished_at"),
                outputs=sdata.get("outputs"),
                session_id=sdata.get("session_id"),
                token_usage=_deserialize_usage(sdata.get("token_usage")),
                error=sdata.get("error"),
            )
        items[k] = ItemState(
            index=idata.get("index", int(k)),
            status=StageStatus(idata.get("status", "pending")),
            started_at=idata.get("started_at"),
            finished_at=idata.get("finished_at"),
            outputs=idata.get("outputs"),
            sub_stages=sub_stages,
            error=idata.get("error"),
        )
    return MapState(
        total_items=raw.get("total_items", 0),
        items=items,
    )


def deserialize(path: Path) -> PipelineState:
    """Reconstruct PipelineState from a JSON file."""
    with open(path) as f:
        data = json.load(f)
    return _dict_to_state(data)


def _dict_to_state(data: dict) -> PipelineState:
    stages: dict[str, StageState] = {}
    for sname, sd in data.get("stages", {}).items():
        loop = _deserialize_loop(sd.get("loop"))
        map_st = _deserialize_map(sd.get("map_state"))
        tu = _deserialize_usage(sd.get("token_usage"))
        stages[sname] = StageState(
            name=sd.get("name", sname),
            status=StageStatus(sd.get("status", "pending")),
            started_at=sd.get("started_at"),
            finished_at=sd.get("finished_at"),
            session_id=sd.get("session_id"),
            token_usage=tu,
            error=sd.get("error"),
            retry_count=sd.get("retry_count", 0),
            loop=loop,
            map_state=map_st,
            outputs=sd.get("outputs"),
        )

    total_tu = TokenUsage()
    if data.get("total_token_usage"):
        total_tu = TokenUsage.from_dict(data["total_token_usage"])

    return PipelineState(
        pipeline_name=data.get("pipeline_name", ""),
        task_file=data.get("task_file", ""),
        started_at=data.get("started_at", ""),
        finished_at=data.get("finished_at"),
        status=PipelineStatus(data.get("status", "pending")),
        stages=stages,
        total_token_usage=total_tu,
        total_cost_usd=data.get("total_cost_usd", 0.0),
    )


def _deserialize_loop(raw: dict | None) -> LoopState | None:
    if not raw:
        return None
    iters = {}
    for k, idata in raw.get("iterations", {}).items():
        sub_stages: dict[str, SubStageState] = {}
        for sn, sdata in idata.get("sub_stages", {}).items():
            sub_stages[sn] = SubStageState(
                name=sdata.get("name", sn),
                status=StageStatus(sdata.get("status", "pending")),
                started_at=sdata.get("started_at"),
                finished_at=sdata.get("finished_at"),
                outputs=sdata.get("outputs"),
                session_id=sdata.get("session_id"),
                token_usage=_deserialize_usage(sdata.get("token_usage")),
                error=sdata.get("error"),
            )
        iters[k] = IterationState(
            status=IterationStatus(idata.get("status", "pending")),
            started_at=idata.get("started_at"),
            finished_at=idata.get("finished_at"),
            outputs=idata.get("outputs"),
            session_id=idata.get("session_id"),
            token_usage=_deserialize_usage(idata.get("token_usage")),
            error=idata.get("error"),
            sub_stages=sub_stages,
        )
    return LoopState(
        current_iteration=raw.get("current_iteration", 0),
        max_iterations=raw.get("max_iterations", 5),
        iterations=iters,
        converged=raw.get("converged", False),
        convergence_value=raw.get("convergence_value"),
    )


def _deserialize_usage(raw: dict | None) -> TokenUsage | None:
    if not raw:
        return None
    return TokenUsage.from_dict(raw)
