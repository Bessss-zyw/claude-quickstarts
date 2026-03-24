"""Track per-agent token consumption."""

from __future__ import annotations

import json
import os


class TokenTracker:
    """Accumulate prompt/completion tokens per agent and persist to disk."""

    def __init__(self, token_file: str) -> None:
        self._file = token_file
        self._data: dict[str, dict[str, int]] = {}
        if os.path.exists(token_file):
            with open(token_file) as f:
                self._data = json.load(f)

    def record(self, agent_name: str, prompt_tokens: int, completion_tokens: int) -> None:
        if agent_name not in self._data:
            self._data[agent_name] = {"prompt_tokens": 0, "completion_tokens": 0}
        self._data[agent_name]["prompt_tokens"] += prompt_tokens
        self._data[agent_name]["completion_tokens"] += completion_tokens

    def save(self) -> None:
        # Compute total
        total_p = sum(v["prompt_tokens"] for k, v in self._data.items() if k != "total")
        total_c = sum(v["completion_tokens"] for k, v in self._data.items() if k != "total")
        self._data["total"] = {"prompt_tokens": total_p, "completion_tokens": total_c}
        os.makedirs(os.path.dirname(self._file), exist_ok=True)
        with open(self._file, "w") as f:
            json.dump(self._data, f, indent=2)

    def summary(self) -> str:
        lines: list[str] = []
        for name, usage in self._data.items():
            lines.append(f"  {name}: prompt={usage['prompt_tokens']}, completion={usage['completion_tokens']}")
        return "\n".join(lines) if lines else "(no usage recorded)"
