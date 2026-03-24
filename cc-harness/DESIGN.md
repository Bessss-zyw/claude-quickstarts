# CC-Native Multi-Agent Harness — Design

> Coordinator (Opus API) orchestrates N Claude Code instances via tmux.
> Each specialist CC instance has full MCP/Skills/tool access.

## Architecture

```
supervisor.py (Python process)
  │
  ├─ Parse task.md → agents, plan, environment
  │
  ├─ Launch CC instances (1 per specialist)
  │   ├─ tmux new-window -n "coder"   → claude --project-dir /path
  │   ├─ tmux new-window -n "tester"  → claude --project-dir /path
  │   └─ tmux new-window -n "profiler" → claude --project-dir /path
  │
  └─ Coordinator Loop (pure Opus API):
      │
      ├─ Phase 1: Plan
      │   Call Opus API with task + agent list → get plan (steps + assignments)
      │   Write plan to .harness/plan.json
      │
      ├─ Phase 2: Dispatch
      │   For each pending step:
      │     tmux send-keys -t "specialist_name" "instruction" Enter
      │     Mark step as in_progress
      │
      ├─ Phase 3: Monitor (poll loop)
      │   For each in_progress specialist:
      │     output = tmux capture-pane -t "specialist_name"
      │     if is_idle(output):
      │       result = extract_result(output)
      │       mark step done, record result
      │     elif is_permission(output):
      │       tmux send-keys Enter (auto-approve)
      │     elif ctx_pct >= threshold:
      │       send /compact
      │
      ├─ Phase 4: Evaluate & Re-plan
      │   Call Opus API with results so far → next steps or TASK_COMPLETE
      │
      └─ Repeat until done or max_iterations
```

## Files

```
cc-harness/
├── supervisor.py          # CLI entry + coordinator main loop
├── cc_instance.py         # CC instance lifecycle (start/stop/send/capture)
├── state_detector.py      # Detect pane state: active/idle/permission/error/expired
├── planner.py             # Opus API calls for planning & evaluation
├── config.py              # Parse task.md/yaml, HarnessConfig dataclass
├── requirements.txt       # pyyaml, python-dotenv, openai (for Opus API only)
├── templates/
│   └── task_template.md   # Template with 5-agent team
└── README.md
```

## Runtime Directory

```
{working_dir}/
├── .harness/
│   ├── plan.json            # Current execution plan
│   ├── history.jsonl        # Coordinator decision log
│   ├── agents/
│   │   └── {name}.log      # Captured pane output per specialist
│   └── token_usage.json    # Coordinator Opus API token tracking
└── output/                  # Deliverables
```

## Module Specs

### config.py

```python
@dataclass
class AgentDef:
    name: str
    role: str              # System-level role description
    project_dir: str       # CC --project-dir (determines MCP/skills)
    allowlist: str | None  # CC --allowedTools pattern (optional)
    is_coordinator: bool

@dataclass
class HarnessConfig:
    task_file: str
    working_dir: str
    task_name: str
    task_goal: str
    task_context: str
    deliverables: list[dict]
    agents: dict[str, AgentDef]
    tmux_session: str      # tmux session name (default: "harness")
    max_iterations: int
    compact_threshold: int  # % (default 60)
    send_cooldown: int      # seconds (default 30)
    poll_interval: int      # seconds (default 15)
```

task.md frontmatter adds CC-specific fields:
```yaml
agents:
  coder:
    role: "..."
    project_dir: /path/to/project   # CC works here, inherits .claude/ config
    allowlist: "Bash,Read,Write,Edit,Glob,Grep"  # optional tool filter
```

### cc_instance.py

```python
class CCInstance:
    """Manage a single Claude Code process in a tmux window."""

    def __init__(self, agent: AgentDef, tmux_session: str, config: HarnessConfig):
        self.agent = agent
        self.window_name = agent.name
        self.tmux_target = f"{tmux_session}:{agent.name}"

    def start(self) -> None:
        """Launch CC in a new tmux window.
        tmux new-window -t {session} -n {name} 'cd {project_dir} && claude'
        Wait for CC to be ready (detect ❯ prompt).
        """

    def send(self, instruction: str) -> None:
        """Send text to the CC pane via tmux send-keys."""
        # tmux send-keys -t {target} "{instruction}" Enter

    def capture(self, lines: int = 200) -> str:
        """Capture current pane output."""
        # tmux capture-pane -t {target} -p -S -{lines}

    def approve_permission(self) -> None:
        """Send Enter to approve a permission prompt."""

    def send_compact(self, hint: str = "") -> None:
        """Send /compact with optional context hint."""

    def stop(self) -> None:
        """Send /exit or kill the window."""

    def is_running(self) -> bool:
        """Check if the tmux window still exists."""
```

