# Architecture Overview

cc-pipeline is a structured task pipeline framework for orchestrating multiple Claude Code (CC) invocations as a DAG of stages. This document describes the layered architecture, dependency rules, and extension points.

## Layered Design

The codebase is organized into seven layers, ordered from lowest-level (fewest dependencies) to highest-level (most dependencies):

```
Layer 7  cli/              Thin entry point; arg parsing, DI composition, display
Layer 6  orchestration/    DAG scheduler + pipeline orchestrator
Layer 5  strategies/       Stage execution strategies (single-shot, loop, convergence)
Layer 4  artifacts/        Disk I/O for stage inputs/outputs/logs
Layer 3  runners/          CC CLI subprocess wrapper + NDJSON parsing
         extractors/       Output extraction (JSON, number, text, file)
         templates/        Prompt template rendering
Layer 2  config/           YAML loading, validation, typed config models
         state/            State models, transitions, persistence, recovery
Layer 1  constants/        Enums — single source of truth for all type discriminators
         utils/            Pure utility functions (JSON extraction, timestamps)
```

### Dependency Direction

Dependencies flow **strictly downward**. No lower layer imports from a higher layer.

```
cli
 └─> orchestration
      ├─> strategies
      │    ├─> runners
      │    ├─> artifacts
      │    ├─> extractors
      │    └─> templates
      ├─> state (manager, queries)
      └─> config
           └─> constants/enums
                utils
```

Key constraint enforced by the architecture:

- `artifacts/` does NOT import from `runners/`. The shared `RunResult` dataclass lives in `state/models.py` so both layers can depend on it without circular imports.
- `orchestration/` does NOT import from `cli/`. Display logic lives in `cli/display.py`.
- `strategies/` does NOT import from `orchestration/`. Strategies receive a `StageContext` with all dependencies pre-injected.

## Layer Responsibilities

### Layer 1: `constants/` and `utils/`

**`constants/enums.py`** — Every string literal that serves as a type discriminator, status value, or configuration key is defined here as a `str` Enum member. Zero magic strings in the codebase.

Enums defined: `StageStatus`, `IterationStatus`, `PipelineStatus`, `StageType`, `OutputType`, `InputType`, `PermissionLevel`, `ConvergenceOp`.

**`utils/json_extract.py`** — Pure function `extract_json(text)` that extracts JSON from LLM output. Tries code fences, raw JSON, and inline patterns in order.

**`utils/time_utils.py`** — `now_iso()` returns current UTC time in ISO format.

### Layer 2: `config/` and `state/`

**`config/models.py`** — Frozen/mutable dataclasses: `OutputSpec`, `InputSpec`, `ConvergenceSpec`, `StageConfig`, `TaskConfig`. All fields use enum types.

**`config/loader.py`** — `load_task_config(path)` reads a YAML file, parses stages/inputs/outputs, applies defaults, resolves working directory, and validates the DAG.

**`config/validator.py`** — `validate_dag(config)` checks for missing stage references and cycles using Kahn's topological sort algorithm.

**`state/models.py`** — Mutable dataclasses for runtime state: `TokenUsage`, `IterationState`, `LoopState`, `StageState`, `PipelineState`, `RunResult`. `RunResult` is placed here (not in `runners/`) to avoid a circular dependency between `artifacts/` and `runners/`.

**`state/transitions.py`** — Pure mutation functions (`stage_to_running`, `stage_to_done`, etc.) that enforce valid state transitions. Separated from persistence for testability.

**`state/recovery.py`** — `recover_running_stages()` resets any stage/iteration left in RUNNING status back to PENDING after a crash.

**`state/queries.py`** — Pure read-only query functions: `is_stage_done`, `all_deps_done`, `get_pending_stages`, etc.

**`state/serialization.py`** — `serialize()` / `deserialize()` / `fresh_state()` for converting between `PipelineState` and JSON.

**`state/manager.py`** — `StateManager` class providing atomic JSON persistence (temp file + `os.replace`), lifecycle management (`load_or_create` with resume support), and delegated transition/query methods.

### Layer 3: `runners/`, `extractors/`, `templates/`

**`runners/base.py`** — `Runner` ABC with async `run()` method. All CC backends must implement this interface.

**`runners/claude_cli.py`** — `ClaudeCliRunner(Runner)` wraps `claude --print --bare --output-format stream-json` as an async subprocess. Prompt is delivered via stdin pipe to avoid E2BIG.

**`runners/command_builder.py`** — Builds the CLI argument list and resolves `PermissionLevel` to allowed tools / skip-permissions flags.

**`runners/ndjson_parser.py`** — Parses the NDJSON stream output, extracting the final text result, session ID, and accumulated token usage.

**`extractors/base.py`** — `OutputExtractor` ABC and `ExtractorRegistry`. The registry tries bulk JSON extraction first (single parse for all outputs), then falls back to per-output extraction.

