"""
Tool Definitions and Execution
==============================

Defines the tools available to the MiniMax agent and handles their execution.
Tools are defined in OpenAI function-calling format.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from security import validate_bash_command


# ---------------------------------------------------------------------------
# Tool schema (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Execute a bash command in the project directory. "
                "Use for running scripts, git operations, npm commands, "
                "listing files, inspecting output, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The bash command to execute",
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read the contents of a file. Returns the full text content. "
                "Use this to inspect source code, configuration, JSON files, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file (from project root)",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write content to a file. Creates the file if it doesn't exist, "
                "overwrites if it does. Parent directories are created automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file (from project root)",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Perform a search-and-replace edit in a file. "
                "Finds the first occurrence of `old_string` and replaces it with `new_string`. "
                "Use this for surgical edits instead of rewriting entire files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file (from project root)",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "The exact text to search for",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "The text to replace it with",
                    },
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": (
                "List files and directories at a given path. "
                "Returns a newline-separated list. Use to explore project structure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative directory path (from project root). Use '.' for project root.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Search for a pattern across files using grep. "
                "Returns matching lines with file paths and line numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "The regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory or file to search in (default: '.')",
                    },
                    "include": {
                        "type": "string",
                        "description": "Glob pattern to filter files (e.g. '*.py', '*.js')",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

# Maximum output length returned to the model (characters)
MAX_OUTPUT_LENGTH = 30000
BASH_TIMEOUT_SECONDS = 120


def _truncate(text: str, limit: int = MAX_OUTPUT_LENGTH) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return (
        text[:half]
        + f"\n\n... [truncated {len(text) - limit} characters] ...\n\n"
        + text[-half:]
    )


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    project_dir: Path,
) -> str:
    """
    Execute a tool call and return the result as a string.

    Args:
        name: Tool function name
        arguments: Parsed arguments dict
        project_dir: The project working directory

    Returns:
        String result to feed back to the model
    """
    try:
        if name == "bash":
            return _exec_bash(arguments, project_dir)
        elif name == "read_file":
            return _exec_read_file(arguments, project_dir)
        elif name == "write_file":
            return _exec_write_file(arguments, project_dir)
        elif name == "edit_file":
            return _exec_edit_file(arguments, project_dir)
        elif name == "list_files":
            return _exec_list_files(arguments, project_dir)
        elif name == "search_files":
            return _exec_search_files(arguments, project_dir)
        else:
            return f"[ERROR] Unknown tool: {name}"
    except Exception as e:
        return f"[ERROR] Tool '{name}' failed: {e}"


# ---- individual tool implementations ----


def _exec_bash(args: dict, project_dir: Path) -> str:
    command = args.get("command", "")
    if not command:
        return "[ERROR] No command provided"

    # Security validation
    allowed, reason = validate_bash_command(command)
    if not allowed:
        return f"[BLOCKED] {reason}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=BASH_TIMEOUT_SECONDS,
            cwd=str(project_dir.resolve()),
            env={**os.environ, "HOME": os.environ.get("HOME", "/root")},
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n" if output else "") + result.stderr
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return _truncate(output) if output else "(no output)"
    except subprocess.TimeoutExpired:
        return f"[ERROR] Command timed out after {BASH_TIMEOUT_SECONDS}s"


def _exec_read_file(args: dict, project_dir: Path) -> str:
    rel_path = args.get("path", "")
    if not rel_path:
        return "[ERROR] No path provided"
    target = (project_dir / rel_path).resolve()
    # Security: must stay within project dir
    if not str(target).startswith(str(project_dir.resolve())):
        return "[BLOCKED] Cannot read files outside the project directory"
    if not target.exists():
        return f"[ERROR] File not found: {rel_path}"
    if not target.is_file():
        return f"[ERROR] Not a file: {rel_path}"
    content = target.read_text(errors="replace")
    return _truncate(content)


def _exec_write_file(args: dict, project_dir: Path) -> str:
    rel_path = args.get("path", "")
    content = args.get("content", "")
    if not rel_path:
        return "[ERROR] No path provided"
    target = (project_dir / rel_path).resolve()
    if not str(target).startswith(str(project_dir.resolve())):
        return "[BLOCKED] Cannot write files outside the project directory"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"OK - wrote {len(content)} characters to {rel_path}"


def _exec_edit_file(args: dict, project_dir: Path) -> str:
    rel_path = args.get("path", "")
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    if not rel_path:
        return "[ERROR] No path provided"
    if not old_string:
        return "[ERROR] old_string is empty"
    target = (project_dir / rel_path).resolve()
    if not str(target).startswith(str(project_dir.resolve())):
        return "[BLOCKED] Cannot edit files outside the project directory"
    if not target.exists():
        return f"[ERROR] File not found: {rel_path}"
    content = target.read_text(errors="replace")
    count = content.count(old_string)
    if count == 0:
        return f"[ERROR] old_string not found in {rel_path}"
    if count > 1:
        return f"[ERROR] old_string found {count} times in {rel_path} - provide more context to make it unique"
    new_content = content.replace(old_string, new_string, 1)
    target.write_text(new_content)
    return f"OK - replaced 1 occurrence in {rel_path}"


def _exec_list_files(args: dict, project_dir: Path) -> str:
    rel_path = args.get("path", ".")
    target = (project_dir / rel_path).resolve()
    if not str(target).startswith(str(project_dir.resolve())):
        return "[BLOCKED] Cannot list files outside the project directory"
    if not target.exists():
        return f"[ERROR] Path not found: {rel_path}"
    if not target.is_dir():
        return f"[ERROR] Not a directory: {rel_path}"
    entries = sorted(target.iterdir())
    lines = []
    for entry in entries:
        name = entry.name
        if entry.is_dir():
            name += "/"
        lines.append(name)
    return "\n".join(lines) if lines else "(empty directory)"


def _exec_search_files(args: dict, project_dir: Path) -> str:
    pattern = args.get("pattern", "")
    rel_path = args.get("path", ".")
    include = args.get("include", "")
    if not pattern:
        return "[ERROR] No pattern provided"
    target = (project_dir / rel_path).resolve()
    if not str(target).startswith(str(project_dir.resolve())):
        return "[BLOCKED] Cannot search files outside the project directory"

    cmd = ["grep", "-rn", "--color=never"]
    if include:
        cmd.extend(["--include", include])
    cmd.extend([pattern, str(target)])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
        )
        output = result.stdout
        # Make paths relative to project dir for readability
        prefix = str(project_dir.resolve()) + "/"
        output = output.replace(prefix, "")
        return _truncate(output) if output else "(no matches)"
    except subprocess.TimeoutExpired:
        return "[ERROR] Search timed out"
