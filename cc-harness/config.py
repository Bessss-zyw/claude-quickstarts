"""Parse task file (YAML or Markdown with frontmatter) and build config."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import yaml


def _slugify(name: str) -> str:
    """Convert a task name to a safe tmux session name component."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return slug[:40] if slug else "default"


import logging as _logging
_log = _logging.getLogger(__name__)


def _resolve_api_key() -> str:
    """Resolve NVIDIA_API_KEY with fallback chain.

    Priority:
      1. Environment variable NVIDIA_API_KEY
      2. Already loaded via .env (dotenv sets env vars)
      3. Global ~/.nvcortex/secrets.env
    """
    # Check 1 & 2: environment variable (may have been set by dotenv already)
    key = os.environ.get("NVIDIA_API_KEY", "")
    if key:
        _log.info("NVIDIA_API_KEY loaded from environment variable")
        return key

    # Check 3: global secrets file
    global_secrets = os.path.expanduser("~/.nvcortex/secrets.env")
    if os.path.isfile(global_secrets):
        try:
            from dotenv import dotenv_values
            secrets = dotenv_values(global_secrets)
            key = secrets.get("NVIDIA_API_KEY", "")
            if key:
                _log.info("NVIDIA_API_KEY loaded from %s", global_secrets)
                return key
        except Exception as e:
            _log.warning("Failed to read %s: %s", global_secrets, e)

    _log.warning("NVIDIA_API_KEY not found in env, .env, or ~/.nvcortex/secrets.env")
    return ""

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class AgentDef:
    name: str
    role: str
    project_dir: str          # CC --project-dir (determines MCP/skills)
    allowlist: str | None = None  # CC --allowedTools pattern
    is_coordinator: bool = False


@dataclass
class HarnessConfig:
    task_file: str
    working_dir: str
    task_name: str = ""
    task_goal: str = ""
    task_context: str = ""
    deliverables: list[dict] = field(default_factory=list)
    agents: dict[str, AgentDef] = field(default_factory=dict)
    tmux_session: str = "harness"
    max_iterations: int = 20
    compact_threshold: int = 60
    send_cooldown: int = 30
    poll_interval: int = 15

    # Coordinator model config
    coordinator_model: str = "aws/anthropic/bedrock-claude-opus-4-6"
    coordinator_base_url: str = ""
    coordinator_api_key: str = ""

    @property
    def harness_path(self) -> str:
        return os.path.join(self.working_dir, ".harness")

    @property
    def plan_file(self) -> str:
        return os.path.join(self.harness_path, "plan.json")

    @property
    def history_file(self) -> str:
        return os.path.join(self.harness_path, "history.jsonl")

    @property
    def agents_log_dir(self) -> str:
        return os.path.join(self.harness_path, "agents")

    @property
    def token_usage_file(self) -> str:
        return os.path.join(self.harness_path, "token_usage.json")

    @property
    def output_path(self) -> str:
        return os.path.join(self.working_dir, "output")

    def ensure_dirs(self) -> None:
        for d in (self.harness_path, self.agents_log_dir, self.output_path):
            os.makedirs(d, exist_ok=True)

    def get_specialists(self) -> list[AgentDef]:
        """Return non-coordinator agents."""
        return [a for a in self.agents.values() if not a.is_coordinator]


def parse_task_file(path: str) -> dict:
    """Auto-detect format and parse task file."""
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    with open(path) as f:
        content = f.read()

    if ext in (".md", ".markdown"):
        m = _FRONTMATTER_RE.match(content)
        if not m:
            raise ValueError("Markdown task file must start with YAML frontmatter (--- ... ---).")
        data = yaml.safe_load(m.group(1)) or {}
        body = content[m.end():].strip()
        if "goal" not in data and body:
            data["goal"] = body
        elif body and "goal" in data:
            data["goal"] = data["goal"].rstrip() + "\n\n" + body
    else:
        data = yaml.safe_load(content)

    return data


def load_config(task_file: str, **overrides) -> HarnessConfig:
    """Parse task file and build HarnessConfig."""
    task_file = os.path.abspath(task_file)
    data = parse_task_file(task_file)

    env = data.get("environment", {})
    working_dir = env.get("working_dir", ".")
    if not os.path.isabs(working_dir):
        working_dir = os.path.join(os.path.dirname(task_file), working_dir)
    working_dir = os.path.abspath(working_dir)

    # Parse agents
    agents: dict[str, AgentDef] = {}
    for name, spec in data.get("agents", {}).items():
        agents[name] = AgentDef(
            name=name,
            role=spec.get("role", ""),
            project_dir=spec.get("project_dir", working_dir),
            allowlist=spec.get("allowlist"),
            is_coordinator=(name == "coordinator"),
        )

    # Harness-level settings (from "harness:" section in task file)
    harness_cfg = data.get("harness", {})

    # Coordinator model
    coord_cfg = data.get("coordinator", {})
    base_url = os.environ.get("NVIDIA_BASE_URL", "https://inference-api.nvidia.com/v1")
    api_key = _resolve_api_key()

    cfg = HarnessConfig(
        task_file=task_file,
        working_dir=working_dir,
        task_name=data.get("name", "unnamed"),
        task_goal=data.get("goal", ""),
        task_context=data.get("context", ""),
        deliverables=data.get("deliverables", []),
        agents=agents,
        tmux_session=harness_cfg.get("tmux_session", f"harness-{_slugify(data.get('name', 'default'))}"),
        max_iterations=overrides.get("max_iterations", harness_cfg.get("max_iterations", 20)),
        compact_threshold=harness_cfg.get("compact_threshold", 60),
        send_cooldown=harness_cfg.get("send_cooldown", 30),
        poll_interval=harness_cfg.get("poll_interval", 15),
        coordinator_model=coord_cfg.get("model", "aws/anthropic/bedrock-claude-opus-4-6"),
        coordinator_base_url=base_url,
        coordinator_api_key=api_key,
    )
    return cfg
