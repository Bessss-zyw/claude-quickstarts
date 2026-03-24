"""Parse task file (YAML or Markdown) and build the agent registry."""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

import yaml

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class AgentDefinition:
    name: str
    role: str
    model_provider: str
    model_name: str
    tool_names: list[str] = field(default_factory=list)
    is_coordinator: bool = False


class AgentRegistry:
    """Load agent definitions from task.yaml."""

    def __init__(self, task_file_path: str) -> None:
        ext = os.path.splitext(task_file_path)[1].lower()
        with open(task_file_path) as f:
            content = f.read()

        if ext in (".md", ".markdown"):
            m = _FRONTMATTER_RE.match(content)
            if not m:
                raise ValueError("Markdown task file must have YAML frontmatter (--- ... ---).")
            data = yaml.safe_load(m.group(1)) or {}
        else:
            data = yaml.safe_load(content)

        agents_section = data.get("agents", {})
        if "coordinator" not in agents_section:
            raise ValueError("Task file must define an 'agents.coordinator' entry.")

        self._agents: dict[str, AgentDefinition] = {}
        for name, spec in agents_section.items():
            model = spec.get("model", {})
            tools = spec.get("tools", [])
            is_coord = (name == "coordinator")

            # Warn if coordinator has forbidden tools
            if is_coord:
                forbidden = {"bash", "write_file", "edit_file"}
                found = forbidden & set(tools)
                if found:
                    print(f"WARNING: coordinator should not have tools {found}. "
                          "They will be available but direct execution is discouraged.",
                          file=sys.stderr)

            agent_def = AgentDefinition(
                name=name,
                role=spec.get("role", ""),
                model_provider=model.get("provider", "openai"),
                model_name=model.get("name", "gpt-4o"),
                tool_names=tools,
                is_coordinator=is_coord,
            )
            self._agents[name] = agent_def

    def get_agent(self, name: str) -> AgentDefinition:
        return self._agents[name]

    def get_specialists(self) -> list[AgentDefinition]:
        return [a for a in self._agents.values() if not a.is_coordinator]

    def get_coordinator(self) -> AgentDefinition:
        return self._agents["coordinator"]

    def list_agent_names(self) -> list[str]:
        return list(self._agents.keys())
