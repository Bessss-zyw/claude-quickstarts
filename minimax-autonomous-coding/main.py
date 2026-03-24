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
from tools import set_shell_preamble
from prompts import set_mode


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

  # Run inside a conda environment
  python main.py --project-dir ./my_project --shell-preamble "conda activate myenv"

  # Multiple preamble commands
  python main.py --project-dir ./my_project --shell-preamble "conda activate myenv" --shell-preamble "export LANG=en_US.UTF-8"

  # Use nvm
  python main.py --project-dir ./my_project --shell-preamble "nvm use 20"

  # NVIDIA internal Inference Hub (free for employees, approved for NVIDIA data)
  python main.py --provider nvidia --project-dir ./my_project
  python main.py --provider nvidia --model aws/anthropic/bedrock-claude-opus-4-6 --project-dir ./my_project

Environment Variables:
  MINIMAX_API_KEY    MiniMax API key (for --provider minimax)
  OPENAI_API_KEY     OpenAI API key (for --provider openai)
  DEEPSEEK_API_KEY   DeepSeek API key (for --provider deepseek)
  NVIDIA_API_KEY     NVIDIA Inference Hub key (for --provider nvidia, get from https://inference.nvidia.com/key-management)
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
        choices=["minimax", "openai", "deepseek", "nvidia", "custom"],
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
    parser.add_argument(
        "--mode",
        type=str,
        default="coding",
        choices=["coding", "perf"],
        help="Prompt mode: 'coding' for web/app development, 'perf' for GPU performance analysis (default: coding)",
    )
    parser.add_argument(
        "--shell-preamble",
        type=str,
        action="append",
        default=None,
        help=(
            "Shell command(s) to run before every bash execution. "
            "Can be specified multiple times. "
            "Example: --shell-preamble 'conda activate myenv'"
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Set prompt mode
    set_mode(args.mode)
    if args.mode != "coding":
        print(f"Mode: {args.mode}")

    # Configure shell preamble (conda activate, nvm use, etc.)
    if args.shell_preamble:
        set_shell_preamble(args.shell_preamble)
        print(f"Shell preamble: {' && '.join(args.shell_preamble)}")
        print()

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
