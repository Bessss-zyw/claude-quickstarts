"""
Prompt Loading Utilities
========================

Functions for loading prompt templates from the prompts directory.
"""

import shutil
from pathlib import Path


PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    """Load a prompt template from the prompts directory."""
    return (PROMPTS_DIR / f"{name}.md").read_text()


def get_initializer_prompt() -> str:
    return load_prompt("initializer_prompt")


def get_coding_prompt() -> str:
    return load_prompt("coding_prompt")


def get_system_prompt() -> str:
    return load_prompt("system_prompt")


def copy_spec_to_project(project_dir: Path) -> None:
    """Copy the app spec file into the project directory."""
    src = PROMPTS_DIR / "app_spec.txt"
    dst = project_dir / "app_spec.txt"
    if not dst.exists() and src.exists():
        shutil.copy(src, dst)
        print("Copied app_spec.txt to project directory")
