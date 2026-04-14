# Extending cc-pipeline

This guide explains how to add new backends, output types, stage strategies, and convergence operators to cc-pipeline.

All extension points follow the same pattern:

1. Implement an ABC
2. Register the implementation with the corresponding registry
3. (Optional) Add an enum member if introducing a new type discriminator

## Adding a New Runner Backend

The `Runner` ABC defines how CC is invoked. The default `ClaudeCliRunner` uses a subprocess, but you might want an API-based runner, a mock runner for testing, or a runner that routes to different models.

### Step 1: Implement the Runner ABC

```python
# cc_pipeline/runners/my_api_runner.py

from __future__ import annotations

from pathlib import Path

from ..state.models import RunResult, TokenUsage
from .base import Runner


class MyApiRunner(Runner):
    """Runner that calls Claude via a REST API."""

    def __init__(self, api_key: str, base_url: str):
        self._api_key = api_key
        self._base_url = base_url

    async def run(
        self,
        prompt: str,
        *,
        model: str = "claude-sonnet-4-5",
        cwd: Path | None = None,
        timeout: float | None = None,
        permission: str = "readonly",
        system_prompt: str | None = None,
        effort: str = "medium",
        max_budget_usd: float | None = None,
        fallback_model: str | None = None,
        session_id: str | None = None,
        persist_session: bool = False,
        session_name: str | None = None,
        add_dirs: list[Path] | None = None,
        allowed_tools: list[str] | None = None,
        skip_permissions: bool | None = None,
        extra_flags: list[str] | None = None,
    ) -> RunResult:
        # Your API call logic here
        import aiohttp

        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                f"{self._base_url}/messages",
                json={"model": model, "prompt": prompt},
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            )
            data = await resp.json()

        return RunResult(
            text=data["content"][0]["text"],
            token_usage=TokenUsage(
                input_tokens=data["usage"]["input_tokens"],
                output_tokens=data["usage"]["output_tokens"],
            ),
        )
```

### Step 2: Wire It In

In `cli/main.py::_compose()` (or your own composition code), replace the runner:

```python
# Instead of:
runner = ClaudeCliRunner(claude_bin or "claude")

# Use:
from cc_pipeline.runners.my_api_runner import MyApiRunner
runner = MyApiRunner(api_key=os.environ["API_KEY"], base_url="https://...")
```

No other changes needed — the runner is injected into all strategies via `StageContext`.

## Adding a New Output Extractor

Output extractors convert raw CC text responses into typed values. The built-in extractors handle `text`, `json`, `number`, and `file`. You might want to add extractors for YAML, CSV, regex patterns, etc.

### Step 1: Add an OutputType Enum Member

```python
# In cc_pipeline/constants/enums.py

@unique
class OutputType(str, Enum):
    TEXT = "text"
    JSON = "json"
    NUMBER = "number"
    FILE = "file"
    YAML = "yaml"        # ← Add new type
```

### Step 2: Implement the OutputExtractor ABC

```python
# cc_pipeline/extractors/yaml_extractor.py

from __future__ import annotations

import logging
from typing import Any

import yaml

from ..config.models import OutputSpec
from .base import OutputExtractor

logger = logging.getLogger(__name__)


class YamlExtractor(OutputExtractor):
    """Extract a YAML value from CC text output."""

    def extract(self, text: str, name: str, spec: OutputSpec) -> Any:
        # Try to find YAML in code fences
        import re
        match = re.search(r"```ya?ml\s*\n(.*?)```", text, re.DOTALL)
        if match:
            try:
                return yaml.safe_load(match.group(1))
            except yaml.YAMLError:
                pass

        # Try raw YAML
        try:
            return yaml.safe_load(text)
        except yaml.YAMLError:
            logger.warning("Failed to extract YAML for output %r", name)
            return None
```

### Step 3: Register the Extractor

In `extractors/base.py::build_default_registry()`:

```python
def build_default_registry() -> ExtractorRegistry:
    from .json_extractor import JsonExtractor
    from .number_extractor import NumberExtractor
    from .text_extractor import TextExtractor
    from .file_extractor import FileExtractor
    from .yaml_extractor import YamlExtractor    # ← Add import

    reg = ExtractorRegistry()
    reg.register(OutputType.JSON, JsonExtractor())
    reg.register(OutputType.NUMBER, NumberExtractor())
    reg.register(OutputType.TEXT, TextExtractor())
    reg.register(OutputType.FILE, FileExtractor())
    reg.register(OutputType.YAML, YamlExtractor())  # ← Register
    return reg
```

### Step 4: Use in YAML

```yaml
stages:
  my-stage:
    outputs:
      config: {type: yaml}
```

## Adding a New Stage Strategy

Stage strategies define how a stage type is executed. The built-in strategies are `SingleShotStrategy` (one CC invocation) and `LoopStrategy` (iterative with convergence). You might want a strategy that runs multiple CC calls in sequence, does A/B testing, or implements custom retry logic.

### Step 1: Add a StageType Enum Member

```python
# In cc_pipeline/constants/enums.py

@unique
class StageType(str, Enum):
    PRE_EXEC = "pre-exec"
    SUBTASK = "subtask"
    LOOP = "loop"
    POST_EXEC = "post-exec"
    MULTI_SHOT = "multi-shot"    # ← Add new type
```

