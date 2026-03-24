"""File-system tools: read, write, edit, list, search."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

from core.tools import register_tool
from core.security import is_path_allowed

if TYPE_CHECKING:
    from config import HarnessConfig

_CONFIG: HarnessConfig | None = None  # injected by ToolRegistry

_MAX_READ = 100 * 1024  # 100 KB


def _resolve(path: str) -> str:
    """Resolve relative paths against working_dir."""
    assert _CONFIG is not None
    if not os.path.isabs(path):
        return os.path.join(_CONFIG.working_dir, path)
    return path


def _check(path: str) -> str | None:
    """Return error string if path is disallowed, else None."""
    assert _CONFIG is not None
    real = _resolve(path)
    if not is_path_allowed(real, _CONFIG):
        return f"Error: path '{path}' is outside allowed directories."
    return None


# ── read_file ──────────────────────────────────────────────────────────────

def read_file(path: str) -> str:
    real = _resolve(path)
    err = _check(real)
    if err:
        return err
    if not os.path.isfile(real):
        return f"Error: file not found: {path}"
    size = os.path.getsize(real)
    with open(real) as f:
        content = f.read(_MAX_READ)
    if size > _MAX_READ:
        content += f"\n\n... [truncated — file is {size} bytes, showing first {_MAX_READ}]"
    return content


register_tool(
    name="read_file",
    description="Read the contents of a file.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Absolute or relative path to the file."},
        },
        "required": ["path"],
    },
    fn=read_file,
)

# ── write_file ─────────────────────────────────────────────────────────────

def write_file(path: str, content: str) -> str:
    real = _resolve(path)
    err = _check(real)
    if err:
        return err
    os.makedirs(os.path.dirname(real), exist_ok=True)
    with open(real, "w") as f:
        f.write(content)
    return f"Written {len(content)} bytes to {path}"


register_tool(
    name="write_file",
    description="Write content to a file (creates parent dirs).",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path."},
            "content": {"type": "string", "description": "Content to write."},
        },
        "required": ["path", "content"],
    },
    fn=write_file,
)

# ── edit_file ──────────────────────────────────────────────────────────────

def edit_file(path: str, old_text: str, new_text: str) -> str:
    real = _resolve(path)
    err = _check(real)
    if err:
        return err
    if not os.path.isfile(real):
        return f"Error: file not found: {path}"
    with open(real) as f:
        content = f.read()
    count = content.count(old_text)
    if count == 0:
        return "Error: old_text not found in file."
    if count > 1:
        return f"Error: old_text matches {count} times — must be unique."
    content = content.replace(old_text, new_text, 1)
    with open(real, "w") as f:
        f.write(content)
    return f"Edited {path}: replaced {len(old_text)} chars"


register_tool(
    name="edit_file",
    description="Replace a unique text fragment in a file.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path."},
            "old_text": {"type": "string", "description": "Text to find (must match exactly once)."},
            "new_text": {"type": "string", "description": "Replacement text."},
        },
        "required": ["path", "old_text", "new_text"],
    },
    fn=edit_file,
)

# ── list_files ─────────────────────────────────────────────────────────────

def list_files(path: str, recursive: bool = False) -> str:
    real = _resolve(path)
    err = _check(real)
    if err:
        return err
    if not os.path.isdir(real):
        return f"Error: not a directory: {path}"

    entries: list[str] = []
    if recursive:
        for root, dirs, files in os.walk(real):
            for name in dirs + files:
                entries.append(os.path.join(root, name))
    else:
        for name in sorted(os.listdir(real)):
            entries.append(os.path.join(real, name))

    if not entries:
        return "(empty directory)"
    return "\n".join(entries)


register_tool(
    name="list_files",
    description="List directory contents.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory path."},
            "recursive": {"type": "boolean", "description": "Recurse into subdirs (default false)."},
        },
        "required": ["path"],
    },
    fn=list_files,
)

# ── search_files ───────────────────────────────────────────────────────────

_MAX_SEARCH_RESULTS = 200

def search_files(path: str, pattern: str, file_glob: str = "*") -> str:
    real = _resolve(path)
    err = _check(real)
    if err:
        return err
    if not os.path.isdir(real):
        return f"Error: not a directory: {path}"

    import fnmatch
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Error: invalid regex: {e}"

    results: list[str] = []
    for root, _dirs, files in os.walk(real):
        for fname in files:
            if not fnmatch.fnmatch(fname, file_glob):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath) as f:
                    for lineno, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append(f"{fpath}:{lineno}: {line.rstrip()}")
                            if len(results) >= _MAX_SEARCH_RESULTS:
                                results.append(f"... [truncated at {_MAX_SEARCH_RESULTS} matches]")
                                return "\n".join(results)
            except (OSError, UnicodeDecodeError):
                continue

    if not results:
        return "No matches found."
    return "\n".join(results)


register_tool(
    name="search_files",
    description="Search file contents with regex.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to search."},
            "pattern": {"type": "string", "description": "Regex pattern to match."},
            "file_glob": {"type": "string", "description": "File name glob filter (default '*')."},
        },
        "required": ["path", "pattern"],
    },
    fn=search_files,
)
