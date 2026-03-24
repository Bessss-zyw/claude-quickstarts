# Universal Agent Harness — Implementation Specification

> 本文档从 DESIGN.md 中提取所有可直接编码的规格。
> 每个模块包含：接口签名、数据结构、关键逻辑伪代码、边界条件。
> 实现时应将本文档作为 **唯一参考**，无需反复翻阅 DESIGN.md。

---

## 0. 全局约定

| 项目 | 值 |
|------|-----|
| 语言 | Python 3.10+ |
| 外部依赖 | `openai`（任意 OpenAI-compatible provider）, `pyyaml` |
| 状态存储 | 纯文件系统，无数据库 |
| 并行策略 | v1 串行执行所有 pending tasks；并行为 v2 保留 |

---

## 1. 目录结构与文件布局

```
universal-agent-harness/
├── main.py                  # CLI 入口
├── coordinator.py           # Coordinator 主循环
├── agent_runner.py          # 通用 ReAct loop
├── agent_registry.py        # 从 task.yaml 加载 agent 定义
├── client.py                # OpenAI-compatible LLM client
├── message_bus.py           # 文件 based agent 间通信
├── tools/
│   ├── __init__.py          # 工具注册表 + tool schema 生成
│   ├── bash.py              # bash 命令执行
│   ├── filesystem.py        # read/write/edit/list/search file
│   ├── planning.py          # create_plan / update_plan / get_plan
│   ├── messaging.py         # assign_task / check_result / check_all_results
│   └── journal.py           # write_journal
├── middleware/
│   ├── __init__.py
│   ├── loop_detector.py
│   └── token_tracker.py
├── security.py              # 路径白名单 + bash 安全检查
├── config.py                # 全局配置
└── requirements.txt         # openai, pyyaml
```

### 运行时目录（`working_dir` 下自动创建）

```
{working_dir}/
├── .harness/
│   ├── plan.json                  # 执行计划
│   ├── journals/
│   │   └── {agent_name}.md        # 每个 agent 一个 journal
│   ├── messages/
│   │   └── msg_{NNN}.json         # 任务消息
│   ├── session_log.jsonl          # session 元信息（自动写入）
│   ├── token_usage.json           # 按 agent 累计 token
│   └── journal_archive/           # journal 压缩备份
├── workspace/
│   └── {agent_name}/              # 每个 specialist agent 的中间产物目录
└── output/                        # 最终 deliverables
```

---

## 2. task.yaml 解析规格

### 2.1 完整 Schema

```yaml
# --- Part 1: 任务描述 ---
name: str                          # 必填，任务名
goal: str                          # 必填，多行文本
context: str                       # 可选，背景信息
deliverables:                      # 必填，至少 1 项
  - path: str                      #   相对于 working_dir/output/
    description: str
environment:
  shell_preamble: list[str]        # 可选，每条 bash 前自动执行
  working_dir: str                 # 必填，harness 工作目录
  allowed_paths: list[str]         # 可选，agent 可访问的外部绝对路径

# --- Part 2: Agent 团队 ---
agents:
  coordinator:                     # 必须存在且 key 必须是 "coordinator"
    role: str                      # system prompt 中的角色描述
    model:
      provider: str                # "nvidia" | "openai" | "deepseek" | 其他
      name: str                    # 模型标识
    tools: list[str]               # 工具白名单
  {agent_name}:                    # 0..N 个 specialist
    role: str
    model: { provider: str, name: str }
    tools: list[str]
```

### 2.2 解析逻辑要点

- `coordinator` 是保留 key，必须存在。其他 agent name 由用户自定义。
- `coordinator.tools` 不应包含 `bash`、`write_file`、`edit_file`，加载时应**校验并警告**（但不阻止，以保持灵活）。
- 所有 agent 无论是否在 `tools` 中声明，都**自动获得** `write_journal` 工具。
- `environment.working_dir` 如果是相对路径，解析为相对于 `task.yaml` 所在目录。

---

## 3. 模块规格

### 3.1 `config.py` — 全局配置

