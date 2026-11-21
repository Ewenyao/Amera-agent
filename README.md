# ollama-agent

A small terminal coding agent for **local Ollama models**. No API keys, no cloud, no framework.

一个跑在终端里的本地编码助手：只依赖 Python 标准库，直连 Ollama，模型和数据都在你自己的机器上。

```bash
$ agent
────────────────────────────────────────────────────────────────
  agent  qwen3.5:4b   工具 7 个   上下文 4096   思维链关
  cwd    D:\code\my-project
  服务   http://127.0.0.1:11434
  上下文 已加载 AGENTS.md
────────────────────────────────────────────────────────────────

> 看一下 utils.py 里的分页逻辑，把 off-by-one 修掉
```

## Why another one

Most agent frameworks assume a frontier model with a 128k window and a paid API. A 4B model on a laptop CPU is a different problem: the system prompt has to stay small, tools have to be few and obvious, and the loop has to defend against the model getting stuck.

This project is that defensive loop, packaged. Roughly 900 lines, zero dependencies.

## Install

Requires Python 3.9+ and a running [Ollama](https://ollama.com).

```bash
git clone https://github.com/OWNER/ollama-agent.git
cd ollama-agent
pip install -e .
```

That gives you an `agent` command. Or skip installing and run from source:

```bash
# macOS / Linux
PYTHONPATH=src python -m ollama_agent

# Windows (PowerShell)
$env:PYTHONPATH="src"; python -m ollama_agent
```

Windows users who prefer double-clicking: `agent.cmd`.

## Usage

```bash
agent                                     # interactive session
agent "把 tests 补齐"                      # one-shot task
agent -m qwen3:8b --think                 # different model, thinking on
agent --init                              # scan cwd, write AGENTS.md
agent --list-models                       # what's pulled locally
agent --jail                              # restrict reads to the workspace
```

In-session commands:

| Command | What it does |
|---|---|
| `/init` | scan the project, generate `AGENTS.md`, apply it immediately |
| `/model [name]` | show local models, or switch |
| `/clear` | drop conversation history |
| `/tools` | list available tools |
| `/help` | help |
| `/exit` | quit |

## Tools

| Tool | Notes |
|---|---|
| `list_dir` | directory listing, capped at 200 entries |
| `read_file` | line-ranged reads, so big files don't blow the window |
| `write_file` | creates parent dirs |
| `replace_in_file` | requires a unique match — safer than rewriting a whole file |
| `shell` | any normal command; destructive patterns are blocked |
| `web_search` | Bing, returns title/url/snippet |
| `fetch_url` | fetch a URL, strips HTML to text |

Reads are unrestricted by default. Writes are limited to the working directory plus the roots you pass via `--write-root`. `--jail` turns reads back into workspace-only.

## The loop guards

Small models get stuck. These are hard stops, not prompt suggestions:

1. **Duplicate calls** — same tool + same args short-circuits with "already ran, try something else".
2. **Streak cap** — `web_search` / `fetch_url` more than 3 times in a row gets refused.
3. **Forced summary** — when the step budget runs out, one final no-tools call produces a conclusion instead of nothing.

These exist because a 4B model will happily call `web_search` twelve times and answer nothing. On the reference machine that turned a 366-second dead end into an 88-second partial answer.

## Project context (`/init`)

```
agent --init
```

Scans the current directory — file counts, extension mix, two levels of tree, project markers, README head, dependency manifest — then asks the model to write `AGENTS.md`: what the project is, stack, directory roles, how to run and test.

`AGENTS.md` is loaded into the system prompt on every start (first 1500 chars). Dependency directories (`.venv`, `node_modules`, `dist`, …) are skipped.

## Configuration

Precedence: CLI flags → environment → config file → defaults.

| Setting | Env | Default |
|---|---|---|
| model | `OLLAMA_MODEL` | `qwen3.5:4b` |
| host | `OLLAMA_HOST` | `http://127.0.0.1:11434` |
| context | `OLLAMA_NUM_CTX` | `4096` |

Config file:

- Windows: `%APPDATA%\ollama-agent\config.json`
- macOS / Linux: `~/.config/ollama-agent/config.json`

```json
{
  "model": "qwen3.5:4b",
  "num_ctx": 4096,
  "max_steps": 12,
  "think": false,
  "write_roots": ["D:\\"]
}
```

## Caveats

- **Tool calling must be supported by the model.** Native Ollama `tools`, not prompt-parsed XML. If your model can't do it, the agent degrades to a chatbot.
- **4B models are slow on CPU.** 20–30s per turn is normal; a tool chain costs several turns.
- **It will make mistakes.** Check diffs. `replace_in_file` failing loudly is intentional.
- **No sandbox.** The shell tool runs as your user. `--no-shell` if that's too much.

## Layout

```
src/ollama_agent/
├── cli.py           argument parsing, REPL, rendering
├── config.py        flags/env/file resolution
├── llm.py           Ollama /api/chat client (streaming, tools)
├── loop.py          the agent loop + loop guards
├── project_scan.py  scan + AGENTS.md
└── tools.py         tool schemas + ToolBox
```

## License

MIT
