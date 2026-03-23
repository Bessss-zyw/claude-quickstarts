"""
Bash Command Security
=====================

Allowlist-based security for bash commands executed by the agent.
Adapted from the Anthropic autonomous-coding demo.
"""

import os
import re
import shlex


# Allowed base commands
ALLOWED_COMMANDS = {
    # File inspection
    "ls", "cat", "head", "tail", "wc", "grep", "find", "diff",
    # File operations
    "cp", "mv", "mkdir", "chmod", "touch", "rm",
    # Directory
    "pwd", "cd",
    # Node.js development
    "npm", "npx", "node",
    # Python development
    "python", "python3", "pip", "pip3",
    # Version control
    "git",
    # Process management
    "ps", "lsof", "sleep", "pkill", "kill",
    # Utility
    "echo", "printf", "sort", "uniq", "tr", "cut", "awk", "sed",
    "tee", "xargs", "which", "env", "true", "false", "test",
    # Script execution
    "bash", "sh",
    # Archive
    "tar", "zip", "unzip",
    # Network (for dev servers)
    "curl", "wget",
}

# Commands that need extra validation
RESTRICTED_COMMANDS = {"rm", "pkill", "kill"}


def _extract_commands(command_string: str) -> list[str]:
    """Extract base command names from a shell command string."""
    commands = []
    segments = re.split(r'(?<!["\'])\s*;\s*(?!["\'])', command_string)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return []  # fail-safe: block unparseable commands
        if not tokens:
            continue

        expect_command = True
        for token in tokens:
            if token in ("|", "||", "&&", "&"):
                expect_command = True
                continue
            if token in ("if", "then", "else", "elif", "fi", "for", "while",
                         "until", "do", "done", "case", "esac", "in", "!",
                         "{", "}"):
                continue
            if token.startswith("-"):
                continue
            if "=" in token and not token.startswith("="):
                continue
            if expect_command:
                cmd = os.path.basename(token)
                commands.append(cmd)
                expect_command = False
    return commands


def _validate_rm(command_string: str) -> tuple[bool, str]:
    """Only allow rm on obviously safe targets (no -rf /)."""
    # Block dangerous patterns
    if re.search(r"rm\s+(-\w*r\w*f|-\w*f\w*r)\s+/\s*$", command_string):
        return False, "rm -rf / is not allowed"
    if re.search(r"rm\s+(-\w*r\w*f|-\w*f\w*r)\s+~\s*$", command_string):
        return False, "rm -rf ~ is not allowed"
    return True, ""


def _validate_pkill(command_string: str) -> tuple[bool, str]:
    allowed_targets = {"node", "npm", "npx", "python", "python3", "vite", "next"}
    try:
        tokens = shlex.split(command_string)
    except ValueError:
        return False, "Could not parse pkill command"
    args = [t for t in tokens[1:] if not t.startswith("-")]
    if not args:
        return False, "pkill requires a process name"
    target = args[-1].split()[0] if " " in args[-1] else args[-1]
    if target in allowed_targets:
        return True, ""
    return False, f"pkill only allowed for: {allowed_targets}"


def validate_bash_command(command: str) -> tuple[bool, str]:
    """
    Validate a bash command against the allowlist.

    Returns:
        (is_allowed, reason_if_blocked)
    """
    commands = _extract_commands(command)
    if not commands:
        return False, f"Could not parse command for security validation: {command}"

    for cmd in commands:
        # Allow script execution (./something.sh)
        if cmd.endswith(".sh"):
            continue
        if cmd not in ALLOWED_COMMANDS:
            return False, f"Command '{cmd}' is not in the allowed commands list"
        # Extra validation for restricted commands
        if cmd in RESTRICTED_COMMANDS:
            if cmd == "rm":
                ok, reason = _validate_rm(command)
                if not ok:
                    return False, reason
            elif cmd in ("pkill", "kill"):
                ok, reason = _validate_pkill(command)
                if not ok:
                    return False, reason

    return True, ""
