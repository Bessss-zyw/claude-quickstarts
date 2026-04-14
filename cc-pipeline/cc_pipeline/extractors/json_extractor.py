"""JSON output extractor."""

from __future__ import annotations

import logging
from typing import Any

from ..config.models import OutputSpec
from ..utils.json_extract import extract_json
from .base import OutputExtractor

logger = logging.getLogger(__name__)


class JsonExtractor(OutputExtractor):
    """Extract a JSON value from CC text output."""

    def extract(self, text: str, name: str, spec: OutputSpec) -> Any:
        try:
            return extract_json(text)
        except ValueError:
            logger.warning("Failed to extract JSON for output %r", name)
            return None
