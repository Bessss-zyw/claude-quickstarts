"""
Prompt Loading Utilities
========================

Functions for loading prompt templates from the prompts directory.
Supports multiple modes (coding, perf) with different prompt sets.
"""

import shutil
from pathlib import Path


PROMPTS_DIR = Path(__file__).parent / "prompts"

# ---------------------------------------------------------------------------
# Mode → prompt file mapping
# ---------------------------------------------------------------------------
PROMPT_SETS = {
    "coding": {
        "system": "system_prompt",
        "initializer": "initializer_prompt",
        "coding": "coding_prompt",
    },
    "perf": {
        "system": "system_prompt_perf",
        "initializer": "initializer_prompt_perf",
        "coding": "coding_prompt_perf",
    },
}

# Module-level mode (set via set_mode())
_current_mode: str = "coding"


def set_mode(mode: str) -> None:
    """Set the prompt mode (coding, perf)."""
    global _current_mode
    if mode not in PROMPT_SETS:
        raise ValueError(f"Unknown mode '{mode}'. Choose from: {list(PROMPT_SETS.keys())}")
    _current_mode = mode


def get_mode() -> str:
    return _current_mode


def load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    return (PROMPTS_DIR / f"{name}.md").read_text()


def get_initializer_prompt() -> str:
    return load_prompt(PROMPT_SETS[_current_mode]["initializer"])


def get_coding_prompt() -> str:
    return load_prompt(PROMPT_SETS[_current_mode]["coding"])


def get_system_prompt() -> str:
    return load_prompt(PROMPT_SETS[_current_mode]["system"])


def copy_spec_to_project(project_dir: Path) -> None:
    """Copy the app spec file into the project directory."""
    src = PROMPTS_DIR / "app_spec.txt"
    dst = project_dir / "app_spec.txt"
    if not dst.exists() and src.exists():
        shutil.copy(src, dst)
        print("Copied app_spec.txt to project directory")
