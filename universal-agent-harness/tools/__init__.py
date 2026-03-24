"""Tool registry — manages tool schemas and dispatches execution."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from config import HarnessConfig
    from message_bus import MessageBus

# Each tool module will register itself here via register_tool()
_TOOL_DEFS: dict[str, dict] = {}   # name -> {"schema": {...}, "fn": callable}


def register_tool(name: str, description: str, parameters: dict, fn: Callable) -> None:
    """Register a tool globally (called at import time by each tool module)."""
    _TOOL_DEFS[name] = {
        "schema": {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": parameters,
            },
        },
        "fn": fn,
    }


class ToolRegistry:
    """Resolve agent tool whitelists to schemas and dispatch execution."""

    def __init__(self, config: "HarnessConfig", message_bus: "MessageBus") -> None:
        self.config = config
        self.message_bus = message_bus

        # Import tool modules so they self-register
        from tools import bash, filesystem, planning, messaging, journal  # noqa: F401

        # Inject runtime dependencies that tools need
        from tools import bash as _bash_mod
        from tools import filesystem as _fs_mod
        from tools import planning as _plan_mod
        from tools import messaging as _msg_mod
        from tools import journal as _journal_mod

        _bash_mod._CONFIG = config
        _fs_mod._CONFIG = config
        _plan_mod._CONFIG = config
        _msg_mod._CONFIG = config
        _msg_mod._MESSAGE_BUS = message_bus
        _journal_mod._CONFIG = config

    def get_tool_schemas(self, tool_names: list[str]) -> list[dict]:
        """Return OpenAI function-calling schemas for the given tool names."""
        schemas: list[dict] = []
        for name in tool_names:
            if name in _TOOL_DEFS:
                schemas.append(_TOOL_DEFS[name]["schema"])
        return schemas

    def execute_tool(self, tool_name: str, arguments: dict, agent_name: str) -> str:
        """Execute a tool and return the result string."""
        if tool_name not in _TOOL_DEFS:
            return f"Error: unknown tool '{tool_name}'"

        # Inject agent_name for journal
        if tool_name == "write_journal":
            arguments["_agent_name"] = agent_name

        try:
            result = _TOOL_DEFS[tool_name]["fn"](**arguments)
            return str(result)
        except Exception as e:
            return f"Error executing {tool_name}: {e}"
