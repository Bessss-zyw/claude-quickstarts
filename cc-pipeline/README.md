# cc-pipeline

A structured task pipeline framework for orchestrating multiple Claude Code invocations as a DAG of stages. Define your pipeline as a YAML file with typed inputs/outputs, dependency chains, and loop stages with convergence — cc-pipeline handles scheduling, parallelism, crash recovery, and artifact management.

cc-pipeline treats Claude Code as a composable building block. Instead of one monolithic prompt, you decompose work into stages that flow data through a dependency graph. Each stage runs in isolation with explicit permissions, and results are extracted, validated, and passed downstream automatically.

## Features

- **DAG-based scheduling** — Stages run in parallel when dependencies allow, bounded by configurable concurrency
- **Four stage types** — `pre-exec`, `subtask`, `loop` (iterative with convergence), `post-exec`
- **Typed outputs** — Extract `json`, `number`, `text`, or `file` from CC responses
- **Prompt templates** — `{{stages.X.outputs.Y}}` interpolation, conditionals, env vars
- **Crash recovery** — Atomic state persistence; `--resume` skips completed stages
- **Permission model** — `readonly` / `write` / `full` mapped to CC CLI flags
- **Plugin architecture** — Extend with custom Runners, Extractors, Strategies, and Convergence Operators
- **Zero magic strings** — All type discriminators are enums
- **Small modules** — Every file under 200 lines

## Quick Start

### Install

```bash
cd cc-pipeline
pip install -e .
```

### Define a Pipeline

Create `my-task.yaml`:

```yaml
name: "code-review"
working_dir: /path/to/project

defaults:
  model: claude-sonnet-4-5
  permissions: readonly

stages:
  analyze:
    type: pre-exec
    prompt_template: |
      Analyze the Python files in this project for code quality issues.
      Output JSON: {"issues": [...], "severity": "high"|"medium"|"low"}
    outputs:
      issues: {type: json}
      severity: {type: text}

  fix:
    type: subtask
    depends_on: [analyze]
    permissions: write
    prompt_template: |
      Fix these issues: {{stages.analyze.outputs.issues}}
    outputs:
      summary: {type: text}

  report:
    type: post-exec
    depends_on: [fix]
    prompt_template: |
      Generate a summary of changes: {{stages.fix.outputs.summary}}
    outputs:
      report: {type: text}
```

### Run

```bash
# Validate first
cc-pipeline --task my-task.yaml --dry-run

# Execute
cc-pipeline --task my-task.yaml

# Resume after interruption
cc-pipeline --task my-task.yaml --resume

# Check status
cc-pipeline --task my-task.yaml --status
```

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](cc_pipeline/doc/architecture.md) | Layered design, dependency rules, extension points |
| [Design Decisions](cc_pipeline/doc/design.md) | Why ABC+Registry, DAG scheduling, crash recovery, template engine |
| [Usage Guide](cc_pipeline/doc/usage.md) | Installation, YAML format reference, CLI commands, tutorial |
| [Extending](cc_pipeline/doc/extending.md) | How to add new Runners, Extractors, Strategies, Operators |

## Project Structure

```
cc-pipeline/
├── pyproject.toml                 # Package metadata, CLI entry point
├── README.md
└── cc_pipeline/
    ├── constants/
    │   └── enums.py               # All enums (StageType, OutputType, PermissionLevel, ...)
    ├── utils/
    │   ├── json_extract.py        # JSON extraction from LLM output
    │   └── time_utils.py          # Timestamp utility
    ├── config/
    │   ├── models.py              # TaskConfig, StageConfig, InputSpec, OutputSpec, ...
    │   ├── loader.py              # YAML parsing + defaults
    │   └── validator.py           # DAG cycle detection (Kahn's algorithm)
    ├── state/
    │   ├── models.py              # PipelineState, StageState, TokenUsage, RunResult
    │   ├── transitions.py         # Pure state mutation functions
    │   ├── recovery.py            # Crash recovery (running → pending)
    │   ├── queries.py             # Read-only state queries
    │   ├── serialization.py       # JSON serialization/deserialization
    │   └── manager.py             # Atomic persistence + transition delegation
    ├── runners/
    │   ├── base.py                # Runner ABC
    │   ├── claude_cli.py          # ClaudeCliRunner (subprocess)
    │   ├── command_builder.py     # CLI argument construction
    │   └── ndjson_parser.py       # NDJSON stream parsing
    ├── extractors/
    │   ├── base.py                # OutputExtractor ABC + ExtractorRegistry
    │   ├── json_extractor.py      # JSON output extraction
    │   ├── number_extractor.py    # Numeric output extraction
    │   ├── text_extractor.py      # Full-text output extraction
    │   └── file_extractor.py      # File path extraction
    ├── artifacts/
    │   ├── layout.py              # Path computation (no I/O)
    │   ├── saver.py               # Write artifacts to disk
    │   ├── reader.py              # Read artifacts from disk
    │   ├── input_resolver.py      # Resolve glob/file/literal inputs
    │   └── store.py               # Facade over layout+saver+reader+resolver
    ├── templates/
    │   └── engine.py              # Minimal prompt template engine
    ├── strategies/
    │   ├── base.py                # StageStrategy ABC + StrategyRegistry
    │   ├── single_shot.py         # Single CC invocation strategy
    │   ├── loop.py                # Iterative strategy with convergence
    │   └── convergence.py         # ConvergenceOperator ABC + registry
    ├── orchestration/
    │   ├── dag_scheduler.py       # Ready/blocked/stuck stage detection
    │   └── orchestrator.py        # Main DAG execution loop
    ├── cli/
    │   ├── main.py                # Entry point + DI composition
    │   └── display.py             # Terminal output formatting
    ├── examples/
    │   ├── smoke_test.yaml        # Minimal test pipeline
    │   ├── simple_refactor.yaml   # Code refactoring pipeline
    │   └── kernel_optimize.yaml   # Iterative optimization with convergence
    ├── doc/
    │   ├── architecture.md
    │   ├── design.md
    │   ├── usage.md
    │   └── extending.md
    └── tests/
        ├── unit/                  # 39 unit tests
        └── integration/           # 4 integration tests (DAG, loop, crash recovery)
```

## Testing

```bash
pip install -e ".[dev]"
python -m pytest cc_pipeline/tests/ -v
```

46 tests covering configuration, state management, templates, artifacts, runners, extractors, strategies, DAG execution, loop convergence, and crash recovery.
