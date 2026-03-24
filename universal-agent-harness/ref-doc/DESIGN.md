# Universal Agent Harness — Design Document (v2)

## 1. 目标

一个轻量、通用、模型无关的 **multi-agent harness**。
能处理任意类型的长任务：写代码、性能调优、写 blog、作图、数据分析、运维等。

### 核心理念

**Coordinator + Specialist Agents**：一个 coordinator 负责规划和调度，多个 specialist agents 负责执行。每个 agent 有独立的 context、工具集和模型配置，由用户在配置文件中定义。

### 非目标

- 不做 UI / Web 界面
- 不做 agent marketplace
- 不做多用户 / 多租户

### 设计原则

- **纯 Python**，唯一外部依赖是 `openai` SDK + `pyyaml`
- **模型无关**：每个 agent 可以用不同的模型（Claude 做规划，DeepSeek 写代码，GPT 做 review）
- **任务无关**：通过 `task.yaml` 描述任务和 agent 团队，harness 核心不感知任务类型
- **文件即状态**：所有状态持久化在文件系统，不依赖数据库
- **可中断可恢复**：Ctrl+C 后再次运行，从上次状态继续

---

## 2. 架构

```
universal-agent-harness/
├── main.py                  # CLI 入口
├── coordinator.py           # ★ Coordinator agent 循环
├── agent_runner.py          # ★ 通用 agent 执行器（ReAct loop）
├── agent_registry.py        # ★ 从 task.yaml 加载 agent 定义
├── client.py                # OpenAI-compatible LLM client（多 provider）
├── message_bus.py           # ★ agent 间通信（文件 based）
├── tools/                   # 工具系统（可插拔）
│   ├── __init__.py          # 工具注册表
│   ├── bash.py              # bash 命令执行
│   ├── filesystem.py        # read/write/edit/list/search file
│   ├── planning.py          # create_plan / update_plan / get_plan
│   ├── messaging.py         # ★ assign_task / check_result（coordinator 专用）
│   └── journal.py           # ★ write_journal（coordinator 决策历史）
├── middleware/               # 中间件系统
│   ├── __init__.py
│   ├── loop_detector.py
│   └── token_tracker.py
├── security.py              # bash 命令白名单
├── config.py                # 全局配置
├── requirements.txt         # openai + pyyaml
└── README.md
```

---

## 3. 核心概念

### 3.1 Task Definition (`task.yaml`)

用户定义两样东西：**任务是什么** + **团队是什么**。

```yaml
# ============================================================
# Part 1: 任务描述
# ============================================================
name: "Optimize POD Attention kernel on B200"

goal: |
  POD Attention 比 FlashAttention v2 慢 30%。
  找到根因并优化到 5% 以内。

context: |
  - Kernel A (baseline): FlashAttention v2, launch: python bench_fa2.py
  - Kernel B (target): CuTile POD Attention, launch: python bench_pod.py
  - GPU: B200, CUDA 13.2

deliverables:
  - path: "output/analysis/root_cause.md"
    description: "Root cause analysis with NCU data"
  - path: "output/kernels/pod_attention_v2.py"
    description: "Optimized kernel, within 5% of FA2"
  - path: "output/results/final_benchmark.csv"
    description: "Final benchmark comparison"

environment:
  shell_preamble:
    - "conda activate cutile"
  working_dir: "./project"           # harness 状态 + workspace + output 放这里

  # agent 可以直接读写的外部路径（绝对路径）
  # 不在此列表中的路径会被文件工具拒绝
  allowed_paths:
    - "/home/yiwenz/repos/cutile/kernels"          # kernel 源码，可直接修改
    - "/home/yiwenz/repos/cutile/benchmarks"        # benchmark 脚本
    - "/home/yiwenz/repos/flash-attention/csrc"     # baseline 代码，参考用

# ============================================================
# Part 2: Agent 团队定义
# ============================================================
agents:
  coordinator:
    role: |
      You are the project coordinator. Your job is to:
      1. Create an execution plan (break the goal into steps).
      2. Assign each step to the most appropriate specialist agent.
      3. Review results from agents and decide next actions.
      4. You do NOT execute tasks yourself. You only plan and delegate.
    model:
      provider: nvidia
      name: aws/anthropic/bedrock-claude-opus-4-6
    # coordinator 的工具集：只有规划和调度工具，没有 bash / file
    tools: [get_plan, create_plan, update_plan, assign_task, check_result, read_file]

  coder:
    role: |
      You are a CUDA kernel developer. You write and modify kernel code.
      You can read existing code, write new code, and run compilation tests.
    model:
      provider: nvidia
      name: aws/anthropic/bedrock-claude-sonnet-4-5-v1
    tools: [bash, read_file, write_file, edit_file, list_files, search_files]

  profiler:
    role: |
      You are a GPU performance analyst. You run benchmarks and NCU profiling,
      analyze metrics, and produce comparison reports.
    model:
      provider: deepseek
      name: deepseek-chat
    tools: [bash, read_file, write_file, list_files]

  reviewer:
    role: |
      You are a code reviewer. You review code changes for correctness,
      performance, and style. You also manage git commits.
    model:
      provider: nvidia
      name: aws/anthropic/bedrock-claude-sonnet-4-5-v1
    tools: [bash, read_file, search_files, list_files]
```

