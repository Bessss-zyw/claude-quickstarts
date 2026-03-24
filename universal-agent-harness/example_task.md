---
name: "hello-world-demo"

deliverables:
  - path: hello.py
    description: "A Python script that prints a greeting message."

environment:
  working_dir: /tmp/agent-harness-test
  shell_preamble: []
  allowed_paths: []

agents:
  coordinator:
    role: "Plan and coordinate task execution. Assign work to specialists."
    model:
      provider: openai
      name: gpt-4o
    tools:
      - get_plan
      - create_plan
      - update_plan
      - assign_task
      - check_result
      - check_all_results
      - read_file

  coder:
    role: "Write Python code as instructed. Save outputs to the designated paths."
    model:
      provider: openai
      name: gpt-4o
    tools:
      - bash
      - read_file
      - write_file
      - edit_file
      - list_files
      - search_files
---

# Hello World Demo

Write a simple Python script that prints "Hello from Agent Harness!" and save
it to the output directory.

## Context

This is a minimal test task to verify the harness works end-to-end.

## Requirements

1. The script should be a single `.py` file
2. It should print exactly: `Hello from Agent Harness!`
3. Save to `output/hello.py`
