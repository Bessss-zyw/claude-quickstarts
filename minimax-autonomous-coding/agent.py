"""
Agent Session Logic (ReAct Loop)
================================

Core agent interaction: sends messages to the LLM, parses tool calls,
executes tools, feeds results back, and loops until the model stops.

This replaces the Claude Agent SDK with a plain OpenAI-compatible
function-calling loop that works with MiniMax M2.7 or any provider.
"""

import json
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI
from openai.types.chat import ChatCompletionMessage

from client import create_client
from tools import TOOL_DEFINITIONS, execute_tool
from progress import print_session_header, print_progress_summary
from prompts import (
    get_initializer_prompt,
    get_coding_prompt,
    get_system_prompt,
    copy_spec_to_project,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AUTO_CONTINUE_DELAY_SECONDS = 3
MAX_TOOL_ROUNDS = 80          # max tool-call rounds per session
MAX_TOKENS_PER_TURN = 16384   # max tokens per LLM response


# ---------------------------------------------------------------------------
# Single ReAct session
# ---------------------------------------------------------------------------

def run_agent_session(
    client: OpenAI,
    model: str,
    user_prompt: str,
    system_prompt: str,
    project_dir: Path,
) -> tuple[str, str]:
    """
    Run a single agent session: a ReAct loop of LLM + tool execution.

    Args:
        client: OpenAI-compatible client
        model: Model name string
        user_prompt: The initial user prompt (initializer or coding)
        system_prompt: System prompt for the agent
        project_dir: Project working directory

    Returns:
        (status, final_text) where status is "continue" or "error"
    """
    messages: list[dict] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    final_text = ""

    for round_idx in range(MAX_TOOL_ROUNDS):
        print(f"  [round {round_idx + 1}/{MAX_TOOL_ROUNDS}]", end=" ", flush=True)

        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                max_tokens=MAX_TOKENS_PER_TURN,
                temperature=0.3,
            )
        except Exception as e:
            error_str = str(e)
            print(f"\n[API ERROR] {e}")

            # Detect fatal errors that should not be retried
            fatal_keywords = [
                "insufficient_balance", "insufficient balance",
                "invalid api key", "authorized_error", "authentication",
                "account_suspended", "quota_exceeded",
            ]
            if any(kw in error_str.lower() for kw in fatal_keywords):
                return "fatal", error_str

            return "error", error_str

        choice = response.choices[0]
        msg: ChatCompletionMessage = choice.message

        # ---------- case 1: model wants to call tools ----------
        if msg.tool_calls:
            # Append the assistant message (with tool_calls) to history
            messages.append(msg.model_dump())

            print(f"calling {len(msg.tool_calls)} tool(s)")

            for tc in msg.tool_calls:
                func_name = tc.function.name
                try:
                    func_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    func_args = {}

                # Print tool call info
                args_preview = json.dumps(func_args, ensure_ascii=False)
                if len(args_preview) > 200:
                    args_preview = args_preview[:200] + "..."
                print(f"    [Tool: {func_name}] {args_preview}")

                # Execute tool
                result = execute_tool(func_name, func_args, project_dir)

                # Print brief result
                result_preview = result[:300].replace("\n", "\\n")
                if len(result) > 300:
                    result_preview += "..."
                print(f"    -> {result_preview}")

                # Append tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            continue  # next round

        # ---------- case 2: model returns text (no tool calls) ----------
        text = msg.content or ""
        final_text = text
        if text:
            print("text response")
            print()
            print(text)
        else:
            print("(empty response)")

        # Check finish reason
        if choice.finish_reason == "stop":
            break
        # Some providers return "length" if max_tokens hit
        if choice.finish_reason == "length":
            print("\n[WARNING] Response truncated (max_tokens reached)")
            break
        break  # default: stop after a non-tool-call response

    print("\n" + "-" * 70)
    return "continue", final_text


# ---------------------------------------------------------------------------
# Main autonomous loop
# ---------------------------------------------------------------------------

def run_autonomous_agent(
    project_dir: Path,
    provider: str = "minimax",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_iterations: Optional[int] = None,
) -> None:
    """
    Run the autonomous agent loop: initializer -> coding -> coding -> ...

    Each iteration is a fresh context window (new message list).
    State is persisted via the filesystem (feature_list.json, git, progress.txt).
    """
    print("\n" + "=" * 70)
    print("  AUTONOMOUS CODING AGENT (MiniMax Harness)")
    print("=" * 70)

    # Create client
    client, resolved_model = create_client(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )

    print(f"  Provider:  {provider}")
    print(f"  Model:     {resolved_model}")
    print(f"  Project:   {project_dir}")
    if max_iterations:
        print(f"  Max iter:  {max_iterations}")
    else:
        print("  Max iter:  Unlimited")
    print()

    # Create project directory
    project_dir.mkdir(parents=True, exist_ok=True)

    # Determine if first run
    tests_file = project_dir / "feature_list.json"
    is_first_run = not tests_file.exists()

    if is_first_run:
        print("Fresh start - will use initializer agent")
        copy_spec_to_project(project_dir)
    else:
        print("Continuing existing project")
        print_progress_summary(project_dir)

    # Load system prompt
    system_prompt = get_system_prompt()

    # Main loop
    iteration = 0
    while True:
        iteration += 1
        if max_iterations and iteration > max_iterations:
            print(f"\nReached max iterations ({max_iterations})")
            print("To continue, run the script again without --max-iterations")
            break

        print_session_header(iteration, is_first_run and iteration == 1)

        # Choose prompt
        if is_first_run and iteration == 1:
            user_prompt = get_initializer_prompt()
        else:
            user_prompt = get_coding_prompt()

        # Run one session (fresh context window)
        status, response = run_agent_session(
            client=client,
            model=resolved_model,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            project_dir=project_dir,
        )

        # Print progress
        print_progress_summary(project_dir)

        if status == "fatal":
            print("\n" + "!" * 70)
            print("  FATAL ERROR - cannot continue")
            print("!" * 70)
            print(f"\n  {response}")
            print("\n  Please fix the issue and run again.")
            break

        if status == "error":
            print("\nSession encountered an error. Retrying...")

        print(f"\nAuto-continuing in {AUTO_CONTINUE_DELAY_SECONDS}s...")
        time.sleep(AUTO_CONTINUE_DELAY_SECONDS)

    # Final summary
    print("\n" + "=" * 70)
    print("  SESSION COMPLETE")
    print("=" * 70)
    print(f"\nProject directory: {project_dir}")
    print_progress_summary(project_dir)

    print("\n" + "-" * 70)
    print("  TO RUN THE GENERATED APPLICATION:")
    print("-" * 70)
    print(f"\n  cd {project_dir.resolve()}")
    print("  ./init.sh           # Run the setup script")
    print("  # Or manually:")
    print("  npm install && npm run dev")
    print("\n  Then open http://localhost:3000 (or check init.sh for the URL)")
    print("-" * 70)
    print("\nDone!")