**关键设计点：**
- 每个 agent 有独立的 `role`（注入 system prompt）、`model`（可以不同）、`tools`（权限隔离）
- coordinator 只有规划和调度工具，**不能直接执行 bash**——强制它只做规划
- agent 列表完全由用户定义——不同任务可以有不同的团队组合

### 3.2 不同任务的 Agent 团队示例

**Kernel 优化任务（如上）：** coordinator + coder + profiler + reviewer

**写 Tech Blog：**
```yaml
agents:
  coordinator:
    role: "Plan the blog structure and coordinate writing."
    model: { provider: nvidia, name: aws/anthropic/bedrock-claude-opus-4-6 }
    tools: [get_plan, create_plan, update_plan, assign_task, check_result, read_file]
  writer:
    role: "Write blog content in Markdown."
    model: { provider: nvidia, name: aws/anthropic/bedrock-claude-sonnet-4-5-v1 }
    tools: [bash, read_file, write_file, edit_file, search_files]
  figure_maker:
    role: "Create publication-quality figures using matplotlib."
    model: { provider: deepseek, name: deepseek-chat }
    tools: [bash, read_file, write_file]
```

**iOS App 开发：**
```yaml
agents:
  coordinator:
    role: "Plan features and coordinate development."
    ...
  developer:
    role: "Write SwiftUI code."
    ...
  tester:
    role: "Run xcodebuild, test features, report bugs."
    ...
```

### 3.3 Agent 间通信（Message Bus）

Agent 之间不直接通信。所有通信通过 **文件系统 message bus** 中转。

```
.harness/
├── messages/
│   ├── msg_001.json    # coordinator → profiler: "run baseline benchmark"
│   ├── msg_002.json    # profiler → coordinator: "done, FA2=1.23ms, POD=1.60ms"
│   ├── msg_003.json    # coordinator → coder: "optimize memory access pattern"
│   └── ...
```

**消息格式：**

```json
{
  "id": "msg_001",
  "from": "coordinator",
  "to": "profiler",
  "type": "task_assignment",
  "content": "Run baseline benchmark: python bench_fa2.py and bench_pod.py, 3 runs each. Save results to results/baseline.csv. Return mean ± std for both kernels.",
  "status": "pending",
  "result": null
}
```

**Coordinator 的调度工具：**

| 工具 | 作用 |
|------|------|
| `assign_task(agent, task_description)` | 给指定 agent 下发任务，写入 message bus |
| `check_result(task_id)` | 查看某个任务的执行结果 |
| `check_all_results()` | 查看所有 pending/done 的任务状态 |

**Specialist agent 看到的：**
当 harness 激活某个 specialist agent 时，它收到的 user message 是 coordinator 下发的任务描述。执行完后，它的最后一条 text response 作为 result 写回 message bus。