```python
@dataclass
class HarnessConfig:
    task_file: str                       # task.yaml 路径
    working_dir: str                     # 解析后的绝对路径
    max_iterations: int = 20             # coordinator 最大循环次数
    journal_compress_threshold: int = 3000  # 字符数，超过则触发压缩
    harness_dir: str = ".harness"        # 相对于 working_dir
    workspace_dir: str = "workspace"     # 相对于 working_dir
    output_dir: str = "output"           # 相对于 working_dir
```

### 3.2 `client.py` — LLM Client

封装 `openai.OpenAI`，支持多 provider。

```python
class LLMClient:
    """OpenAI-compatible client，对每个 agent 的 model 配置初始化。"""

    def __init__(self, provider: str, model_name: str):
        """
        根据 provider 设置 base_url 和 api_key。
        provider 映射规则:
          - "nvidia"  → base_url 从环境变量 NVIDIA_BASE_URL 读取,
                         api_key 从 NVIDIA_API_KEY 读取
          - "openai"  → 默认 openai 行为
          - "deepseek" → base_url = "https://api.deepseek.com",
                         api_key 从 DEEPSEEK_API_KEY 读取
          - 其他      → 尝试从 {PROVIDER}_BASE_URL / {PROVIDER}_API_KEY 环境变量读取
        """

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.0,
    ) -> dict:
        """
        调用 LLM，返回完整 response。
        处理 tool_calls 的解析。
        """
```

**关键**：不同 agent 可能使用不同 provider/model，所以 `LLMClient` 是 per-agent 实例化的。

### 3.3 `agent_registry.py` — Agent 注册表

```python
@dataclass
class AgentDefinition:
    name: str                       # "coordinator", "coder", "profiler", ...
    role: str                       # system prompt 角色描述
    model_provider: str
    model_name: str
    tool_names: list[str]           # 工具白名单（从 task.yaml 读取）
    is_coordinator: bool            # name == "coordinator"

class AgentRegistry:
    def __init__(self, task_yaml_path: str):
        """解析 task.yaml，创建所有 AgentDefinition。"""

    def get_agent(self, name: str) -> AgentDefinition: ...
    def get_specialists(self) -> list[AgentDefinition]: ...
    def get_coordinator(self) -> AgentDefinition: ...
    def list_agent_names(self) -> list[str]: ...
```

### 3.4 `agent_runner.py` — 通用 ReAct Loop

所有 agent（coordinator 和 specialist）都通过同一个 runner 执行。

```python
class AgentRunner:
    """通用 ReAct loop：message → LLM → tool_call → execute → LLM → ... → final text."""

    def __init__(
        self,
        agent_def: AgentDefinition,
        client: LLMClient,
        tool_registry: ToolRegistry,
        config: HarnessConfig,
    ): ...

    def run(self, system_prompt: str, user_message: str) -> AgentResult:
        """
        执行一个完整的 agent session。

        流程:
        1. 构造 messages = [system, user]
        2. loop:
            a. 调用 client.chat(messages, tools=self.tool_schemas)
            b. if response 包含 tool_calls:
               - 对每个 tool_call 执行对应工具
               - 将 tool result 追加到 messages
               - 经过 middleware 检查（loop detection, token tracking）
               - continue loop
            c. if response 是纯 text:
               - 这是 agent 的最终回复
               - break
        3. 返回 AgentResult

        Returns:
            AgentResult: 包含 final_text（最终回复）和 metadata
        """

@dataclass
class AgentResult:
    agent_name: str
    final_text: str                # agent 的最终文本回复（specialist 的摘要）
    tool_calls_count: int
    token_usage: dict              # {"prompt_tokens": N, "completion_tokens": N}
    error: str | None = None
```

**ReAct Loop 细节**：

- 每个 session 是 **fresh context**（不携带前次对话 messages）。
- 跨 session 记忆通过 journal（注入 system prompt）实现。
- Loop 内无硬编码最大轮次，由 middleware `loop_detector` 控制。
- `tool_calls` 严格按 OpenAI function calling 格式处理。

### 3.5 `coordinator.py` — Coordinator 主循环

