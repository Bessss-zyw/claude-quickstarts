---
# ── Universal Agent Harness — Task Template (Markdown) ──
#
# Copy this file and fill in your task details.
# Run:  python main.py --task your_task.md
#
# The YAML frontmatter (between ---) contains config.
# Everything below the frontmatter becomes the task goal.

name: "my-task"

deliverables:
  - path: result.md
    description: "Final report"

environment:
  working_dir: /tmp/harness-workdir
  allowed_paths: []
  shell_preamble: []

agents:
  coordinator:
    role: "Plan and coordinate task execution."
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
    role: "Write and debug code as instructed."
    model:
      provider: nvidia
      name: aws/anthropic/bedrock-claude-opus-4-6
    tools:
      - bash
      - read_file
      - write_file
      - edit_file
      - list_files
      - search_files
---

# Task Goal

Write your task description here in natural Markdown.

Everything below the `---` frontmatter becomes the **goal** field that the
coordinator sees. You can use full Markdown formatting:

## Background

- Explain the context
- Reference relevant files or documentation

## Requirements

1. First requirement
2. Second requirement
3. Expected output format

## Success Criteria

- What "done" looks like
- Performance targets, if any
