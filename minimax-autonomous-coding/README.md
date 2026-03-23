# Autonomous Coding Agent - MiniMax Harness

A **model-agnostic** harness for long-running autonomous coding tasks. Implements the
[two-agent pattern](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
(initializer + coding agent) from Anthropic's harness engineering guide, but uses the
**OpenAI-compatible API** instead of the Claude Agent SDK.

Works with **MiniMax M2.7**, OpenAI, DeepSeek, or any OpenAI-compatible endpoint.

## Architecture

```
main.py                  Entry point + CLI
  -> agent.py            ReAct loop (LLM <-> tool execution)
     -> client.py        OpenAI-compatible API client
     -> tools.py         Tool definitions + execution (bash, read/write/edit file, grep)
     -> security.py      Bash command allowlist
     -> progress.py      Feature progress tracking
     -> prompts.py       Prompt loader
        -> prompts/
           system_prompt.md        System prompt (shared)
           initializer_prompt.md   Session 1: scaffold project
           coding_prompt.md        Session 2+: implement features
           app_spec.txt            What to build (customizable)
```

### How it works

1. **Session 1 (Initializer):** Reads `app_spec.txt`, generates `feature_list.json`
   (50+ test cases), creates `init.sh`, initializes git, sets up project structure.

2. **Session 2+ (Coding):** Each session starts with a *fresh context window* (new
   message list). The agent reads `feature_list.json` + `claude-progress.txt` + git
   history to recover state, picks the next feature, implements it, verifies, and
   commits.

3. **State persistence:** All state lives in the filesystem (`feature_list.json`,
   `claude-progress.txt`, git commits) - not in the LLM context. This allows
   unlimited sessions without context overflow.

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set your API key

```bash
# MiniMax (default)
export MINIMAX_API_KEY='your-minimax-api-key'

# Or OpenAI
export OPENAI_API_KEY='your-openai-key'

# Or DeepSeek
export DEEPSEEK_API_KEY='your-deepseek-key'
```

### 3. Run

```bash
# MiniMax M2.7 (default)
python main.py --project-dir ./my_project

# OpenAI GPT-4o
python main.py --provider openai --project-dir ./my_project

# DeepSeek
python main.py --provider deepseek --project-dir ./my_project

# Custom OpenAI-compatible endpoint
python main.py --provider custom \
    --base-url https://api.example.com/v1 \
    --model my-model \
    --project-dir ./my_project

# Limit iterations for testing
python main.py --project-dir ./my_project --max-iterations 3
```

### 4. Resume

Just run the same command again - the agent will detect `feature_list.json` and
continue where it left off:

```bash
python main.py --project-dir ./my_project
```

## CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--project-dir` | `./autonomous_demo_project` | Project directory |
| `--provider` | `minimax` | LLM provider: minimax, openai, deepseek, custom |
| `--model` | (provider default) | Model name override |
| `--api-key` | (from env var) | API key override |
| `--base-url` | (provider default) | API base URL override |
| `--max-iterations` | unlimited | Max agent iterations |

## Customization

### Change what to build

Edit `prompts/app_spec.txt` with your own project specification.

### Adjust feature count

Edit `prompts/initializer_prompt.md` - change "50 features" to your desired number.

### Modify allowed bash commands

Edit `security.py` - update the `ALLOWED_COMMANDS` set.

### Add new tools

Edit `tools.py`:
1. Add a new entry to `TOOL_DEFINITIONS`
2. Add a handler in `execute_tool()`

## Security

- **Bash allowlist:** Only commands in `ALLOWED_COMMANDS` can execute
- **Filesystem sandbox:** File operations are restricted to the project directory
- **Extra validation:** Dangerous commands (rm, pkill) have additional safety checks

## Differences from the Anthropic Version

| Aspect | Anthropic (original) | This version |
|--------|---------------------|-------------|
| SDK | claude-code-sdk | openai SDK (model-agnostic) |
| Models | Claude only | MiniMax, OpenAI, DeepSeek, any OpenAI-compatible |
| Tools | SDK built-in (Read, Write, Edit, Bash, Glob, Grep) | Custom implementation in tools.py |
| Browser testing | Puppeteer MCP | Not included (add your own testing) |
| Security | SDK sandbox + hooks | Allowlist + filesystem restriction |
| Context mgmt | SDK compaction | Fresh context per session (filesystem state) |

## Credits

Based on Anthropic's [Effective Harnesses for Long-Running Agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
and their [autonomous-coding quickstart](https://github.com/anthropics/claude-quickstarts/tree/main/autonomous-coding).