```python
class CoordinatorLoop:
    """
    顶层循环，驱动整个任务执行。

    伪代码:
    iteration = 0
    while True:
        iteration += 1

        # --- Phase 1: 运行 Coordinator Session ---
        system_prompt = build_coordinator_system_prompt(...)
        user_message = build_coordinator_user_message(iteration)
        result = agent_runner.run(system_prompt, user_message)

        # --- Phase 2: 收集 pending tasks ---
        pending_tasks = message_bus.get_pending_tasks()
        if not pending_tasks:
            # coordinator 没有下发新任务
            if is_all_done():
                break  # 任务完成
            else:
                # 异常：coordinator 没做任何有效操作
                # 可能需要 re-prompt 或 abort

        # --- Phase 3: 串行执行 specialist agents ---
        for task_msg in pending_tasks:
            agent_name = task_msg["to"]
            task_desc = task_msg["content"]

            system_prompt = build_specialist_system_prompt(agent_name, task_desc)
            user_message = "You have been assigned a task by the coordinator. Execute it now."
            result = agent_runner.run(system_prompt, user_message)

            # 将 result.final_text 写回 message bus
            message_bus.complete_task(task_msg["id"], result.final_text)

        # --- Phase 4: 检查终止条件 ---
        if check_stop_conditions(iteration):
            break
    """

    def __init__(self, config: HarnessConfig, registry: AgentRegistry): ...

    def run(self) -> None:
        """主入口。"""

    def build_coordinator_system_prompt(self) -> str:
        """拼接 coordinator 的 system prompt，包含:
        - 固定角色描述
        - task.yaml 的 name/goal/context/deliverables
        - 团队成员列表（每人一行：name + role 摘要）
        - 可用工具列表
        - Decision History（coordinator journal 内容）
        - 行为规则
        """

    def build_specialist_system_prompt(self, agent_name: str, task_desc: str) -> str:
        """拼接 specialist 的 system prompt，包含:
        - 角色描述（来自 task.yaml）
        - 当前任务描述（来自 message bus）
        - Work History（该 agent 的 journal 内容）
        - 可用工具列表 + write_journal
        - 文件路径指引（workspace/{name}/, output/, allowed_paths）
        - 行为规则
        """

    def build_coordinator_user_message(self, iteration: int) -> str:
        """
        iteration == 1:
            "New session. No plan exists yet.
             Read the task description, create a plan, and assign the first batch of tasks."
        iteration > 1:
            "New session. Check completed task results and current plan status.
             Update the plan, then assign next tasks or verify deliverables."
        """

    def check_stop_conditions(self, iteration: int) -> bool:
        """
        返回 True 表示应该停止。检查:
        1. plan.json 所有 steps 的 status == "done"
        2. iteration >= max_iterations
        3. coordinator 最终回复包含 "TASK_COMPLETE"
        """

    def is_all_done(self) -> bool:
        """读取 plan.json，检查是否所有 step 都是 done。"""
```

### 3.6 `message_bus.py` — Agent 间通信

```python
class MessageBus:
    """文件 based 消息系统。每条消息是一个独立的 JSON 文件。"""

    def __init__(self, harness_dir: str): ...

    # --- 写入侧（coordinator 通过 messaging tools 调用）---

    def create_task(self, from_agent: str, to_agent: str, content: str) -> str:
        """
        创建一条 task_assignment 消息。
        返回 task_id (如 "msg_001")。

        消息文件格式 (.harness/messages/msg_NNN.json):
        {
            "id": "msg_NNN",
            "from": "coordinator",
            "to": "profiler",
            "type": "task_assignment",
            "content": "具体任务描述...",
            "status": "pending",
            "result": null,
            "created_at": "ISO timestamp",
            "completed_at": null
        }

        NNN 是自增序号，基于目录中已有文件数量。
        """

    def complete_task(self, task_id: str, result: str) -> None:
        """
        将 task 状态改为 "done"，写入 result。
        同时记录 completed_at timestamp。
        """

    # --- 读取侧 ---

    def get_pending_tasks(self) -> list[dict]:
        """返回所有 status=="pending" 的消息，按 id 排序。"""

    def get_task(self, task_id: str) -> dict:
        """读取指定消息。"""

    def get_all_tasks(self) -> list[dict]:
        """返回所有消息，按 id 排序。"""
```

### 3.7 `tools/__init__.py` — 工具注册表