### 3.4 执行循环

```
main.py → 读取 task.yaml → 注册所有 agents

Coordinator Loop:
  while not done:
    1. 运行 coordinator session（fresh context）
       coordinator 调用 get_plan / create_plan
       coordinator 调用 assign_task(agent="profiler", task="...")
       coordinator 调用 assign_task(agent="coder", task="...")
       coordinator session 结束

    2. harness 检查 message bus，找到所有 pending tasks

    3. 按顺序（或并行）运行对应的 specialist agents：
       profiler session（fresh context, 收到任务描述）
         → 执行 bash/ncu/写文件
         → 返回结果摘要
         → 结果写入 message bus

       coder session（fresh context, 收到任务描述）
         → 读代码、写代码、编译测试
         → 返回结果摘要
         → 结果写入 message bus

    4. 再次运行 coordinator session（fresh context）
       coordinator 调用 check_result / check_all_results
       coordinator 查看结果，决定下一步
       coordinator 调用 update_plan(step=N, status="done", findings="...")
       coordinator 可能下发新任务...

    5. 检查终止条件：
       if plan.json 所有 steps done → 退出
       if max_iterations 达到 → 退出
       if fatal error → 退出
```

**关键：coordinator 永远只看摘要**。profiler 跑了 200 行 NCU 输出，但 coordinator 只收到"FA2 occupancy 85%, POD occupancy 82%, BW gap: 78% vs 52%"。coordinator 的 context 始终干净。

### 3.5 Agent Journal（每个 agent 的决策历史）

**问题**：每个 session 是 fresh context。不仅 coordinator 不知道自己之前做过什么决策，specialist 也不知道——比如 profiler 在 Session 1 跑了 baseline benchmark，到 Session 3 被要求 re-benchmark 时，它不知道之前用了什么参数、遇到过什么问题。更关键的是：如果 coder 在 Session 1 尝试了优化方案 A 但效果不佳，Session 2 时它不知道 A 已经试过了，可能会重复同样的尝试。

**方案**：每个 agent 都有自己的 journal 文件。

```
.harness/
├── journals/
│   ├── coordinator.md    # coordinator 的决策历史
│   ├── profiler.md       # profiler 的执行历史
│   ├── coder.md          # coder 的执行历史
│   └── reviewer.md       # reviewer 的执行历史
├── messages/
│   └── ...
```

**每个 agent 都有 `write_journal` 工具**（不仅是 coordinator）：

| 工具 | 作用 | 谁能用 |
|------|------|--------|
| `write_journal(content)` | 追加本轮工作摘要到自己的 journal 文件 | 所有 agent |

**读取时机**：每个 agent session 启动时，harness 将该 agent 的 journal 注入 system prompt：

```
## Your Work History
{.harness/journals/{agent_name}.md 的内容}
```

**不同角色的 journal 侧重点不同**：

**Coordinator journal 记录**：
- 决策 + 推理逻辑
- 备选方案（如果当前路径失败）
- 关键数据点

```markdown
## Session 2 (2026-03-23 15:12)
- L2 hit rate is primary bottleneck (45% vs 65%)
- Decision: assign coder to optimize coalesced access
- Backup: software prefetch if coalesced access doesn't help
```

**Specialist journal 记录**：
- 做了什么、产出了什么文件
- 遇到的问题和解决方法
- 已尝试但失败的方案（防止重复）
- 留给下一次 session 的 notes

```markdown
## Session 1 (task: baseline benchmark)
- Ran bench_fa2.py and bench_pod.py, 3 runs each
- Results saved to results/baseline.csv
- Note: first run of POD was outlier (cold cache), discarded

## Session 3 (task: re-benchmark after optimization)
- Ran same benchmark suite on optimized kernel
- Gap reduced from 30% to 4%
- Results saved to results/optimized.csv
```

**Coder journal 示例（防止重复尝试）**：

