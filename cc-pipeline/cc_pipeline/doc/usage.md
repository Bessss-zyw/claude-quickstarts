# Usage Guide

## Installation

```bash
cd cc-pipeline
pip install -e .

# With dev dependencies (pytest, pytest-asyncio):
pip install -e ".[dev]"
```

This installs the `cc-pipeline` CLI command.

**Requirement**: Python 3.11+ and `claude` CLI installed and on PATH.

## CLI Commands

```
cc-pipeline --task <path>                # Run the pipeline
cc-pipeline --task <path> --resume       # Resume a previously interrupted run
cc-pipeline --task <path> --status       # Show pipeline state (JSON)
cc-pipeline --task <path> --dry-run      # Validate config and show execution plan
cc-pipeline --task <path> --log-level DEBUG  # Set log verbosity
cc-pipeline --task <path> --claude-bin /path/to/claude  # Custom claude binary
```

### `--task` / `-t` (required)

Path to the task YAML file. Can be relative or absolute.

### `--resume` / `-r`

Resume a previously interrupted pipeline run. Without this flag, any existing state is discarded and the pipeline starts fresh. With this flag, completed stages are skipped and the pipeline continues from where it left off.

### `--status` / `-s`

Print the current pipeline state as JSON and exit. Useful for checking progress of a running or completed pipeline.

### `--dry-run`

Validate the task YAML, print the execution plan (waves of stages), and exit without running anything.

### `--log-level`

Set logging verbosity. Choices: `DEBUG`, `INFO` (default), `WARNING`, `ERROR`. Can also be set via `CC_PIPELINE_LOG_LEVEL` environment variable.

### `--claude-bin`

Path to the `claude` binary. Defaults to `claude` (found on PATH).

## Task YAML Format

A task YAML file defines the pipeline. Here is a complete field reference:

### Top-Level Fields

```yaml
name: "my-pipeline"              # Pipeline name (default: filename stem)
description: "What this does"    # Optional description
working_dir: /path/to/project    # Working directory (default: YAML file's directory)
max_parallel: 4                  # Max concurrent stages (default: 4)

defaults:                        # Default values inherited by all stages
  model: claude-sonnet-4-5
  effort: medium
  max_budget_usd: 1.0
  permissions: readonly
  add_dirs: [/extra/dir]

stages:                          # Stage definitions (see below)
  stage-name:
    ...
```

### Stage Fields

```yaml
stages:
  my-stage:
    # ---- Required ----
    prompt_template: |
      Your prompt here with {{variables}}.

    # ---- Type & Dependencies ----
    type: subtask            # pre-exec | subtask | loop | post-exec (default: subtask)
    depends_on: [other-stage]  # Stages that must complete first (default: [])
    description: "..."       # Human-readable description

    # ---- CC Invocation Parameters ----
    model: claude-sonnet-4-5   # Model name (inherits from defaults)
    effort: medium             # low | medium | high | max (inherits from defaults)
    max_budget_usd: 1.0        # Cost limit per invocation (inherits from defaults)
    fallback_model: claude-haiku-3  # Fallback if primary fails
    permissions: readonly      # readonly | write | full (inherits from defaults)
    system_prompt: "..."       # Appended to CC system prompt
    add_dirs: [/extra/dir]     # Additional directories for CC to access
    persist_session: false     # Whether to persist CC session (default: false)
    extra_flags: ["--flag"]    # Additional raw CLI flags

    # ---- Inputs ----
    inputs:
      # String shorthand → literal input
      my_literal: "some value"

      # Full spec with type
      source_files:
        type: glob             # literal | glob | file | stage_output
        pattern: "**/*.py"     # For glob type
      readme:
        type: file
        path: "README.md"      # Relative to working_dir

    # ---- Outputs ----
    outputs:
      # String shorthand → output type
      summary: text            # text | json | number | file

      # Full spec
      analysis:
        type: json
        schema: {targets: array}   # Optional schema hint (documentation only)
      report:
        type: file
        path: "REPORT.md"         # For file type

    # ---- Loop-Specific (type: loop only) ----
    max_iterations: 5          # Maximum iterations (default: 5)
    convergence:
      metric: "outputs.speedup"  # Dot-path to the metric in outputs
      threshold: 1.5             # Target value
      operator: ">="             # >= | <= | > | < | == (default: >=)
```

### Stage Types

| Type | Purpose | Strategy |
|------|---------|----------|
| `pre-exec` | Setup / analysis before main work | SingleShotStrategy |
| `subtask` | Main work stage (default) | SingleShotStrategy |
| `loop` | Iterative optimization with convergence | LoopStrategy |
| `post-exec` | Summary / reporting after main work | SingleShotStrategy |

### Input Types

| Type | Fields | Description |
|------|--------|-------------|
| `literal` | `value` | Static string passed directly |
| `glob` | `pattern` | Glob pattern resolved against `working_dir` |
| `file` | `path` | File content read from `working_dir/path` |
| `stage_output` | `value` (stage name) | Output from another stage (usually via template) |