```python
class ToolRegistry:
    """
    管理所有可用工具。
    根据 agent 的 tool_names 白名单生成该 agent 可用的 tool schema 列表。
    """

    def __init__(self, config: HarnessConfig, message_bus: MessageBus): ...

    def get_tool_schemas(self, tool_names: list[str]) -> list[dict]:
        """
        返回 OpenAI function calling 格式的 tool definitions。
        每个 tool 的 schema 格式:
        {
            "type": "function",
            "function": {
                "name": "tool_name",
                "description": "...",
                "parameters": {
                    "type": "object",
                    "properties": {...},
                    "required": [...]
                }
            }
        }
        """

    def execute_tool(
        self, tool_name: str, arguments: dict, agent_name: str
    ) -> str:
        """
        执行工具并返回结果字符串。
        agent_name 用于:
        - write_journal: 确定写入哪个 journal 文件
        - 文件工具: 路径安全检查
        - bash: 注入 shell_preamble
        """
```

### 3.8 各工具详细规格

#### 3.8.1 `tools/bash.py`

```python
def bash(command: str, timeout: int = 120) -> str:
    """
    执行 bash 命令。

    Tool Schema:
        name: "bash"
        parameters:
            command: str    # 必填
            timeout: int    # 可选，默认 120 秒

    执行逻辑:
    1. 如果 task.yaml 定义了 shell_preamble，在 command 前拼接
       (例: "conda activate cutile && {command}")
    2. 通过 subprocess 执行，捕获 stdout + stderr
    3. 超时则 kill 进程，返回超时错误
    4. 返回格式: "exit_code: {N}\nstdout:\n{...}\nstderr:\n{...}"
    5. 输出截断: 如果 stdout/stderr 超过 10000 字符，只保留前 5000 + 后 5000

    安全检查 (由 security.py 实现):
    - 检查命令是否包含危险操作 (rm -rf /, 写系统文件等)
    - 可配置白名单/黑名单模式
    """
```

#### 3.8.2 `tools/filesystem.py`

共 5 个工具，全部受路径安全检查约束。

```python
def read_file(path: str) -> str:
    """
    Tool Schema:
        name: "read_file"
        parameters:
            path: str       # 绝对路径或相对路径（相对于 working_dir）

    逻辑:
    - 路径安全检查 (is_path_allowed)
    - 读取并返回文件内容
    - 文件不存在返回错误
    - 大文件 (>100KB) 截断并提示
    """

def write_file(path: str, content: str) -> str:
    """
    Tool Schema:
        name: "write_file"
        parameters:
            path: str
            content: str

    逻辑:
    - 路径安全检查
    - 自动创建父目录
    - 写入内容
    - 返回 "Written N bytes to {path}"
    """

def edit_file(path: str, old_text: str, new_text: str) -> str:
    """
    Tool Schema:
        name: "edit_file"
        parameters:
            path: str
            old_text: str    # 要替换的文本（必须在文件中唯一匹配）
            new_text: str

    逻辑:
    - 路径安全检查
    - 读取文件内容
    - 查找 old_text，如果不存在或有多个匹配，返回错误
    - 替换为 new_text
    - 写回文件
    - 返回 "Edited {path}: replaced {len(old_text)} chars"
    """

def list_files(path: str, recursive: bool = False) -> str:
    """
    Tool Schema:
        name: "list_files"
        parameters:
            path: str           # 目录路径
            recursive: bool     # 可选，默认 False

    逻辑:
    - 路径安全检查
    - 列出目录内容
    - recursive=True 时递归列出
    - 返回文件列表，每行一个路径
    """

def search_files(path: str, pattern: str, file_glob: str = "*") -> str:
    """
    Tool Schema:
        name: "search_files"
        parameters:
            path: str           # 搜索起始目录
            pattern: str        # 正则或文本模式
            file_glob: str      # 可选，文件名 glob 过滤

    逻辑:
    - 路径安全检查
    - 在指定目录下搜索文件内容
    - 返回匹配行 + 文件名 + 行号
    - 结果过多时截断
    """
```

#### 3.8.3 `tools/planning.py`

