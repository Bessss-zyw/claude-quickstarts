"""Number output extractor."""

from __future__ import annotations

import logging
import re
from typing import Any

from ..config.models import OutputSpec
from ..utils.json_extract import extract_json
from .base import OutputExtractor

logger = logging.getLogger(__name__)


class NumberExtractor(OutputExtractor):
    """Extract a numeric value from CC text output."""

    def extract(self, text: str, name: str, spec: OutputSpec) -> Any:
        # 1. Try JSON field
        try:
            data = extract_json(text)
            if isinstance(data, dict) and name in data:
                val = data[name]
                if isinstance(val, (int, float)):
                    return float(val)
        except ValueError:
            pass

        # 2. Try key: value pattern
        pattern = rf"{re.escape(name)}\s*[:=]\s*([\d.]+)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                pass

        # 3. Last number in text
        numbers = re.findall(r"\b\d+\.?\d*\b", text)
        if numbers:
            try:
                return float(numbers[-1])
            except ValueError:
                pass

        logger.warning("Failed to extract number for output %r", name)
        return None