### Step 2: Implement the StageStrategy ABC

```python
# cc_pipeline/strategies/multi_shot.py

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..config.models import StageConfig
from ..templates.engine import build_template_context
from .base import StageContext, StageStrategy

logger = logging.getLogger(__name__)


class MultiShotStrategy(StageStrategy):
    """Run a stage with multiple CC invocations and merge outputs."""

    def __init__(self, num_shots: int = 3):
        self._num_shots = num_shots

    async def execute(
        self, ctx: StageContext, stage_cfg: StageConfig,
    ) -> dict[str, Any]:
        resolved = ctx.artifacts.resolve_inputs(
            stage_cfg.inputs, ctx.config.working_dir,
        )
        upstream = {
            dep: ctx.artifacts.get_stage_outputs(dep)
            for dep in stage_cfg.depends_on
        }
        context = build_template_context(
            stage_name=ctx.stage_name,
            inputs=resolved,
            stage_outputs=upstream,
            config_values={"name": ctx.config.name},
        )
        prompt = ctx.templates.render(stage_cfg.prompt_template, context)

        # Run multiple shots
        all_outputs = []
        for i in range(self._num_shots):
            result = await ctx.runner.run(
                prompt=prompt,
                model=stage_cfg.model,
                effort=stage_cfg.effort,
                permission=stage_cfg.permissions.value,
                cwd=Path(ctx.config.working_dir),
            )
            outputs = ctx.extractors.extract_all(
                result.text, stage_cfg.outputs,
            )
            all_outputs.append(outputs)
            ctx.artifacts.save_result(
                ctx.stage_name, result,
                outputs=outputs, iteration=i,
            )

        # Merge: pick the most common value for each output key
        merged = self._merge(all_outputs)
        ctx.artifacts.save_result(ctx.stage_name, result, outputs=merged)
        return merged

    def _merge(
        self, all_outputs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        # Simple: take the last one (customize as needed)
        return all_outputs[-1] if all_outputs else {}
```

### Step 3: Register the Strategy

In `cli/main.py::_compose()`:

```python
from ..strategies.multi_shot import MultiShotStrategy

strategies.register(StageType.MULTI_SHOT, MultiShotStrategy(num_shots=3))
```

### Step 4: Use in YAML

```yaml
stages:
  consensus-analysis:
    type: multi-shot
    prompt_template: |
      Analyze this code for bugs...
```

**Important**: The strategy must return a `dict[str, Any]` of extracted outputs. The orchestrator handles marking the stage done/failed — strategies should NOT call `state_mgr.mark_stage_done()`.

## Adding a New Convergence Operator

Convergence operators evaluate whether a loop metric meets a threshold. The built-in operators are `>=`, `<=`, `>`, `<`, `==`. You might want percentage-based convergence, moving-average convergence, or custom statistical tests.

### Step 1: Add a ConvergenceOp Enum Member

```python
# In cc_pipeline/constants/enums.py

@unique
class ConvergenceOp(str, Enum):
    GE = ">="
    LE = "<="
    GT = ">"
    LT = "<"
    EQ = "=="
    WITHIN = "within"    # ← Add new operator
```

### Step 2: Implement the ConvergenceOperator ABC

```python
# cc_pipeline/strategies/within_operator.py

from __future__ import annotations

from .convergence import ConvergenceOperator


class WithinOperator(ConvergenceOperator):
    """Check if value is within threshold percent of 1.0.

    For example, threshold=0.05 checks if 0.95 <= value <= 1.05.
    """

    def check(self, value: float, threshold: float) -> bool:
        return abs(value - 1.0) <= threshold
```

### Step 3: Register the Operator

In `strategies/convergence.py::build_default_operators()`:

```python
from .within_operator import WithinOperator

def build_default_operators() -> OperatorRegistry:
    reg = OperatorRegistry()
    reg.register(ConvergenceOp.GE, ThresholdOperator(op_mod.ge))
    reg.register(ConvergenceOp.LE, ThresholdOperator(op_mod.le))
    reg.register(ConvergenceOp.GT, ThresholdOperator(op_mod.gt))
    reg.register(ConvergenceOp.LT, ThresholdOperator(op_mod.lt))
    reg.register(ConvergenceOp.EQ, ThresholdOperator(op_mod.eq))
    reg.register(ConvergenceOp.WITHIN, WithinOperator())  # ← Register
    return reg
```

### Step 4: Use in YAML

```yaml
stages:
  optimize:
    type: loop
    convergence:
      metric: "outputs.loss_ratio"
      threshold: 0.05
      operator: "within"
```

## General Extension Guidelines

1. **Keep files under 200 lines**. If your extension is large, split it into multiple modules.

2. **Use enums for new type discriminators**. Every new type value must be added to `constants/enums.py`.

3. **Follow the ABC + Registry pattern**. Don't add switch/case or if/elif chains.

4. **Strategies don't own lifecycle**. Strategies return outputs; the orchestrator handles `mark_stage_done()` / `mark_stage_failed()`.

5. **Test your extension**. Add unit tests in `cc_pipeline/tests/unit/` and integration tests in `cc_pipeline/tests/integration/`.

6. **Dependency direction**: New modules at a given layer may only import from the same layer or lower layers. Never import upward.