```python
def create_plan(steps: list[dict]) -> str:
    """
    Tool Schema:
        name: "create_plan"
        parameters:
            steps: list[object]
                每个 step: { "description": str, "assigned_to": str }

    逻辑:
    - 如果 plan.json 已存在，返回错误（不能重复创建，应使用 update_plan）
    - 为每个 step 自动分配递增 id，初始 status = "pending"，findings = ""
    - 写入 .harness/plan.json
    - 返回 "Created plan with N steps"

    plan.json 格式:
    {
        "steps": [
            {
                "id": 1,
                "description": "...",
                "assigned_to": "profiler",
                "status": "pending",    # pending | in_progress | done | failed
                "findings": ""
            },
            ...
        ],
        "created_at": "ISO timestamp",
        "updated_at": "ISO timestamp"
    }
    """

def update_plan(step_id: int, status: str = None, findings: str = None) -> str:
    """
    Tool Schema:
        name: "update_plan"
        parameters:
            step_id: int
            status: str          # 可选: "pending" | "in_progress" | "done" | "failed"
            findings: str        # 可选: 记录发现/结论

    逻辑:
    - 读取 plan.json
    - 找到指定 step_id
    - 更新提供的字段
    - 更新 updated_at timestamp
    - 写回文件
    - 返回 "Updated step {id}: status={status}"
    """

def get_plan() -> str:
    """
    Tool Schema:
        name: "get_plan"
        parameters: (无)

    逻辑:
    - 读取 plan.json
    - 如果不存在，返回 "No plan exists yet."
    - 存在则返回格式化的计划文本:
      "Step 1 [done] (profiler): Baseline benchmark
         Findings: FA2: 1.23ms ± 0.02, POD: 1.60ms ± 0.03
       Step 2 [in_progress] (coder): Optimize memory access
         Findings: (none)
       ..."
    """
```

#### 3.8.4 `tools/messaging.py`

Coordinator 专用工具。底层调用 `MessageBus`。

```python
def assign_task(agent: str, task_description: str) -> str:
    """
    Tool Schema:
        name: "assign_task"
        parameters:
            agent: str                 # 目标 agent name（必须是已注册的 specialist）
            task_description: str      # 任务描述

    逻辑:
    - 校验 agent 是否在 registry 中且不是 coordinator
    - 调用 message_bus.create_task(from="coordinator", to=agent, content=task_description)
    - 返回 "Assigned task {task_id} to {agent}"
    """

def check_result(task_id: str) -> str:
    """
    Tool Schema:
        name: "check_result"
        parameters:
            task_id: str

    逻辑:
    - 读取 message bus 中的消息
    - 返回格式化结果:
      "Task {id} [{status}] → {agent}:
       Content: {task description 前 200 字符}
       Result: {result 或 'pending'}"
    """

def check_all_results() -> str:
    """
    Tool Schema:
        name: "check_all_results"
        parameters: (无)

    逻辑:
    - 读取所有消息
    - 返回所有任务的格式化摘要列表
    """
```

#### 3.8.5 `tools/journal.py`

```python
def write_journal(content: str) -> str:
    """
    Tool Schema:
        name: "write_journal"
        parameters:
            content: str        # Markdown 格式的工作记录

    逻辑:
    - 确定 journal 文件路径: .harness/journals/{agent_name}.md
    - 自动添加 session header:
      "## Session {N} ({ISO timestamp})\n"
      其中 N 基于文件中已有的 "## Session" 数量 + 1
    - 追加 content 到文件末尾
    - 返回 "Journal updated"

    Journal 压缩逻辑（由 harness 在 session 开始前检查）:
    - 如果 journal 长度 > journal_compress_threshold (默认 3000 字符)
    - 将当前内容备份到 .harness/journal_archive/{agent_name}_{timestamp}.md
    - 用 LLM 压缩，保留:
      1. 关键决策和推理
      2. 已尝试但失败的方案（高优先保留）
      3. 关键数据点和数值
      4. 去掉冗余叙述和重复信息
    - 压缩后的内容替换原文件
    """
```

---

## 4. Prompt 模板

### 4.1 Coordinator System Prompt