```markdown
## Session 1 (task: optimize memory access)
- Tried: tiled coalesced access for Q/K loading
- Changed: kernels/pod_attention.py lines 45-78
- Result: compiles OK, needs profiler verification
- Note: did NOT try software prefetch — coordinator said try coalesced first

## Session 2 (task: further optimize after coalesced access insufficient)
- Previous attempt (coalesced access) improved BW from 52% to 68%, but not enough
- Now trying: software prefetch with __prefetch_l2 intrinsic
- Changed: kernels/pod_attention.py lines 30-42
- Also adjusted tile size from 64 to 128 based on L2 cache line analysis
```

**Prompt 中的呈现**：

对于 coordinator：
```
## Your Decision History
{coordinator journal}
```

对于 specialist：
```
## Your Work History
{该 agent 的 journal}

## Context from Coordinator
{当前任务的 message，里面包含 coordinator 的指令}
```

**⚠️ Journal 增长问题**：如果任务特别长（某个 agent 被调用 20+ 次），journal 可能会很大。解决方案：
- 当某个 agent 的 journal 超过一定长度（如 3000 字）时，harness 自动用 LLM 做一次压缩（保留关键决策/数据/失败记录，去掉冗余）
- 压缩后的版本替换原文件，旧版本备份到 `.harness/journal_archive/`
- 失败记录优先保留（防止重复尝试是 journal 的核心价值之一）

### 3.6 Planning Tool

coordinator 专用。

| 工具 | 作用 |
|------|------|
| `create_plan` | 创建执行计划 |
| `update_plan` | 更新步骤状态和 findings |
| `get_plan` | 读取当前计划 |

plan.json 示例：

```json
{
  "steps": [
    {
      "id": 1,
      "description": "Baseline benchmark",
      "assigned_to": "profiler",
      "status": "done",
      "findings": "FA2: 1.23ms ± 0.02, POD: 1.60ms ± 0.03, gap=30%"
    },
    {
      "id": 2,
      "description": "NCU profiling both kernels",
      "assigned_to": "profiler",
      "status": "done",
      "findings": "Key gap: L2 hit rate 45% vs 65%, memory BW 52% vs 78%"
    },
    {
      "id": 3,
      "description": "Optimize memory access pattern in POD kernel",
      "assigned_to": "coder",
      "status": "in_progress",
      "findings": ""
    }
  ]
}
```

### 3.6 Middleware

和 v1 相同，但作用于每个 agent 独立。

| Middleware | 作用 |
|-----------|------|
| `loop_detector` | 检测单个 agent 是否卡循环 |
| `token_tracker` | 按 agent 追踪 token 消耗 |

---

## 4. Prompt 策略

### 4.1 Coordinator 的 System Prompt（自动生成）

```
You are the coordinator of a multi-agent team.

## Task
{task.yaml: name + goal + context}

## Deliverables
{task.yaml: deliverables}

## Your Team
- profiler: GPU performance analyst. Can run benchmarks and NCU profiling.
- coder: CUDA kernel developer. Can read/write/compile kernel code.
- reviewer: Code reviewer. Can review changes and manage git.

## Your Tools
- get_plan / create_plan / update_plan: manage execution plan
- assign_task(agent, description): send a task to a specialist
- check_result(task_id) / check_all_results: read task results
- write_journal(content): record your decisions and reasoning for future sessions
- read_file(path): read a file (for checking deliverables)

## Your Decision History
{coordinator_journal.md 的完整内容，首次为空}

## Rules
1. You do NOT execute tasks yourself. Always assign to a specialist.
2. First session: create a plan. Assign the first tasks.
3. Subsequent sessions: check results from previous assignments, update plan, assign next tasks.
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

{task.yaml: instructions}
```

### 4.2 Specialist Agent 的 System Prompt（自动生成）

