"""Output extractor ABC and registry.

New output types are added by implementing OutputExtractor and
registering with ExtractorRegistry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..config.models import OutputSpec
from ..constants.enums import OutputType


class OutputExtractor(ABC):
    """Extract a typed value from raw LLM text output."""

    @abstractmethod
    def extract(self, text: str, name: str, spec: OutputSpec) -> Any:
        """Extract a single output value.

        Args:
            text: Full CC text response.
            name: Output name (e.g. "pass_rate").
            spec: Output specification from config.

        Returns:
            Extracted value (type depends on extractor).
        """
        ...


class ExtractorRegistry:
    """Maps OutputType → OutputExtractor. Trivially extensible."""

    def __init__(self) -> None:
        self._registry: dict[OutputType, OutputExtractor] = {}

    def register(
        self, output_type: OutputType, extractor: OutputExtractor,
    ) -> None:
        self._registry[output_type] = extractor

    def get(self, output_type: OutputType) -> OutputExtractor:
        ext = self._registry.get(output_type)
        if ext is None:
            raise KeyError(
                f"No extractor registered for {output_type.value!r}"
            )
        return ext

    def extract(
        self, text: str, name: str, spec: OutputSpec,
    ) -> Any:
        return self.get(spec.type).extract(text, name, spec)

    def extract_all(
        self, text: str, specs: dict[str, OutputSpec],
    ) -> dict[str, Any]:
        """Extract all outputs, trying bulk JSON first."""
        from ..utils.json_extract import extract_json

        # Optimistic: try single JSON containing all keys
        try:
            blob = extract_json(text)
            if isinstance(blob, dict):
                result = {
                    n: blob[n] for n in specs if n in blob
                }
                if len(result) == len(specs):
                    return result
        except ValueError:
            pass

        # Fallback: per-output extraction
        return {
            n: self.extract(text, n, spec)
            for n, spec in specs.items()
        }


def build_default_registry() -> ExtractorRegistry:
    """Create a registry with all built-in extractors."""
    from .json_extractor import JsonExtractor
    from .number_extractor import NumberExtractor
    from .text_extractor import TextExtractor
    from .file_extractor import FileExtractor

    reg = ExtractorRegistry()
    reg.register(OutputType.JSON, JsonExtractor())
    reg.register(OutputType.NUMBER, NumberExtractor())
    reg.register(OutputType.TEXT, TextExtractor())
    reg.register(OutputType.FILE, FileExtractor())
    return reg