```
You are the coordinator of a multi-agent team.

## Task
Name: {task.name}
Goal: {task.goal}
Context: {task.context}

## Deliverables
{for each deliverable:}
- {d.path}: {d.description}

## Your Team
{for each specialist agent:}
- {agent.name}: {agent.role 的首句或前 100 字符}

## Your Tools
- get_plan: read the current execution plan
- create_plan(steps): create a new execution plan
- update_plan(step_id, status, findings): update a plan step
- assign_task(agent, task_description): assign a task to a specialist
- check_result(task_id): check the result of a specific task
- check_all_results(): check all task statuses
- write_journal(content): record your decisions for future sessions
- read_file(path): read a file

## Your Decision History
{.harness/journals/coordinator.md 的内容，首次为空字符串}

## Rules
1. You do NOT execute tasks yourself. Always assign to a specialist.
2. First session: create a plan. Assign the first tasks.
3. Subsequent sessions: check results, update plan, assign next tasks.
4. Keep task descriptions clear and specific. Include:
   - What to do
   - What files to read/write
   - What output format you expect
   - Maximum 3-5 sentences summary required at the end
5. When all plan steps are done, verify all deliverables exist.
6. BEFORE ENDING EVERY SESSION: call write_journal to record:
   - What you decided this session and why
   - Key data points that informed your decision
   - Backup plans if current approach fails
   This is critical — your next session has NO memory of this conversation.
```

### 4.2 Specialist System Prompt

```
You are {agent_name}, a specialist agent in a team.

## Your Role
{agent.role from task.yaml}

## Your Task
{message bus 中的 task_description}

## Your Work History
{.harness/journals/{agent_name}.md 的内容，首次为空字符串}

## File Organization
- Source code: use absolute paths directly (e.g. {allowed_paths 示例})
- Intermediate files: save to workspace/{agent_name}/
- Final deliverables: save to output/{path}
- Your journal: use write_journal tool (do NOT write to .harness/ directly)

## Accessible External Paths
{列出 task.yaml 中 allowed_paths，每行一个}

You can read and write files at these paths using their absolute paths.
Files outside these paths and outside the working directory will be blocked.

## Available Tools
{列出该 agent 的 tools}
- write_journal(content): record what you did for future sessions

## Rules
1. Execute the task described above.
2. Save all outputs to files in the working directory.
3. BEFORE ENDING: call write_journal to record:
   - What you did and what files you changed
   - What worked and what didn't (especially failed attempts)
   - Any useful observations for future sessions
4. Provide a concise summary (3-5 sentences) as your final message.
   This summary will be sent back to the coordinator.
```

### 4.3 Session User Messages

| 场景 | 消息内容 |
|------|----------|
| Coordinator 首次 | `New session. No plan exists yet. Read the task description, create a plan, and assign the first batch of tasks.` |
| Coordinator 后续 | `New session. Check completed task results and current plan status. Update the plan, then assign next tasks or verify deliverables.` |
| Specialist | `You have been assigned a task by the coordinator. Execute it now.` |

---

## 5. 安全机制

### 5.1 路径安全检查 (`security.py`)

```python
def is_path_allowed(path: str, config: HarnessConfig) -> bool:
    """
    判断路径是否在允许范围内。

    允许:
    1. working_dir 及其所有子目录
    2. task.yaml 中 allowed_paths 声明的路径及其子目录

    实现:
    - 对 path 做 os.path.realpath 解析符号链接
    - 依次检查是否以 working_dir 或任一 allowed_path 开头
    - 全部不匹配则拒绝

    所有文件工具 (read_file, write_file, edit_file, list_files, search_files)
    在执行前必须调用此函数。
    """
```

### 5.2 Bash 安全检查

- 维护一个危险模式黑名单（`rm -rf /`, `chmod 777`, `curl | bash` 等）
- 命令匹配到危险模式时拒绝执行，返回错误信息
- 具体黑名单可配置

---

## 6. Middleware

### 6.1 `loop_detector.py`

```python
class LoopDetector:
    """
    检测单个 agent session 内是否陷入重复循环。

    检测方式:
    - 记录最近 N 次 tool call 的 (tool_name, arguments_hash)
    - 如果连续 3+ 次调用完全相同，触发 loop 告警
    - 告警方式: 在下一次 LLM 调用的 messages 中注入提示:
      "WARNING: You appear to be repeating the same action. Try a different approach."
    - 如果注入后仍然重复 2 次，强制结束该 session，返回错误
    """
```

### 6.2 `token_tracker.py`