```
You are {agent_name}, a specialist agent in a team.

## Your Role
{agent.role from task.yaml}

## Your Task
{从 message bus 拿到的 task description}

## Your Work History
{.harness/journals/{agent_name}.md 的内容，首次为空}

## Available Tools
{agent.tools from task.yaml}
- write_journal(content): record what you did, what worked, what failed

## Rules
1. Execute the task described above.
2. Save all outputs to files in the working directory.
3. BEFORE ENDING: call write_journal to record:
   - What you did and what files you changed
   - What worked and what didn't (especially failed attempts — this prevents repeating them)
   - Any useful observations for future sessions
4. Provide a concise summary (3-5 sentences) as your final message. This summary
   will be sent back to the coordinator — it is the ONLY thing the coordinator sees.
```

### 4.3 Session Prompt

**Coordinator（首次）：**
```
New session. No plan exists yet.
Read the task description, create a plan, and assign the first batch of tasks.
```

**Coordinator（后续）：**
```
New session. Check completed task results and current plan status.
Update the plan, then assign next tasks or verify deliverables.
```

**Specialist：**
```
You have been assigned a task by the coordinator. Execute it now.
```

---

## 5. 工具分配

| 工具 | Coordinator | Specialist |
|------|:-----------:|:----------:|
| `get_plan` | ✅ | ❌ |
| `create_plan` | ✅ | ❌ |
| `update_plan` | ✅ | ❌ |
| `assign_task` | ✅ | ❌ |
| `check_result` | ✅ | ❌ |
| `check_all_results` | ✅ | ❌ |
| `write_journal` | ✅ | ✅（每个 agent 写自己的 journal） |
| `read_file` | ✅（检查 deliverable） | ✅ |
| `bash` | ❌ | ✅（按 task.yaml 配置） |
| `write_file` | ❌ | ✅ |
| `edit_file` | ❌ | ✅ |
| `list_files` | ❌ | ✅ |
| `search_files` | ❌ | ✅ |

Coordinator **不能 bash**，这是强制的。它只能通过 `assign_task` 让别人干活。

### 5.2 文件路径安全

Agent 的文件工具（read_file, write_file, edit_file, list_files, search_files）可以访问：
1. `working_dir` 及其子目录（workspace/, output/, .harness/ 等）
2. `allowed_paths` 中声明的所有绝对路径

**其他路径一律拒绝**。这防止 agent 意外读写系统文件或不相关的项目。

```python
# 伪代码：路径访问检查
def is_path_allowed(path: str, config: dict) -> bool:
    resolved = os.path.realpath(path)
    # 允许 working_dir 下的所有文件
    if resolved.startswith(config["working_dir"]):
        return True
    # 允许 allowed_paths 中声明的路径
    for allowed in config.get("allowed_paths", []):
        if resolved.startswith(os.path.realpath(allowed)):
            return True
    return False
```

---

## 6. 典型执行流程（详细）

