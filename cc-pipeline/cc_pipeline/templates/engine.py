"""Minimal prompt template engine for cc-pipeline.

Supports:
- {{inputs.X}} — stage input values
- {{stages.X.outputs.Y}} — upstream outputs
- {{loop.previous.outputs.X}} — previous iteration
- {{loop.iteration}} — current iteration number
- {{#if VAR}}...{{/if}} — conditional blocks
- {{env.X}} — environment variables
- {{config.X}} — task config values
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_VAR_RE = re.compile(r"\{\{([\w.]+)\}\}")
_IF_RE = re.compile(
    r"\{\{#if\s+([\w.]+)\}\}(.*?)\{\{/if\}\}", re.DOTALL,
)


class TemplateEngine:
    """Resolves prompt templates against a context dict."""

    def render(self, template: str, context: dict[str, Any]) -> str:
        result = self._process_conditionals(template, context)
        return self._replace_variables(result, context)

    def _process_conditionals(
        self, text: str, context: dict,
    ) -> str:
        def _replace(match: re.Match) -> str:
            val = _resolve_path(match.group(1), context)
            if val is not None and val != "" and val is not False:
                return match.group(2)
            return ""

        prev = None
        while prev != text:
            prev = text
            text = _IF_RE.sub(_replace, text)
        return text

    def _replace_variables(
        self, text: str, context: dict,
    ) -> str:
        def _replace(match: re.Match) -> str:
            path = match.group(1)
            val = _resolve_path(path, context)
            if val is None:
                logger.warning("Unresolved template var: {{%s}}", path)
                return f"{{{{UNRESOLVED:{path}}}}}"
            return _format_value(val)

        return _VAR_RE.sub(_replace, text)


def _resolve_path(path: str, context: dict) -> Any:
    parts = path.split(".")
    current: Any = context
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return None
    return current


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    if value is None:
        return ""
    return str(value)


def build_template_context(
    *,
    stage_name: str,
    inputs: dict[str, Any],
    stage_outputs: dict[str, dict[str, Any]],
    loop_context: dict[str, Any] | None = None,
    config_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the template context dict for a stage."""
    stages_ctx = {
        name: {"outputs": outs} for name, outs in stage_outputs.items()
    }
    ctx: dict[str, Any] = {
        "inputs": inputs,
        "stages": stages_ctx,
        "env": dict(os.environ),
        "config": config_values or {},
    }
    if loop_context:
        ctx["loop"] = loop_context
    return ctx
