# CC-Native Multi-Agent Harness

Orchestrate multiple Claude Code instances as specialist agents, coordinated by Opus API via tmux.

## How It Differs from universal-agent-harness

| | universal-agent-harness | cc-harness |
|---|---|---|
| Specialists | Reimplemented tools (bash, read_file, etc.) | Full Claude Code instances |
| MCP/Skills | Not available | Inherited from project_dir/.claude/ |
| Coordinator | LLM with tool calling (ReAct loop) | Pure Opus API (no CC instance) |
| Communication | File-based message bus | tmux send-keys / capture-pane |
| Overhead | Lower (API only) | Higher (N CC processes) but richer |

## Installation

```bash
pip install -r requirements.txt
```

Dependencies: `pyyaml`, `python-dotenv`, `openai`

Also required: `tmux`, `claude` CLI (Claude Code).

## Quick Start

### 1. Set API key

```bash
export NVIDIA_API_KEY="nvapi-..."
# Or create a .env file next to your task file
```

### 2. Write a task file

```yaml
---
name: "my-task"
context: "Background info"

deliverables:
  - path: result.py
    description: "Optimized implementation"

environment:
  working_dir: /tmp/my-workdir

agents:
  coordinator:
    role: "Plan and coordinate."
    project_dir: .

  coder:
    role: "Write and debug code."
    project_dir: /home/user/my-project
    allowlist: "Bash,Read,Write,Edit,Glob,Grep"
---

# Goal

Describe what you want accomplished. This becomes the goal field.
```

### 3. Run

```bash
python supervisor.py --task task.md
python supervisor.py --task task.md --max-iterations 10
```

You can attach to any specialist's tmux window to watch it work:
```bash
tmux attach -t harness       # attach to session
# Ctrl+b n / Ctrl+b p        # switch windows
```

## Architecture

```
supervisor.py (Python process)
  │
  ├─ Parse task file → agents, environment
  │
  ├─ Launch CC instances (1 tmux window per specialist)
  │   ├─ tmux new-window -n "coder"    → claude --project-dir /path
  │   ├─ tmux new-window -n "tester"   → claude --project-dir /path
  │   └─ tmux new-window -n "profiler" → claude --project-dir /path
  │
  └─ Coordinator Loop (pure Opus API):
      ├─ Plan: call Opus → generate steps + assignments
      ├─ Dispatch: tmux send-keys instructions to specialists
      ├─ Monitor: poll capture-pane, detect idle/permission/error
      │   ├─ Auto-approve permission prompts
      │   ├─ Send /compact when context >= threshold
      │   └─ Extract results when specialist goes idle
      └─ Evaluate: call Opus → update plan or declare complete
```

## Task File Reference

Supports both `.yaml` and `.md` (YAML frontmatter + markdown body) formats.

| Field | Required | Description |
|-------|:--------:|-------------|
| `name` | Yes | Task identifier |
| `goal` | Yes | What to accomplish (or markdown body) |
| `context` | No | Background information |
| `deliverables` | Yes | List of `{path, description}` relative to `output/` |
| `environment.working_dir` | Yes | Runtime directory for .harness/ and output/ |
| `agents.coordinator` | Yes | Must exist; pure API coordinator, NOT a CC instance. Its `project_dir` is ignored. |
| `agents.{name}.role` | Yes | Role description |
| `agents.{name}.project_dir` | Yes | Directory where CC runs (`--project-dir`). CC inherits `.claude/` config (MCP, skills, rules) from this dir. |
| `agents.{name}.allowlist` | No | CC `--allowedTools` filter |
| `harness.tmux_session` | No | tmux session name (default: `harness`) |
| `harness.compact_threshold` | No | Context % trigger for /compact (default: 60) |
| `harness.poll_interval` | No | Seconds between state polls (default: 15) |
| `harness.send_cooldown` | No | Min seconds between sends (default: 30) |

## Runtime Directory

```
{working_dir}/
├── .harness/
│   ├── plan.json             # Execution plan (steps + status)
│   ├── history.jsonl         # Coordinator decision log
│   ├── agents/
│   │   └── {name}.log       # Captured pane output per specialist
│   └── token_usage.json     # Coordinator Opus API token tracking
└── output/                   # Final deliverables
```

## State Detection

The supervisor monitors each CC pane and detects these states:

| State | Meaning | Action |
|-------|---------|--------|
| `active` | CC is thinking (spinner visible) | Wait |
| `idle` | `❯` prompt, waiting for input | Extract result, mark step done |
| `permission` | Waiting for y/N approval | Auto-approve (send Enter) |
| `error` | Error visible in output | Log warning, may recover |
| `expired` | Session/context expired | Mark step failed |

## Resume

All state is file-based. Re-running with the same `working_dir` automatically resumes:
- Existing `plan.json` is loaded
- In-progress steps are re-monitored
- CC instances are re-launched

To start fresh, delete `.harness/`.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `NVIDIA_API_KEY` | Required. API key for Opus coordinator calls |
| `NVIDIA_BASE_URL` | Optional. Override inference endpoint (default: `https://inference-api.nvidia.com/v1`) |

## Permission Handling

Each specialist CC instance will prompt for tool permissions on first use. The harness auto-approves by sending Enter when it detects a permission prompt.

For smoother operation, pre-authorize tools in each specialist's project directory:

```json
// {project_dir}/.claude/settings.json
{
  "permissions": {
    "allow": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"]
  }
}
```

Alternatively, launch specialists with broader permissions via the `allowlist` field in your task file.

## Templates

A full 5-agent team template is provided at `templates/task_template.md`. Copy and customize:

```bash
cp templates/task_template.md my_task.md
# Edit my_task.md with your task details
python supervisor.py --task my_task.md
```

## Running Tests

```bash
cd cc-harness
python -m unittest tests.test_harness -v
```

All tests are pure unit tests (no tmux/CC required) using mocks.

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| CC instance never reaches `idle` | `claude` CLI not in PATH, or tmux window failed to start | Run `which claude` and `tmux ls` to verify |
| State stuck on `UNKNOWN` | Pane output doesn't match any detection pattern | `tmux capture-pane -t harness:{name} -p` to inspect raw output; update `state_detector.py` patterns if CC version changed |
| Permission auto-approve not working | CC shows a selection menu (Allow once / Allow always) instead of simple y/N | Pre-authorize tools in `.claude/settings.json` (see Permission Handling above) |
| `NVIDIA_API_KEY` error | API key not set or expired | Check `echo $NVIDIA_API_KEY`; create `.env` file next to task file |
| Steps all `pending`, nothing dispatched | `depends_on` cycle or unknown `assigned_to` agent name | Check `plan.json` — agent names must match keys in `agents:` |
| Context hits 100% and CC stops | Long-running task exceeded CC context window | Lower `compact_threshold` (e.g., 40) to trigger `/compact` earlier |