```
用户: python main.py --task task.yaml

═══ Coordinator Session 1 ═══
  (system prompt 里 Decision History 为空)
  coordinator 调用 get_plan → "not found"
  coordinator 调用 create_plan → 5 steps
  coordinator 调用 assign_task(agent="profiler", task="Run baseline benchmark...")
  coordinator 调用 assign_task(agent="profiler", task="Run NCU profiling...")
  coordinator 调用 write_journal("Created 5-step plan. Assigned profiler for baseline + NCU. Discovery phase first.")
  → session 结束

═══ Harness: dispatch pending tasks ═══
  找到 2 个 pending tasks，都分给 profiler

═══ Profiler Session 1 (task: baseline benchmark) ═══
  profiler 调用 bash("python bench_fa2.py") × 3
  profiler 调用 bash("python bench_pod.py") × 3
  profiler 调用 write_file("results/baseline.csv", ...)
  profiler 返回摘要: "FA2: 1.23ms ± 0.02, POD: 1.60ms ± 0.03. Gap=30%."
  → 摘要写入 message bus

═══ Profiler Session 2 (task: NCU profiling) ═══
  profiler 调用 bash("ncu --set full python bench_fa2.py")
  profiler 调用 bash("ncu --set full python bench_pod.py")
  profiler 调用 write_file("profiles/comparison.md", ...)
  profiler 返回摘要: "Key gap: L2 hit rate 45% vs 65%. Memory BW 52% vs 78%."
  → 摘要写入 message bus

═══ Coordinator Session 2 ═══
  (system prompt 里 Decision History = Session 1 的 journal)
  coordinator 调用 check_all_results → 看到两个摘要
  coordinator 调用 update_plan(step=1, done) + update_plan(step=2, done)
  coordinator 分析: L2 hit rate 是主因
  coordinator 调用 assign_task(agent="coder", task="Optimize Q/K loading pattern in pod_attention.py to improve L2 hit rate. Current: non-coalesced access.")
  coordinator 调用 write_journal("L2 hit rate is primary bottleneck (45% vs 65%). Assigned coder to fix coalesced access. Backup: software prefetch.")
  → session 结束

═══ Coder Session 1 (task: optimize memory access) ═══
  coder 调用 read_file("kernels/pod_attention.py")
  coder 调用 edit_file(...) → 改 memory access pattern
  coder 调用 bash("python -c 'import pod_attention'") → 编译检查
  coder 返回摘要: "Refactored Q/K loading to tiled coalesced access. Compiles OK."
  → 摘要写入 message bus

═══ Coordinator Session 3 ═══
  (system prompt 里 Decision History = Session 1-2 的 journal)
  coordinator 调用 check_result → coder 完成
  coordinator 调用 update_plan(step=3, done)
  coordinator 调用 assign_task(agent="profiler", task="Re-run benchmark on optimized kernel. Compare with baseline.")
  coordinator 调用 write_journal("Coder completed coalesced access refactor. Assigned profiler to verify. If gap >5%, try prefetch next (per Session 2 backup).")
  → session 结束

═══ Profiler Session 3 (task: re-benchmark) ═══
  ...跑 benchmark...
  profiler 返回: "Optimized POD: 1.28ms ± 0.02. Gap reduced to 4%. Target met."
  → 摘要写入 message bus

═══ Coordinator Session 4 ═══
  (system prompt 里 Decision History = Session 1-3 的 journal)
  coordinator 调用 check_result → gap=4%, target met!
  coordinator 调用 update_plan(step=4, done)
  coordinator 调用 assign_task(agent="reviewer", task="Review changes in pod_attention.py, git commit.")
  coordinator 调用 write_journal("Gap reduced to 4%, target met! Prefetch backup not needed. Assigned reviewer for final commit.")
  → session 结束

═══ Reviewer Session 1 ═══
  reviewer 调用 bash("git diff")
  reviewer 调用 read_file("kernels/pod_attention.py")
  reviewer 返回: "Code looks good. Committed as 'Optimize Q/K memory access for L2 locality'."
  → 摘要写入 message bus

═══ Coordinator Session 5 ═══
  (system prompt 里 Decision History = Session 1-4 的 journal)
  coordinator 调用 check_result → reviewer done
  coordinator 调用 update_plan(step=5, done)
  coordinator 调用 read_file("analysis/root_cause.md") → exists
  coordinator 调用 read_file("results/final_benchmark.csv") → exists
  coordinator 调用 write_journal("All deliverables verified. Task complete. Root cause: non-coalesced Q/K loading. Fix: tiled coalesced access. Result: gap 30%→4%.")
  coordinator: "TASK_COMPLETE"
  → harness 检测 all steps done → 退出
```

---

## 7. 停止条件

| 条件 | 触发 |
|------|------|
| plan.json 所有 steps 的 status == "done" | 正常完成 |
| `--max-iterations` 达到上限 | 安全阀 |
| 致命 API 错误（余额不足、key 无效） | 立即停止 |
| Ctrl+C | 用户中断，下次自动恢复 |
| coordinator 明确输出 "TASK_COMPLETE" | coordinator 自行判断完成 |

---

## 8. 并行 vs 串行

默认：**串行**执行所有 pending tasks（简单可靠）。

可选（v2 再做）：如果 coordinator 在一轮里 assign 了多个不相关的 task（分给不同 agent），可以并行执行。但 v1 先不做，避免复杂度。

