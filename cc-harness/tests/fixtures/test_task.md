---
name: "test-task-md"
context: "Unit test fixture (markdown)"
deliverables:
  - path: report.md
    description: "Test report"
environment:
  working_dir: /tmp/cc-harness-test-md
agents:
  coordinator:
    role: "Coordinate"
  coder:
    role: "Write code"
    project_dir: /tmp/cc-harness-test-md
---

# Test Task

This is the goal from the markdown body.

## Requirements

1. Produce report.md
