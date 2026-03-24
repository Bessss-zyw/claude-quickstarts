"""Global configuration for the Universal Agent Harness."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class HarnessConfig:
    """Resolved configuration for a single harness run."""

    task_file: str                                # Absolute path to task.yaml
    working_dir: str                              # Resolved absolute path
    max_iterations: int = 20
    journal_compress_threshold: int = 3000        # chars before compression
    harness_dir: str = ".harness"
    workspace_dir: str = "workspace"
    output_dir: str = "output"

    # Populated from task.yaml
    task_name: str = ""
    task_goal: str = ""
    task_context: str = ""
    deliverables: list[dict] = field(default_factory=list)
    shell_preamble: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)

    # ── Derived paths ──────────────────────────────────────────────────

    @property
    def harness_path(self) -> str:
        return os.path.join(self.working_dir, self.harness_dir)

    @property
    def workspace_path(self) -> str:
        return os.path.join(self.working_dir, self.workspace_dir)

    @property
    def output_path(self) -> str:
        return os.path.join(self.working_dir, self.output_dir)

    @property
    def plan_file(self) -> str:
        return os.path.join(self.harness_path, "plan.json")

    @property
    def journals_dir(self) -> str:
        return os.path.join(self.harness_path, "journals")

    @property
    def messages_dir(self) -> str:
        return os.path.join(self.harness_path, "messages")

    @property
    def journal_archive_dir(self) -> str:
        return os.path.join(self.harness_path, "journal_archive")

    @property
    def token_usage_file(self) -> str:
        return os.path.join(self.harness_path, "token_usage.json")

    @property
    def session_log_file(self) -> str:
        return os.path.join(self.harness_path, "session_log.jsonl")

    def ensure_dirs(self) -> None:
        """Create all runtime directories if they don't exist."""
        for d in (
            self.harness_path,
            self.journals_dir,
            self.messages_dir,
            self.journal_archive_dir,
            self.workspace_path,
            self.output_path,
        ):
            os.makedirs(d, exist_ok=True)
