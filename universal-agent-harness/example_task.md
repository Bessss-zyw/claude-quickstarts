---
# Minimal smoke-test example (Markdown format)
#
# Smallest possible config: coordinator + 1 coder.
# Run:  python main.py --task example_task.md --max-iterations 3

name: "hello-world-smoke-test"
context: "Minimal smoke test — verify the harness works."

deliverables:
  - path: hello.py
    description: "A single-file Python script that prints a greeting."

environment:
  working_dir: /tmp/agent-harness-test
  shell_preamble: []
  allowed_paths: []

agents:
  coordinator:
    role: "Plan and coordinate. Assign the coding task to the coder."
    model:
      provider: nvidia
      name: aws/anthropic/bedrock-claude-opus-4-6
    tools: [get_plan, create_plan, update_plan, assign_task, check_result, check_all_results, read_file]

  coder:
    role: "Write Python code as instructed."
    model:
      provider: nvidia
      name: aws/anthropic/bedrock-claude-opus-4-6
    tools: [bash, read_file, write_file, edit_file, list_files]
---

# Hello World Smoke Test

Write a Python script that prints "Hello from Agent Harness!" and save it to
`output/hello.py`.

This is a minimal test to verify the harness runs end-to-end.
