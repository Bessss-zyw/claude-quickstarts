---
# ── Universal Agent Harness — Task Template (Markdown) ──────────────────
#
# Copy this file and fill in your task details.
# Run:  python main.py --task your_task.md
#
# The YAML frontmatter (between ---) contains config.
# Everything below the frontmatter becomes the task goal.
#
# This template defines a full 5-agent team. Remove or add agents as
# needed — only "coordinator" is required.

name: "TODO: your-task-name"

context: |
  TODO: Optional background info. Remove if not needed.

deliverables:
  - path: report.md
    description: "Final analysis report"

environment:
  working_dir: /tmp/harness-workdir
  allowed_paths: []
  shell_preamble: []

# Agent team — each agent can use a different model/provider.
# write_journal is auto-injected for every agent.
# Coordinator CANNOT use bash (blocked at runtime).

agents:
  coordinator:
    role: >
      You are the project coordinator. Break the overall goal into concrete
      steps, assign each step to the best-suited specialist, track progress
      via the plan, and verify deliverables before declaring the task done.
    model:
      provider: nvidia
      name: aws/anthropic/bedrock-claude-opus-4-6
    tools:
      - get_plan
      - create_plan
      - update_plan
      - assign_task
      - check_result
      - check_all_results
      - read_file

  coder:
    role: >
      You are a software engineer. Write, debug, and refactor code as
      instructed. Save intermediate work to workspace/coder/ and final
      deliverables to output/.
    model:
      provider: nvidia
      name: aws/anthropic/bedrock-claude-opus-4-6
    tools: [bash, read_file, write_file, edit_file, list_files, search_files]

  tester:
    role: >
      You are a QA engineer. Run tests in various environments, verify
      correctness and edge cases, and report clear pass/fail results with
      reproduction steps for any failures.
    model:
      provider: nvidia
      name: aws/anthropic/bedrock-claude-sonnet-4-6
    tools: [bash, read_file, write_file, list_files, search_files]

  profiler:
    role: >
      You are a performance engineer. Profile code, collect benchmarks,
      identify bottlenecks, and provide actionable optimization
      recommendations with supporting data.
    model:
      provider: nvidia
      name: aws/anthropic/bedrock-claude-opus-4-6
    tools: [bash, read_file, write_file, list_files, search_files]

  code_manager:
    role: >
      You are a code review and release manager. Review code for quality,
      consistency, and best practices. Manage MR submissions and ensure
      the codebase stays clean and well-documented.
    model:
      provider: nvidia
      name: aws/anthropic/bedrock-claude-opus-4-6
    tools: [bash, read_file, write_file, edit_file, list_files, search_files]
---

# Task Goal

TODO: Write your task description here in natural Markdown.

Everything below the `---` frontmatter becomes the **goal** field that the
coordinator sees. You can use full Markdown formatting.

## Background

- Explain the context and motivation
- Reference relevant files, documentation, or prior work

## Requirements

1. First requirement
2. Second requirement
3. Expected output format and location

## Success Criteria

- What "done" looks like
- Specific metrics or acceptance tests, if any