---

## 9. 文件管理

### 9.1 三层文件结构

```
{working_dir}/
│
│  ┌─────────────────────────────────────────────────┐
│  │  Layer 0: 用户输入                               │
│  └─────────────────────────────────────────────────┘
├── task.yaml                         # 任务定义 + agent 团队定义
│
│  (外部路径：agent 通过 allowed_paths 直接操作原始项目目录)
│  /home/yiwenz/repos/cutile/kernels/     ← agent 直接 read/write/edit
│  /home/yiwenz/repos/cutile/benchmarks/  ← agent 直接 read/bash
│  /home/yiwenz/repos/flash-attention/    ← agent 直接 read
│
│  ┌─────────────────────────────────────────────────┐
│  │  Layer 1: Harness 基础设施（agent 通过工具间接操作）│
│  └─────────────────────────────────────────────────┘
├── .harness/
│   ├── plan.json                     # 执行计划（coordinator 通过 planning 工具操作）
│   ├── journals/                     # 每个 agent 的工作记忆（通过 write_journal 工具写入）
│   │   ├── coordinator.md
│   │   ├── profiler.md
│   │   ├── coder.md
│   │   └── reviewer.md
│   ├── messages/                     # agent 间任务分发（通过 messaging 工具操作）
│   │   ├── msg_001.json
│   │   └── ...
│   ├── session_log.jsonl             # 每个 session 的元信息（harness 自动写）
│   ├── token_usage.json              # 按 agent 的 token 消耗（harness 自动写）
│   └── journal_archive/              # journal 压缩备份
│
│  ┌─────────────────────────────────────────────────┐
│  │  Layer 2: Agent 工作区（中间产物，每个 agent 独立） │
│  └─────────────────────────────────────────────────┘
├── workspace/
│   ├── profiler/                     # profiler 的中间文件
│   │   ├── baseline_run1.txt         #   原始 benchmark 输出
│   │   ├── ncu_fa2_full.ncu-rep      #   NCU 报告
│   │   └── metrics_comparison.csv    #   对比数据
│   ├── coder/                        # coder 的中间文件
│   │   ├── patch_v1.diff             #   代码修改记录
│   │   └── compile_log.txt           #   编译日志
│   └── reviewer/                     # reviewer 的中间文件
│       └── review_notes.md           #   review 记录
│
│  ┌─────────────────────────────────────────────────┐
│  │  Layer 3: 最终交付物（对应 task.yaml deliverables）│
│  └─────────────────────────────────────────────────┘
├── output/
│   ├── analysis/
│   │   └── root_cause.md             # deliverable 1
│   ├── results/
│   │   └── final_benchmark.csv       # deliverable 2
│   └── kernels/
│       └── pod_attention_v2.py       # deliverable 3
```

### 9.2 各层职责

| Layer | 目录 | 谁写 | 怎么写 | 生命周期 |
|-------|------|------|--------|---------|
| 0 | `task.yaml` + `allowed_paths` | 用户 | 手动 | task.yaml 只读；allowed_paths 下的文件 agent 可直接修改 |
| 1 | `.harness/` | harness + agent（通过工具） | `write_journal`, `create_plan`, `assign_task` 等专用工具 | 任务全程保留，用于恢复状态 |
| 2 | `workspace/{agent}/` | specialist agents | `write_file`, `bash` 等通用工具 | 中间产物，任务结束后可清理 |
| 3 | `output/` | specialist agents | `write_file` 等通用工具 | 最终交付物，永久保留 |

### 9.3 Agent 文件访问约定

**Specialist agent 的工作流**：
1. 读取/修改源码：直接用绝对路径操作 `allowed_paths` 下的文件（如 `/home/yiwenz/repos/cutile/kernels/pod_attention.py`）
2. 中间产物：写入 `workspace/{自己的名字}/`（如 `workspace/profiler/ncu_output.txt`）
3. 最终交付物：写入 `output/` 下对应路径（如 `output/analysis/root_cause.md`）
4. 记录记忆：调用 `write_journal` 写入 `.harness/journals/{自己的名字}.md`

