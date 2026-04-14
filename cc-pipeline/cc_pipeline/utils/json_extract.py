"""JSON extraction from LLM text output.

Pure utility functions — no dependencies on any cc-pipeline module.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Patterns for code-fenced JSON
_CODE_FENCE_PATTERNS = [
    re.compile(r"```json\s*\n(.*?)```", re.DOTALL),
    re.compile(r"```\s*\n(.*?)```", re.DOTALL),
]

# Patterns for inline JSON objects/arrays
_INLINE_PATTERNS = [
    re.compile(r"(\{[\s\S]*\})"),
    re.compile(r"(\[[\s\S]*\])"),
]


def extract_json(text: str) -> Any:
    """Extract a JSON value from LLM text output.

    Tries, in order:
    1. ``json ... `` code fences
    2. `` ... `` code fences (no language tag)
    3. Raw JSON (text starts with ``{`` or ``[``)
    4. First JSON object/array found anywhere in the text

    Returns:
        Parsed JSON value.

    Raises:
        ValueError: If no valid JSON is found.
    """
    # 1-2. Code fences
    for pattern in _CODE_FENCE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                continue

    # 3. Raw JSON
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # 4. Inline JSON anywhere
    for pattern in _INLINE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue

    raise ValueError(f"No valid JSON found in text ({len(text)} chars)")
