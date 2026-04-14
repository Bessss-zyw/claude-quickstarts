# Design Decisions

This document explains the key design decisions and implementation details of cc-pipeline.

## ABC/Protocol + Registry Pattern

**Decision**: Use Abstract Base Classes (ABCs) paired with registries for all extension points (Runner, OutputExtractor, StageStrategy, ConvergenceOperator).

**Why not raw duck typing / Protocols**: ABCs provide explicit contracts with clear method signatures and docstrings. They fail fast at instantiation time (via `abstractmethod`) rather than at call time. For an internal framework where the extension points are well-defined and few, ABCs are simpler and more discoverable than Protocols.

**Why registries**: Registries decouple the "what types exist" from "who processes them." Adding a new `OutputType` requires only: (1) add the enum member, (2) implement the extractor, (3) one `register()` call. No switch/case statements to update, no import chains to modify.

**Pattern used everywhere**:
```python
# ABC defines the contract
class OutputExtractor(ABC):
    @abstractmethod
    def extract(self, text: str, name: str, spec: OutputSpec) -> Any: ...

# Registry maps discriminator → implementation
class ExtractorRegistry:
    def register(self, output_type: OutputType, extractor: OutputExtractor): ...
    def get(self, output_type: OutputType) -> OutputExtractor: ...

# Factory creates the default set
def build_default_registry() -> ExtractorRegistry:
    reg = ExtractorRegistry()
    reg.register(OutputType.JSON, JsonExtractor())
    reg.register(OutputType.NUMBER, NumberExtractor())
    ...
    return reg
```

## DAG Scheduler

**Algorithm**: The scheduler uses a simplified version of Kahn's algorithm at runtime:

1. `get_ready(dispatched)` scans all stages and returns those that are: (a) still pending, (b) not already dispatched, and (c) have all dependencies in DONE status.
2. Ready stages are dispatched as `asyncio.Task`s, bounded by a `Semaphore(max_parallel)`.
3. The main loop calls `asyncio.wait(FIRST_COMPLETED)` to collect finished tasks, then re-evaluates readiness.
4. `skip_blocked()` marks pending stages whose dependencies have FAILED as SKIPPED.
5. `check_stuck()` raises `PipelineStuckError` if there are pending stages but nothing is in-flight or ready.

**Why not pre-compute a topological order**: Pre-computing would require re-sorting after every stage completion and doesn't naturally support parallel dispatch. The runtime scan is O(stages) per iteration, which is negligible for typical pipeline sizes (5-20 stages).

**DAG validation** (at config load time) uses full Kahn's algorithm with adjacency lists and in-degree tracking to detect cycles in O(V+E).

## Crash Recovery

**Problem**: If the pipeline process is killed mid-execution, some stages/iterations will be left in RUNNING status. On resume, these would never be re-executed (they're not PENDING) and never complete (they're not DONE).

**Solution**: Two-part mechanism:

1. **Atomic state persistence**: Every state mutation is followed by `save()`, which writes to a temp file and then atomically renames via `os.replace()`. This ensures the state file is never half-written.

2. **Running → Pending recovery**: On `load_or_create(resume=True)`, `recover_running_stages()` scans all stages and iterations, resetting any RUNNING status back to PENDING. This allows them to be re-dispatched.

```python
def recover_running_stages(state: PipelineState) -> None:
    for ss in state.stages.values():
        if ss.status == StageStatus.RUNNING:
            ss.status = StageStatus.PENDING
            ss.started_at = None
        if ss.loop:
            for it in ss.loop.iterations.values():
                if it.status == IterationStatus.RUNNING:
                    it.status = IterationStatus.PENDING
                    it.started_at = None
```

**Resume behavior**: The `--resume` flag controls whether existing state is loaded. Without `--resume`, a fresh state is always created even if one exists. With `--resume`, existing state is loaded and recovered.

## Loop Convergence

Loop stages iterate until either:
- A convergence criterion is met (metric ≥/≤/>/</== threshold), or
- `max_iterations` is reached.

**Implementation**:

1. Each iteration runs a full CC invocation and extracts outputs.
2. The convergence metric is extracted from the outputs via dot-path notation (e.g., `"outputs.speedup"` extracts `outputs["speedup"]`).
3. The metric value is checked against the threshold using a registered `ConvergenceOperator`.
4. If converged, `mark_loop_converged(stage, value)` records the convergence in state and the loop exits.
5. Previous iteration outputs are injected into the next iteration's template context under `{{loop.previous.outputs.X}}`.

**Crash recovery for loops**: `_find_resume_point(loop)` scans iteration states and returns the index of the first iteration that is not DONE. This allows a resumed pipeline to skip already-completed iterations.

## Template Engine

**Decision**: Use a custom minimal template engine instead of Jinja2.

