"""Text output extractor."""

from __future__ import annotations

from typing import Any

from ..config.models import OutputSpec
from .base import OutputExtractor


class TextExtractor(OutputExtractor):
    """Return the full CC text response."""

    def extract(self, text: str, name: str, spec: OutputSpec) -> Any:
        return text
