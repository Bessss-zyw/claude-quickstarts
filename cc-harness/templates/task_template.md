---
# ── CC-Native Multi-Agent Harness — Task Template ───────────────────────
#
# Copy this file, fill in your task details, then run:
#   python supervisor.py --task your_task.md
#
# This template defines a 5-agent team. Remove or add agents as needed —
# only "coordinator" is required.
#
# Key difference from universal-agent-harness: each specialist is a full
# Claude Code instance with access to MCP servers, skills, and all CC
# tools. The coordinator is a pure Opus API caller (no CC instance).

name: "TODO: your-task-name"

context: |
  TODO: Optional background info. Remove if not needed.

deliverables:
  - path: report.md
    description: "Final analysis report"

environment:
  working_dir: /tmp/cc-harness-workdir     # runtime state (.harness/, output/)

# Optional harness tuning
harness:
  tmux_session: harness                     # tmux session name
  compact_threshold: 60                     # send /compact when context >= N%
  poll_interval: 3                          # seconds between state polls
  send_cooldown: 30                         # min seconds between sends to same agent
  skip_permissions: false                   # true = launch CC with --dangerouslySkipPermissions
                                            # (skips all permission prompts — faster but no safety checks)

# Optional coordinator settings
# coordinator:
#   api_type: anthropic                      # "openai" (default, NVIDIA endpoint) or "anthropic" (native, with prompt caching)
#   model: claude-opus-4-6                   # model name (depends on api_type)

# ── Agent team ──────────────────────────────────────────────────────────
#
# • coordinator: pure Opus API — plans, assigns, reviews (NOT a CC instance)
# • specialists: each is a full Claude Code instance in its own tmux window
#   - project_dir: CC inherits .claude/ config from this directory
#     (MCP servers, skills, rules all come from project_dir/.claude/)
#   - allowlist: optional CC --allowedTools filter (comma-separated)

agents:
  coordinator:
    role: >
      You are the project coordinator. Break the goal into steps, assign
      each to the best specialist, monitor progress, and verify deliverables.
    project_dir: .                          # not used (coordinator is API-only)

  coder:
    role: >
      You are a software engineer. Write, debug, and refactor code.
      Save intermediate files to workspace/coder/ and deliverables to output/.
    project_dir: /path/to/your/project      # CC works here, inherits MCP/skills
    allowlist: "Bash,Read,Write,Edit,Glob,Grep"
    # system_prompt: |                       # optional: override auto-generated prompt
    #   Custom instructions injected into CC via --system-prompt.
    #   If omitted, auto-generated from role + task goal + context.

  tester:
    role: >
      You are a QA engineer. Run tests, verify correctness and edge cases,
      report clear pass/fail results with reproduction steps for failures.
    project_dir: /path/to/your/project
    allowlist: "Bash,Read,Glob,Grep"

  profiler:
    role: >
      You are a performance engineer. Profile code, collect benchmarks,
      identify bottlenecks, and provide optimization recommendations
      with supporting data.
    project_dir: /path/to/your/project
    allowlist: "Bash,Read,Write,Glob,Grep"

  code_manager:
    role: >
      You are a code review and release manager. Review code quality,
      manage MR submissions, and ensure the codebase stays clean and
      well-documented.
    project_dir: /path/to/your/project
    allowlist: "Bash,Read,Write,Edit,Glob,Grep"
---

# Task Goal

TODO: Write your task description here in natural Markdown.

Everything below the `---` frontmatter becomes the **goal** field that the
coordinator uses for planning.

## Background

- Explain the context and motivation
- Reference relevant files or documentation

## Requirements

1. First requirement
2. Second requirement

## Success Criteria

- What "done" looks like
- Specific metrics or acceptance tests
