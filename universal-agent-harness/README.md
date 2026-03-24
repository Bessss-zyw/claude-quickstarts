# Universal Agent Harness

A multi-agent coordination framework that orchestrates LLM-powered specialist agents to execute complex tasks via a coordinator-driven ReAct loop.

## Installation

```bash
pip install -r requirements.txt
```

Dependencies: `openai`, `pyyaml`, `python-dotenv`

## Quick Start

### 1. Set up API keys

Create a `.env` file next to your `task.yaml` (or in the harness directory):

```bash
# .env — loaded automatically at startup
# Copy from .env.example and fill in your keys.

# NVIDIA Inference Hub (default base_url; override only if needed)
# NVIDIA_BASE_URL=https://inference-api.nvidia.com/v1
NVIDIA_API_KEY=nvapi-...

# OpenAI
OPENAI_API_KEY=sk-...

# DeepSeek
DEEPSEEK_API_KEY=sk-...
```

The harness searches for `.env` in this order: task.yaml directory → cwd → harness source directory.

Alternatively, export env vars directly:
```bash
export NVIDIA_API_KEY="nvapi-..."
```

### 2. Write a `task.yaml`

```yaml
name: "my-task"
goal: |
  Describe what you want the agent team to accomplish.
  Be specific about expected outputs.
context: |
  Optional background information.

deliverables:
  - path: report.md              # relative to {working_dir}/output/
    description: "Final report"

environment:
  working_dir: /path/to/workdir  # runtime state goes here
  allowed_paths:                 # external paths agents can access
    - /home/user/project/src
  shell_preamble:                # prepended to every bash command
    - "conda activate myenv"

agents:
  coordinator:                   # required — exactly this key name
    role: "Plan and coordinate task execution."
    model:
      provider: openai           # openai | nvidia | deepseek | custom
      name: gpt-4o
    tools:
      - get_plan
      - create_plan
      - update_plan
      - assign_task
      - check_result
      - check_all_results
      - read_file

  coder:                         # custom specialist (name anything)
    role: "Write and debug code."
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

  reviewer:                      # add as many specialists as needed
    role: "Review code quality."
    model:
      provider: deepseek
      name: deepseek-chat
    tools:
      - read_file
      - list_files
      - search_files
```

### 3. Run

```bash
python main.py --task task.yaml
python main.py --task task.yaml --max-iterations 10
```

## How It Works

```
main.py → CoordinatorLoop
              │
              ├─ iteration 1:
              │   ├─ Coordinator LLM session → creates plan + assigns tasks
              │   └─ Each specialist runs its assigned task (ReAct loop)
              │
              ├─ iteration 2:
              │   ├─ Coordinator checks results → updates plan → assigns next tasks
              │   └─ Specialists execute...
              │
              └─ ... until all plan steps are "done" or max iterations reached
```

The coordinator *never* executes code directly — it only plans, assigns, and reviews. Specialists do all the real work.

## Tool Matrix

| Tool | Coordinator | Specialist | Notes |
|------|:-----------:|:----------:|-------|
| `get_plan` | ✓ | ✗ | |
| `create_plan` | ✓ | ✗ | |
| `update_plan` | ✓ | ✗ | |
| `assign_task` | ✓ | ✗ | |
| `check_result` | ✓ | ✗ | |
| `check_all_results` | ✓ | ✗ | |
| `write_journal` | ✓ | ✓ | Auto-injected for all agents |
| `read_file` | ✓ | ✓ | Coordinator uses for deliverable checks |
| `bash` | ✗ | ✓ | Blocked at runtime for coordinator |
| `write_file` | ✗ | ✓ | |
| `edit_file` | ✗ | ✓ | |
| `list_files` | ✗ | ✓ | |
| `search_files` | ✗ | ✓ | |

## Runtime Directory Structure

After running, `working_dir` will contain:

```
{working_dir}/
├── .harness/
│   ├── plan.json               # execution plan (steps + status)
│   ├── journals/
│   │   ├── coordinator.md      # coordinator decision history
│   │   └── {agent}.md          # each specialist's work log
│   ├── messages/
│   │   └── msg_001.json        # task assignment messages
│   ├── token_usage.json        # per-agent token consumption
│   ├── session_log.jsonl       # session metadata
│   └── journal_archive/        # compressed journal backups
├── workspace/
│   └── {agent_name}/           # intermediate files per specialist
└── output/                     # final deliverables go here
```

## Resume

The harness is **interruptible and resumable**. All state lives on the filesystem:

- If `.harness/` exists when you re-run, it picks up where it left off.
- `Ctrl+C` triggers graceful shutdown (saves token usage).
- To start fresh, delete the `.harness/` directory.

## Key Design Decisions

- **Fresh context per session**: Each LLM call starts with a clean message history. Cross-session memory is maintained through journal files injected into system prompts.
- **Journal compression**: When a journal exceeds 3000 characters, it's archived and compressed (via LLM summary), preserving failed attempts with high priority.
- **Path safety**: All file operations are sandboxed to `working_dir` + explicitly declared `allowed_paths`. No exceptions.
- **Bash safety**: A blacklist blocks dangerous commands (`rm -rf /`, `chmod 777`, `curl | bash`, etc.).
- **Loop detection**: If an agent repeats the same tool call 3+ times, it gets a warning; after 2 warnings, the session is force-stopped.

## task.yaml Reference

| Field | Required | Description |
|-------|:--------:|-------------|
| `name` | ✓ | Task identifier |
| `goal` | ✓ | What to accomplish (multi-line) |
| `context` | ✗ | Background information |
| `deliverables` | ✓ | List of `{path, description}` — paths relative to `output/` |
| `environment.working_dir` | ✓ | Runtime directory (absolute or relative to task.yaml) |
| `environment.allowed_paths` | ✗ | External paths agents can access |
| `environment.shell_preamble` | ✗ | Commands prepended to every bash execution |
| `agents.coordinator` | ✓ | Must exist with this exact key |
| `agents.{name}` | ✗ | Any number of specialist agents |
| `agents.*.role` | ✓ | Role description (used in system prompt) |
| `agents.*.model.provider` | ✓ | LLM provider identifier |
| `agents.*.model.name` | ✓ | Model name |
| `agents.*.tools` | ✓ | List of allowed tool names |

## Environment Variables

| Variable | Provider | Required |
|----------|----------|:--------:|
| `OPENAI_API_KEY` | openai | ✓ |
| `NVIDIA_BASE_URL` | nvidia | ✗ (default: `https://inference-api.nvidia.com/v1`) |
| `NVIDIA_API_KEY` | nvidia | ✓ (get from [inference.nvidia.com/key-management](https://inference.nvidia.com/key-management)) |
| `DEEPSEEK_API_KEY` | deepseek | ✓ |
| `{PROVIDER}_BASE_URL` | custom | ✓ |
| `{PROVIDER}_API_KEY` | custom | ✓ |
