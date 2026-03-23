#!/usr/bin/env python3
"""
Autonomous Coding Agent - MiniMax Harness
==========================================

A model-agnostic harness for long-running autonomous coding tasks.
Implements the two-agent pattern (initializer + coding agent) from
Anthropic's "Effective Harnesses for Long-Running Agents" guide,
but uses the OpenAI-compatible API instead of the Claude Agent SDK.

Works with MiniMax M2.7, OpenAI, DeepSeek, or any OpenAI-compatible API.

Usage:
    # MiniMax M2.7 (default)
    export MINIMAX_API_KEY='your-key'
    python main.py --project-dir ./my_project

    # OpenAI GPT-4o
    export OPENAI_API_KEY='your-key'
    python main.py --provider openai --project-dir ./my_project

    # DeepSeek
    export DEEPSEEK_API_KEY='your-key'
    python main.py --provider deepseek --project-dir ./my_project

    # Any OpenAI-compatible API
    export LLM_API_KEY='your-key'
    python main.py --provider custom --base-url https://api.example.com/v1 --model my-model --project-dir ./my_project
"""

import argparse
from pathlib import Path

from agent import run_autonomous_agent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Autonomous Coding Agent - MiniMax Harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # MiniMax M2.7 (default)
  python main.py --project-dir ./my_project

  # OpenAI
  python main.py --provider openai --model gpt-4o --project-dir ./my_project

  # Limit iterations for testing
  python main.py --project-dir ./my_project --max-iterations 3

  # Resume an existing project
  python main.py --project-dir ./my_project

  # Custom provider
  python main.py --provider custom --base-url https://api.example.com/v1 --model my-model --project-dir ./my_project

Environment Variables:
  MINIMAX_API_KEY    MiniMax API key (for --provider minimax)
  OPENAI_API_KEY     OpenAI API key (for --provider openai)
  DEEPSEEK_API_KEY   DeepSeek API key (for --provider deepseek)
  LLM_API_KEY        Generic API key (for --provider custom)
        """,
    )

    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path("./autonomous_demo_project"),
        help="Directory for the project (default: ./autonomous_demo_project)",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default="minimax",
        choices=["minimax", "openai", "deepseek", "custom"],
        help="LLM provider (default: minimax)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model name override (default: provider-specific)",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        default=None,
        help="API key override (default: from environment variable)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="API base URL override (required for --provider custom)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum number of agent iterations (default: unlimited)",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Auto-prepend generations/ for relative paths
    project_dir = args.project_dir
    if not project_dir.is_absolute() and not str(project_dir).startswith("generations/"):
        project_dir = Path("generations") / project_dir

    try:
        run_autonomous_agent(
            project_dir=project_dir,
            provider=args.provider,
            model=args.model,
            api_key=args.api_key,
            base_url=args.base_url,
            max_iterations=args.max_iterations,
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        print("To resume, run the same command again")
    except Exception as e:
        print(f"\nFatal error: {e}")
        raise


if __name__ == "__main__":
    main()