**Coordinator 的工作流**：
1. 通过 `get_plan` 查看进度
2. 通过 `check_all_results` 查看 specialist 的执行摘要
3. 通过 `read_file` 检查 `output/` 下的 deliverables 是否存在
4. 调用 `write_journal` 记录决策历史
5. **coordinator 不读 `workspace/` 下的中间文件**——它只看摘要，不看细节

**Harness 层面**：
- `session_log.jsonl`：每个 session 结束后自动写入一行（agent 名、开始/结束时间、token 用量、状态）
- `token_usage.json`：按 agent 累计 token 消耗

### 9.4 Specialist 的 System Prompt 中的路径指引

```
## File Organization
- Source code: use absolute paths directly (e.g. /home/yiwenz/repos/cutile/kernels/...)
- Intermediate files: save to workspace/{your_name}/ (e.g. workspace/profiler/ncu_output.txt)
- Final deliverables: save to output/{path} (e.g. output/results/final_benchmark.csv)
- Your journal: use write_journal tool (do NOT write to .harness/ directly)

## Accessible External Paths
{列出 task.yaml 中 allowed_paths 的所有路径}

You can read and write files at these paths using their absolute paths.
Files outside these paths and outside the working directory will be blocked.
```

### 9.5 任务结束后的产出

任务完成后，用户关心的只有两个目录：

| 目录 | 内容 | 用途 |
|------|------|------|
| `output/` | 所有 deliverables | 最终成果，直接使用 |
| `.harness/journals/` | 所有 agent 的工作记录 | 复盘、审计、理解决策过程 |

`workspace/` 可以选择保留（debug 用）或清理。
`.harness/messages/` 和 `session_log.jsonl` 用于追溯执行过程，一般不需要手动查看。

---

## 10. 代码结构

| 模块 | 职责 | 预估行数 |
|------|------|----------|
| `main.py` | CLI 入口，解析参数 | ~80 |
| `coordinator.py` | Coordinator 循环：run coordinator → dispatch tasks → repeat | ~150 |
| `agent_runner.py` | 通用 ReAct loop（任何 agent 都用这个跑） | ~150 |
| `agent_registry.py` | 从 task.yaml 解析 agent 定义，创建 client + tool set | ~100 |
| `client.py` | OpenAI-compatible client（多 provider 支持） | ~90（复用） |
| `message_bus.py` | 文件 based 消息系统（assign/check/update） | ~120 |
| `tools/__init__.py` | 工具注册表 + tool definition 生成 | ~80 |
| `tools/bash.py` | bash 执行 + security + preamble | ~100（复用） |
| `tools/filesystem.py` | read/write/edit/list/search | ~200（复用） |
| `tools/planning.py` | create/update/get plan | ~100 |
| `tools/messaging.py` | assign_task / check_result（coordinator 工具） | ~80 |
| `tools/journal.py` | write_journal（coordinator 决策历史） | ~50 |
| `middleware/` | loop detector + token tracker | ~100 |
| `security.py` | bash 白名单 | ~120（复用） |
| `config.py` | 全局配置 | ~40 |
| **总计** | | **~1600 行** |

---

## 11. 与 v1 设计的差异

| | v1（单 agent） | v2（multi-agent team） |
|--|---------------|----------------------|
| Agent 数量 | 1 个（+ optional subagent） | N 个，用户定义 |
| 模型 | 全局一个 | 每个 agent 可以不同 |
| 工具集 | 所有 agent 共享 | 按 agent 隔离 |
| Context 污染 | 主 agent 做所有事，容易污染 | Coordinator 只看摘要，specialists 独立 context |
| 通信 | subagent 结果直接返回 | 文件 based message bus |
| 配置 | 硬编码 mode（coding/perf） | task.yaml 定义一切 |
| Prompt | 多套（system/init/coding × mode） | 从 task.yaml 自动生成，一套逻辑 |