```python
class TokenTracker:
    """
    按 agent 追踪 token 消耗。

    每次 LLM 调用后:
    - 从 response.usage 提取 prompt_tokens, completion_tokens
    - 累加到该 agent 的计数器
    - 定期（每个 session 结束）写入 .harness/token_usage.json

    token_usage.json 格式:
    {
        "coordinator": {"prompt_tokens": N, "completion_tokens": N, "total_cost_usd": N},
        "profiler":    {"prompt_tokens": N, "completion_tokens": N, "total_cost_usd": N},
        ...
        "total":       {"prompt_tokens": N, "completion_tokens": N, "total_cost_usd": N}
    }
    """
```

---

## 7. `main.py` — CLI 入口

```python
"""
用法: python main.py --task task.yaml [--max-iterations 20] [--resume]

参数:
  --task          task.yaml 路径（必填）
  --max-iterations  coordinator 最大循环次数（默认 20）
  --resume        从上次中断处恢复（默认行为，检测 .harness/ 是否存在）

启动流程:
1. 解析命令行参数
2. 加载 task.yaml → HarnessConfig
3. 创建运行时目录 (.harness/, workspace/, output/)
   - 如果 .harness/ 已存在且 --resume，跳过初始化
4. 初始化 AgentRegistry
5. 初始化 MessageBus
6. 初始化 ToolRegistry
7. 创建 CoordinatorLoop
8. 注册 SIGINT handler（Ctrl+C 优雅退出，保存当前状态）
9. 运行 coordinator_loop.run()
10. 输出最终摘要（token 用量、deliverables 状态）
"""
```

---

## 8. 执行流程 Summary

```
main.py
  │
  ├─ 解析 task.yaml
  ├─ 初始化各模块
  │
  └─ CoordinatorLoop.run()
       │
       ├─ iteration 1:
       │   ├─ AgentRunner.run(coordinator, "create plan...")
       │   │   └─ coordinator 调用 create_plan + assign_task × N
       │   ├─ message_bus.get_pending_tasks() → [task1, task2, ...]
       │   └─ for each task:
       │       └─ AgentRunner.run(specialist, task.content)
       │           └─ specialist 执行任务 → message_bus.complete_task(result)
       │
       ├─ iteration 2:
       │   ├─ AgentRunner.run(coordinator, "check results...")
       │   │   └─ coordinator 调用 check_all_results + update_plan + assign_task
       │   └─ dispatch & execute new pending tasks...
       │
       ├─ ...
       │
       └─ check_stop_conditions() → True → exit
```

---

## 9. 工具分配矩阵（快速参考）

| 工具 | Coordinator | Specialist | 备注 |
|------|:-----------:|:----------:|------|
| `get_plan` | Y | N | |
| `create_plan` | Y | N | |
| `update_plan` | Y | N | |
| `assign_task` | Y | N | |
| `check_result` | Y | N | |
| `check_all_results` | Y | N | |
| `write_journal` | Y | Y | 每个 agent 写自己的 journal |
| `read_file` | Y | Y | coordinator 仅用于检查 deliverables |
| `bash` | **N** | Y | coordinator 禁止直接执行 |
| `write_file` | N | Y | |
| `edit_file` | N | Y | |
| `list_files` | N | Y | |
| `search_files` | N | Y | |

---

## 10. 关键设计约束（实现 Checklist）

1. **Coordinator 不能 bash** — 即使 task.yaml 中误配了 bash，运行时也应拦截并警告。
2. **每个 session 是 fresh context** — AgentRunner.run() 不保留前次 messages，跨 session 记忆仅靠 journal。
3. **Coordinator 只看摘要** — specialist 的 `result.final_text`（3-5 句）是 coordinator 唯一能看到的信息，不传 raw 输出。
4. **write_journal 是所有 agent 的隐式工具** — 无论 task.yaml 是否声明，都自动注入。
5. **路径安全是硬约束** — 所有文件工具必须过 `is_path_allowed`，无例外。
6. **plan.json 的 all-done 是终止条件** — harness 每轮检查，但也支持 coordinator 显式说 "TASK_COMPLETE"。
7. **Journal 压缩** — 超过 3000 字符时自动压缩，失败记录优先保留。
8. **消息 ID 自增** — `msg_001`, `msg_002`, ... 基于 messages/ 目录现有文件数量。
9. **可中断可恢复** — 所有状态在文件系统，再次运行自动 resume（检测 .harness/ 存在）。
10. **Shell preamble** — 每条 bash 命令前拼接 `environment.shell_preamble` 中的命令。