### state_detector.py

```python
class PaneState(Enum):
    ACTIVE = "active"           # CC is thinking/working
    IDLE = "idle"               # ❯ prompt, waiting for input
    PERMISSION = "permission"   # Waiting for y/N approval
    ERROR = "error"             # Unrecoverable error visible
    EXPIRED = "expired"         # Session/machine expired
    UNKNOWN = "unknown"

def detect_state(pane_output: str) -> PaneState:
    """Analyze tmux pane output to determine CC state."""

def get_context_pct(pane_output: str) -> int | None:
    """Extract context usage % from status line."""

def extract_last_response(pane_output: str) -> str:
    """Extract the last CC response text (between prompts)."""
```

Patterns (from Isaac's impl):
- ACTIVE: Shimmying|Doodling|Thinking|Plotting|Pondering|Cogitating|✢|✽
- IDLE: line starts with `❯` and not active
- PERMISSION: "Do you want to proceed"|"Allow this action"|"[y/N]"
- Context %: grep for `[0-9]+%` in status line

### planner.py

```python
class Planner:
    """Opus API for coordinator decision-making."""

    def __init__(self, config: HarnessConfig):
        # Init OpenAI client pointing to NVIDIA Inference Hub

    def create_plan(self, task_goal: str, task_context: str,
                    agents: list[AgentDef]) -> list[dict]:
        """Call Opus to generate initial plan.
        Returns list of steps: [{description, assigned_to, depends_on}]
        """

    def evaluate_and_replan(self, plan: dict, results: dict,
                            agents: list[AgentDef]) -> dict:
        """Given current plan + completed results, decide next actions.
        Returns: {
            "next_instructions": {agent_name: instruction_text, ...},
            "plan_updates": [{step_id, status, findings}, ...],
            "new_steps": [...],
            "is_complete": bool,
            "reasoning": str
        }
        """

    def generate_instruction(self, agent: AgentDef, step: dict,
                              context: str) -> str:
        """Generate a specific instruction for a specialist.
        Context includes: task goal, step description, agent role,
        recent pane output (last ~2000 chars).
        """
```

### supervisor.py — Main Loop

```python
def main():
    # 1. Parse args + load config
    # 2. Create tmux session
    # 3. Start CC instances for each specialist
    # 4. Wait for all CC instances to be ready

    # 5. Coordinator loop:
    iteration = 0
    while True:
        iteration += 1

        # Create/update plan via Opus
        if iteration == 1:
            plan = planner.create_plan(...)
            save_plan(plan)
        else:
            decisions = planner.evaluate_and_replan(plan, results, agents)
            apply_decisions(decisions)
            if decisions["is_complete"]:
                break

        # Dispatch: send instructions to specialists
        for step in get_pending_steps(plan):
            agent_name = step["assigned_to"]
            instruction = planner.generate_instruction(agent, step, ...)
            cc_instances[agent_name].send(instruction)
            step["status"] = "in_progress"

        # Monitor loop: wait for dispatched tasks to complete
        while has_in_progress_steps():
            for name, cc in cc_instances.items():
                output = cc.capture()
                state = detect_state(output)

                if state == PERMISSION:
                    cc.approve_permission()
                elif state == IDLE and was_in_progress(name):
                    result = extract_last_response(output)
                    mark_step_done(name, result)
                elif get_context_pct(output) >= compact_threshold:
                    cc.send_compact()

            sleep(poll_interval)

        # Check stop conditions
        if iteration >= max_iterations:
            break

    # 6. Cleanup: save state, report results
    # 7. Optionally stop CC instances
```

## Key Design Decisions

1. **Coordinator is NOT a CC instance** — pure Opus API. Lightweight, no CC overhead,
   no MCP needed for planning.

2. **Each specialist is a full CC** — inherits .claude/ config from project_dir,
   including MCP servers, skills, rules. No tool reimplementation needed.

3. **Communication is via tmux** — send-keys for instructions, capture-pane for output.
   Simple, debuggable (you can attach to any window and see what's happening).

4. **Plan is file-based** — .harness/plan.json, same as v1. Resumable.

5. **Specialist CC instances are long-running** — they stay open between tasks.
   Coordinator sends new instructions when ready. /compact manages context.

6. **No message bus needed** — coordinator directly reads pane output. Much simpler.
