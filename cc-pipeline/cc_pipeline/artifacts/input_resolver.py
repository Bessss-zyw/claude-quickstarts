"""Input resolution for stage inputs (glob, file, literal)."""

from __future__ import annotations

import glob as glob_mod
from pathlib import Path
from typing import Any

from ..config.models import InputSpec
from ..constants.enums import InputType


class InputResolver:
    """Resolves stage InputSpec into concrete values."""

    def resolve(
        self, spec: InputSpec, working_dir: str,
    ) -> Any:
        if spec.type == InputType.LITERAL:
            return spec.value or ""
        if spec.type == InputType.GLOB:
            return self._resolve_glob(spec.pattern or "", working_dir)
        if spec.type == InputType.FILE:
            return self._resolve_file(spec.path or "", working_dir)
        return spec.value or ""

    def resolve_all(
        self, inputs: dict[str, InputSpec], working_dir: str,
    ) -> dict[str, Any]:
        return {
            name: self.resolve(spec, working_dir)
            for name, spec in inputs.items()
        }

    @staticmethod
    def _resolve_glob(pattern: str, working_dir: str) -> str:
        base = Path(working_dir)
        matches = sorted(
            glob_mod.glob(str(base / pattern), recursive=True),
        )
        if not matches:
            return f"(no files matched: {pattern})"
        result = []
        for m in matches:
            try:
                result.append(str(Path(m).relative_to(base)))
            except ValueError:
                result.append(m)
        return "\n".join(result)

    @staticmethod
    def _resolve_file(path: str, working_dir: str) -> str:
        file_path = Path(working_dir) / path
        if not file_path.exists():
            raise FileNotFoundError(
                f"Input file not found: {file_path} "
                f"(path={path!r}, working_dir={working_dir!r})"
            )
        return file_path.read_text(encoding="utf-8")
