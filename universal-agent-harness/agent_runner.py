"""Generic ReAct loop shared by coordinator and specialist agents."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from middleware.loop_detector import LoopDetector
from middleware.token_tracker import TokenTracker

if TYPE_CHECKING:
    from agent_registry import AgentDefinition
    from client import LLMClient
    from config import HarnessConfig
    from tools import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    agent_name: str
    final_text: str
    tool_calls_count: int
    token_usage: dict = field(default_factory=dict)
    error: str | None = None


class AgentRunner:
    """Run a single agent session (fresh context each time)."""

    def __init__(
        self,
        agent_def: "AgentDefinition",
        client: "LLMClient",
        tool_registry: "ToolRegistry",
        config: "HarnessConfig",
        token_tracker: TokenTracker | None = None,
    ) -> None:
        self.agent_def = agent_def
        self.client = client
        self.tool_registry = tool_registry
        self.config = config
        self.token_tracker = token_tracker

        # Build tool schemas for this agent
        # write_journal is implicit for every agent
        tool_names = list(agent_def.tool_names)
        if "write_journal" not in tool_names:
            tool_names.append("write_journal")
        # Coordinator also gets read_file implicitly
        if agent_def.is_coordinator and "read_file" not in tool_names:
            tool_names.append("read_file")
        self.tool_schemas = tool_registry.get_tool_schemas(tool_names)
        self._allowed_tools = set(tool_names)

    def run(self, system_prompt: str, user_message: str) -> AgentResult:
        """Execute one full agent session: LLM ↔ tools until final text."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        tool_calls_count = 0
        total_prompt = 0
        total_completion = 0
        loop_detector = LoopDetector()

        while True:
            # Call LLM
            try:
                response = self.client.chat(
                    messages=messages,
                    tools=self.tool_schemas if self.tool_schemas else None,
                )
            except Exception as e:
                logger.error("LLM call failed for %s: %s", self.agent_def.name, e)
                return AgentResult(
                    agent_name=self.agent_def.name,
                    final_text="",
                    tool_calls_count=tool_calls_count,
                    error=str(e),
                )

            # Track tokens
            usage = getattr(response, "usage", None)
            if usage:
                total_prompt += getattr(usage, "prompt_tokens", 0)
                total_completion += getattr(usage, "completion_tokens", 0)
                if self.token_tracker:
                    self.token_tracker.record(
                        self.agent_def.name,
                        getattr(usage, "prompt_tokens", 0),
                        getattr(usage, "completion_tokens", 0),
                    )

            choice = response.choices[0]
            msg = choice.message

            # If no tool calls, this is the final response
            if not msg.tool_calls:
                final_text = msg.content or ""
                return AgentResult(
                    agent_name=self.agent_def.name,
                    final_text=final_text,
                    tool_calls_count=tool_calls_count,
                    token_usage={"prompt_tokens": total_prompt, "completion_tokens": total_completion},
                )

            # Process tool calls
            # Add assistant message with tool calls to history
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                tool_calls_count += 1

                # Security: block coordinator from bash
                if self.agent_def.is_coordinator and tool_name == "bash":
                    result_str = "Error: coordinator cannot execute bash commands. Assign this work to a specialist."
                elif tool_name not in self._allowed_tools:
                    result_str = f"Error: tool '{tool_name}' is not in your allowed tool set."
                else:
                    result_str = self.tool_registry.execute_tool(
                        tool_name, args, self.agent_def.name
                    )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                })

                # Loop detection
                loop_detector.record(tool_name, args)
                warning = loop_detector.check()
                if warning == "FORCE_STOP":
                    logger.warning("Loop detected for %s — forcing stop.", self.agent_def.name)
                    return AgentResult(
                        agent_name=self.agent_def.name,
                        final_text="Session terminated: repeated loop detected.",
                        tool_calls_count=tool_calls_count,
                        token_usage={"prompt_tokens": total_prompt, "completion_tokens": total_completion},
                        error="loop_detected",
                    )
                elif warning:
                    # Inject warning into next user turn
                    messages.append({"role": "user", "content": warning})