**Why not Jinja2**:
- cc-pipeline prompts need only variable interpolation and simple conditionals.
- Jinja2 is a large dependency with many features (inheritance, macros, filters) that add complexity without value for prompt templates.
- The custom engine is ~110 lines, zero external dependencies, and trivially auditable.
- Prompt rendering should be predictable — no risk of Jinja2 auto-escaping, whitespace control, or template inheritance surprising users.

**Supported syntax**:

| Syntax | Description | Example |
|--------|-------------|---------|
| `{{inputs.X}}` | Stage input value | `{{inputs.source_file}}` |
| `{{stages.X.outputs.Y}}` | Upstream stage output | `{{stages.analyze.outputs.targets}}` |
| `{{loop.iteration}}` | Current loop iteration (0-based) | `Iteration: {{loop.iteration}}` |
| `{{loop.previous.outputs.X}}` | Previous iteration output | `{{loop.previous.outputs.speedup}}` |
| `{{#if VAR}}...{{/if}}` | Conditional block | `{{#if loop.previous}}Previous: ...{{/if}}` |
| `{{env.X}}` | Environment variable | `{{env.HOME}}` |
| `{{config.X}}` | Task config value | `{{config.name}}` |

**Value formatting**: Strings pass through directly. Numbers are stringified. Dicts and lists are JSON-formatted with `indent=2`. Unresolved variables produce `{{UNRESOLVED:path}}` with a logged warning.

## Dependency Injection

**Composition root**: `cli/main.py::_compose()` is the single location where all components are instantiated and wired together. No component creates its own dependencies.

```python
def _compose(config, task_path, claude_bin, resume):
    # 1. Create leaf components
    runner = ClaudeCliRunner(claude_bin or "claude")
    state_mgr = StateManager(pipeline_dir)
    artifacts = ArtifactStore(pipeline_dir)
    templates = TemplateEngine()
    extractors = build_default_registry()

    # 2. Build strategy registry
    strategies = StrategyRegistry()
    strategies.register(StageType.PRE_EXEC, SingleShotStrategy())
    strategies.register(StageType.LOOP, LoopStrategy(operators))
    ...

    # 3. Context factory (closure captures all components)
    def ctx_factory(stage_name):
        return StageContext(
            stage_name=stage_name, config=config,
            runner=runner, artifacts=artifacts,
            state_mgr=state_mgr, templates=templates,
            extractors=extractors,
        )

    # 4. Compose orchestrator
    return PipelineOrchestrator(
        config=config, state_mgr=state_mgr,
        strategies=strategies, ctx_factory=ctx_factory,
    ), state_mgr
```

**Why closures instead of a DI container**: The dependency graph is small and static. A closure-based factory is simpler and more transparent than a generic container. The `StageContext` dataclass serves as the "dependency bag" passed to strategies, making all dependencies explicit.

**Testing benefit**: Integration tests can swap in mock runners without touching any production code:

```python
class MockRunner(Runner):
    async def run(self, prompt, **kwargs) -> RunResult:
        return RunResult(text='{"result": "mock"}')
```

## Permission Model

CC CLI permissions are mapped from three semantic levels to concrete CLI flags:

| PermissionLevel | CC CLI Effect |
|----------------|---------------|
| `readonly` | `--allowedTools Read,Glob,Grep,WebSearch,WebFetch,mcp__*` |
| `write` | `--allowedTools Read,Write,Edit,Glob,Grep,WebSearch,WebFetch` |
| `full` | `--dangerouslySkipPermissions` |

The mapping is defined in `runners/command_builder.py::_PERMISSION_MAP` and can be overridden per-stage via `allowed_tools` or `skip_permissions` in the stage config.

## Prompt Delivery

Prompts are delivered to CC via stdin pipe (`proc.communicate(input=prompt.encode())`) rather than as a command-line argument. This avoids the OS `E2BIG` error that occurs when prompts exceed the argument length limit (~128KB on Linux).

## Enum-Only Constants

**Rule**: Every string literal used as a type discriminator, status value, or config key must be defined as an enum member in `constants/enums.py` and referenced by that member — never by raw string.

**Enforcement**: `str(Enum)` subclasses ensure enum members are valid strings for JSON serialization while preventing typos and enabling IDE auto-completion. New values require explicit enum additions, making the change visible in version control.

## Token Usage Tracking

`TokenUsage` is a mutable dataclass with `accumulate(other)` for aggregation and `from_dict()` / `to_dict()` for serialization. Token usage flows upward:

1. `ndjson_parser` accumulates tokens from NDJSON events into `RunResult.token_usage`.
2. `StateManager.mark_iteration_done()` / `mark_stage_done()` accumulates into `PipelineState.total_token_usage`.
3. `print_pipeline_summary()` displays the total.