**`extractors/json_extractor.py`**, **`number_extractor.py`**, **`text_extractor.py`**, **`file_extractor.py`** — Four built-in extractors, one per `OutputType`.

**`templates/engine.py`** — Minimal prompt template engine supporting `{{var}}` interpolation, `{{#if VAR}}...{{/if}}` conditionals, environment variables (`{{env.X}}`), and config values (`{{config.X}}`).

### Layer 4: `artifacts/`

Five focused modules, split from an original monolithic `ArtifactStore`:

| Module | Responsibility |
|--------|---------------|
| `layout.py` | Pure path computation under `.pipeline/` — no I/O |
| `saver.py` | Writes `response.txt`, `prompt.txt`, `meta.json`, `outputs.json` |
| `reader.py` | Reads previously saved stage outputs |
| `input_resolver.py` | Resolves `glob`, `file`, `literal` inputs |
| `store.py` | Thin facade composing the above four |

### Layer 5: `strategies/`

**`base.py`** — `StageStrategy` ABC, `StageContext` (dependency bag), and `StrategyRegistry` (maps `StageType` → strategy).

**`single_shot.py`** — `SingleShotStrategy` handles `pre-exec`, `subtask`, and `post-exec` stages: resolve inputs → render prompt → run CC → extract outputs → save artifacts.

**`loop.py`** — `LoopStrategy` handles `loop` stages: iterates until convergence or `max_iterations`, with crash recovery (resumes from last completed iteration).

**`convergence.py`** — `ConvergenceOperator` ABC, `ThresholdOperator` (generic comparison), and `OperatorRegistry`. Built-in operators: `>=`, `<=`, `>`, `<`, `==`.

### Layer 6: `orchestration/`

**`dag_scheduler.py`** — `DagScheduler` determines which stages are ready (all deps done), skips stages with failed deps, and detects stuck pipelines.

**`orchestrator.py`** — `PipelineOrchestrator` drives the main DAG loop with `asyncio.Semaphore`-bounded parallelism. All components are dependency-injected. The orchestrator owns stage lifecycle (marks stages running/done/failed).

### Layer 7: `cli/`

**`main.py`** — Entry point. Parses args, composes all components via `_compose()` (the DI composition root), and dispatches to `--status`, `--dry-run`, or the main run command.

**`display.py`** — `print_waves()` (execution plan), `state_summary()` (JSON status), and `print_pipeline_summary()` (final run report).

## Extension Points (Plugin System)

The architecture provides four ABC-based extension points, each following the same pattern: implement the ABC, then register the instance with the corresponding registry.

### 1. Runner (CC invocation backend)

```
ABC:       runners/base.py → Runner
Registry:  N/A (single instance injected directly)
Method:    async run(prompt, *, model, cwd, timeout, permission, ...) → RunResult
```

To add a new backend (e.g., API-based runner), implement `Runner.run()` and pass the instance to the orchestrator via `_compose()`.

### 2. OutputExtractor (output type handler)

```
ABC:       extractors/base.py → OutputExtractor
Registry:  extractors/base.py → ExtractorRegistry
Method:    extract(text, name, spec) → Any
```

To add a new output type, implement `OutputExtractor.extract()`, add an `OutputType` enum member, and register with `ExtractorRegistry.register()`.

### 3. StageStrategy (execution strategy)

```
ABC:       strategies/base.py → StageStrategy
Registry:  strategies/base.py → StrategyRegistry
Method:    async execute(ctx, stage_cfg) → dict[str, Any]
```

To add a new stage type, implement `StageStrategy.execute()`, add a `StageType` enum member, and register with `StrategyRegistry.register()`.

### 4. ConvergenceOperator (loop convergence check)

```
ABC:       strategies/convergence.py → ConvergenceOperator
Registry:  strategies/convergence.py → OperatorRegistry
Method:    check(value, threshold) → bool
```

To add a custom convergence operator, implement `ConvergenceOperator.check()`, add a `ConvergenceOp` enum member, and register with `OperatorRegistry.register()`.

## Data Flow

A typical pipeline execution follows this path:

```
1. CLI parses args, loads YAML → TaskConfig
2. StateManager creates or resumes PipelineState
3. Orchestrator enters DAG loop:
   a. DagScheduler.get_ready() → list of stage names
   b. For each ready stage (bounded by Semaphore):
      i.   StrategyRegistry.get(stage_type) → StageStrategy
      ii.  StageStrategy.execute(ctx, stage_cfg):
           - InputResolver resolves stage inputs
           - TemplateEngine renders the prompt
           - Runner.run() invokes CC CLI
           - ExtractorRegistry extracts outputs from response
           - ArtifactSaver persists results to disk
      iii. Orchestrator marks stage done/failed
   c. Loop until all stages done or stuck
4. CLI prints summary
```

## File Size Constraint

Every source file is kept under 200 lines. When a module grows beyond this limit, it is split along responsibility boundaries (e.g., `state/manager.py` was split into `manager.py` + `serialization.py` + `queries.py` + `transitions.py` + `recovery.py`).
