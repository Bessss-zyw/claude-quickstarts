"""File output extractor."""

from __future__ import annotations

from typing import Any

from ..config.models import OutputSpec
from .base import OutputExtractor


class FileExtractor(OutputExtractor):
    """Extract file path from CC output (or use spec.path)."""

    def extract(self, text: str, name: str, spec: OutputSpec) -> Any:
        return spec.path or text.strip()