### Output Types

| Type | Extractor Behavior |
|------|-------------------|
| `text` | Returns the full CC response text |
| `json` | Extracts JSON from code fences, raw JSON, or inline JSON |
| `number` | Tries: JSON field by name, `key: value` pattern, last number in text |
| `file` | Returns `spec.path` if set, otherwise the stripped response text |

### Template Variables

Use `{{...}}` syntax in `prompt_template`:

```yaml
prompt_template: |
  Analyze these files: {{inputs.source_files}}

  Previous analysis results: {{stages.analyze.outputs.targets}}

  {{#if loop.previous}}
  Previous iteration speedup: {{loop.previous.outputs.speedup}}
  {{/if}}

  Project name: {{config.name}}
  User home: {{env.HOME}}
```

| Variable | Source |
|----------|--------|
| `{{inputs.X}}` | Resolved input value |
| `{{stages.X.outputs.Y}}` | Output Y from stage X |
| `{{loop.iteration}}` | Current loop iteration (0-based) |
| `{{loop.previous.outputs.X}}` | Previous iteration's output X |
| `{{config.X}}` | Task config value or default |
| `{{env.X}}` | Environment variable |

## Tutorial: Writing a Pipeline

### Step 1: Define the Goal

Decide what your pipeline does. A pipeline is a DAG of stages where each stage invokes Claude Code with a specific prompt and extracts structured outputs.

### Step 2: Create the YAML

Start with a minimal pipeline:

```yaml
name: "my-first-pipeline"
working_dir: /path/to/my/project

defaults:
  model: claude-sonnet-4-5
  permissions: readonly

stages:
  analyze:
    type: pre-exec
    prompt_template: |
      Analyze the Python files in this project.
      Output JSON: {"issues": [...], "file_count": N}
    outputs:
      issues: {type: json}
      file_count: {type: number}

  fix:
    type: subtask
    depends_on: [analyze]
    permissions: write
    prompt_template: |
      Fix the following issues:
      {{stages.analyze.outputs.issues}}
    outputs:
      summary: {type: text}
```

### Step 3: Validate

```bash
cc-pipeline --task my-pipeline.yaml --dry-run
```

This prints the execution plan:

```
Pipeline: my-first-pipeline
Description:
Working dir: /path/to/my/project
Max parallel: 4
Stages: 2

Execution plan:
----------------------------------------

  Wave 1:
    • analyze [pre-exec] model=claude-sonnet-4-5 effort=medium perm=readonly

  Wave 2:
    • fix [subtask] model=claude-sonnet-4-5 effort=medium perm=write ← [analyze]

Config valid ✓
```

### Step 4: Run

```bash
cc-pipeline --task my-pipeline.yaml
```

### Step 5: Check Status

While running or after completion:

```bash
cc-pipeline --task my-pipeline.yaml --status
```

### Step 6: Resume (if interrupted)

```bash
cc-pipeline --task my-pipeline.yaml --resume
```

## Pipeline Artifacts

All pipeline state and artifacts are stored under `<working_dir>/.pipeline/`:

```
.pipeline/
├── state.json              # Pipeline state (atomically updated)
├── artifacts/
│   ├── analyze/
│   │   ├── raw_response.txt   # Full CC response text
│   │   ├── meta.json          # Session ID, token usage, duration
│   │   └── output.json        # Extracted outputs
│   ├── fix/
│   │   ├── raw_response.txt
│   │   ├── meta.json
│   │   └── output.json
│   └── optimize/              # Loop stage
│       ├── iter_0/
│       │   ├── raw_response.txt
│       │   ├── meta.json
│       │   └── output.json
│       ├── iter_1/
│       │   └── ...
│       ├── raw_response.txt   # Final loop output
│       └── output.json
└── logs/
    ├── analyze.ndjson         # Raw NDJSON stream events
    └── fix.ndjson
```

## Example Pipelines

### `smoke_test.yaml`

A minimal 4-stage pipeline for testing the framework. Four stages in three waves:
1. `step-a` (pre-exec) → generates a greeting and number
2. `step-b` and `step-c` (subtasks, parallel) → process step-a outputs
3. `final` (post-exec) → combines results

### `simple_refactor.yaml`

A Python refactoring pipeline with four stages:
1. `analyze` (pre-exec) → identifies refactoring targets
2. `extract-utils` and `update-docs` (subtasks, parallel) → apply changes
3. `summarize` (post-exec) → generates final report

### `kernel_optimize.yaml`

A CUDA/Triton kernel optimization pipeline with a loop stage:
1. `analyze-source` (pre-exec) → identifies bottlenecks
2. `profile-baseline` (subtask) → runs baseline benchmark
3. `optimize` (loop, max 5 iterations) → iteratively optimizes with convergence check (speedup >= 1.5x)
4. `report` (post-exec) → generates optimization report
