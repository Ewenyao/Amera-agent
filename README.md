# ollama-agent 使用手册

跑在终端里的本地编码助手。只依赖 Python 标准库，直连 Ollama，不要 API Key，除主动联网搜索外不出本机。

```
> 看一下 utils.py 的分页逻辑，把 off-by-one 修掉
```

适用场景：本地 4B～14B 模型，想让它读代码、改文件、跑命令，但不想把代码上传到云端。

---

## 一、功能总览

| 分类 | 功能 | 说明 |
|---|---|---|
| 交互 | 交互式会话 | 敲 `agent` 进入，多轮对话，上下文累积 |
| 交互 | 单次任务 | `agent "任务"`，跑完退出，适合脚本调用 |
| 交互 | 流式输出 | 边生成边打印，不用等整段 |
| 交互 | 思维链显示 | `--think` 打开，推理过程暗色缩进显示 |
| 交互 | 斜杠命令 | `/init` `/model` `/clear` `/tools` `/help` `/exit` |
| 工具 | `list_dir` | 列目录，最多 200 项 |
| 工具 | `read_file` | 按行分段读，单次最多 2000 行 |
| 工具 | `write_file` | 写文件，自动建父目录 |
| 工具 | `replace_in_file` | 精确替换，要求原文唯一匹配 |
| 工具 | `shell` | 执行命令，60 秒超时，破坏性操作黑名单 |
| 工具 | `web_search` | Bing 搜索，返回标题 / 链接 / 摘要 |
| 工具 | `fetch_url` | 抓网页，HTML 转纯文本，2MB 上限 |
| 上下文 | 项目说明 | `/init` 生成 `AGENTS.md`，每次启动自动加载 |
| 上下文 | 窗口控制 | 超长对话自动裁剪历史，避免撑爆 `num_ctx` |
| 上下文 | Token 统计 | 每轮结束显示输入 / 输出 token 和耗时 |
| 安全 | 读权限 | 默认全盘可读，`--jail` 收回到工作目录 |
| 安全 | 写权限 | 默认只写工作目录 + 家目录（Windows 加当前盘符），`--write-root` 扩展 |
| 安全 | 命令黑名单 | 拦截 `rm -rf`、`format`、`shutdown`、`bcdedit` 等 |
| 安全 | 防死循环 | 重复调用拦截、联网工具连续上限、轮数用尽强制收尾 |
| 配置 | 多级配置 | 命令行 > 环境变量 > 配置文件 > 内置默认 |
| 配置 | 模型切换 | `/model` 运行时切换，或 `-m` 指定 |
| 跨平台 | Windows / macOS / Linux | 路径、编码、换行符都做了处理 |

七个工具是刻意压低的。4B 模型在 8 个以上工具时选错率明显上升。

---

## 二、环境要求

| 项 | 要求 |
|---|---|
| Python | 3.9 及以上 |
| Ollama | 已安装并运行（默认 `http://127.0.0.1:11434`） |
| 模型 | 必须支持原生 tool calling（Ollama 的 `tools` 参数），如 qwen3 / qwen3.5 / llama3.1+ |
| 磁盘 | 仓库本身不到 1MB，模型另算（4B 约 3.3GB） |

检查环境：

```bash
python --version
ollama --version
ollama list                 # 看有哪些模型
curl http://127.0.0.1:11434/api/version
```

没有模型就先拉一个：

```bash
ollama pull qwen3.5:4b
```

---

## 三、部署

### 方式 A：pip 安装（推荐）

```bash
git clone https://github.com/OWNER/ollama-agent.git
cd ollama-agent
pip install -e .
```

装完在任何目录都能用 `agent`。验证：

```bash
agent --version      # ollama-agent 0.1.0
agent --list-models  # 列出本地模型
```

### 方式 B：不安装，源码直跑

Windows：

```bat
cd D:\ollama\ollama-agent
agent.cmd
```

macOS / Linux：

```bash
cd ~/ollama-agent
./bin/agent
```

或者手动指定路径：

```bash
# macOS / Linux
PYTHONPATH=src python3 -m ollama_agent

# Windows PowerShell
$env:PYTHONPATH="src"; python -m ollama_agent
```

### 方式 C：从 Git 直接装（不发 PyPI）

```bash
pip install git+https://github.com/OWNER/ollama-agent.git
```

### 用虚拟环境（建议）

别装进系统 Python：

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -e .
```

---

## 四、快速上手

```bash
cd 你的项目目录
agent
```

第一次进会看到：

```
────────────────────────────────────────────────────────────────
  agent  qwen3.5:4b   工具 7 个   上下文 4096   思维链关
  cwd    D:\code\my-project
  服务   http://127.0.0.1:11434
  上下文 没有 AGENTS.md，可以先跑 /init
────────────────────────────────────────────────────────────────

> 
```

建议第一件事：

```
> /init
```

它会扫一遍当前目录，生成 `AGENTS.md`（项目是什么、技术栈、目录职责、怎么跑怎么测）。之后每次启动自动加载，模型开口就知道项目结构，不用再翻文件。

然后正常提需求：

```
> 把 tests 里缺的用例补上
> 这个报错是什么意思：ModuleNotFoundError: No module named 'yaml'
> 把 config.py 里的硬编码路径改成从环境变量读
```

---

## 五、命令参考

### 命令行参数

| 参数 | 作用 | 示例 |
|---|---|---|
| `[任务]` | 单次执行，跑完退出 | `agent "列出所有 TODO"` |
| `-q, --query` | 同上，显式写法 | `agent -q "..."` |
| `-m, --model` | 指定模型 | `agent -m qwen3:8b` |
| `--host` | Ollama 地址 | `agent --host http://192.168.1.10:11434` |
| `--num-ctx` | 上下文窗口 | `agent --num-ctx 8192` |
| `--max-steps` | 最大工具轮数 | `agent --max-steps 6` |
| `--think` | 开思维链（更慢更准） | `agent --think` |
| `--no-shell` | 禁用命令执行 | `agent --no-shell` |
| `--jail` | 读也限制在工作目录 | `agent --jail` |
| `--write-root` | 追加可写目录（可重复） | `agent --write-root E:\work` |
| `--root` | 指定工作目录 | `agent --root D:\proj` |
| `--init` | 生成 AGENTS.md 后退出 | `agent --init` |
| `--list-models` | 列出本地模型 | `agent --list-models` |
| `--version` | 版本 | `agent --version` |

### 会话内命令

| 命令 | 作用 |
|---|---|
| `/init` | 扫描当前目录，生成并立即启用 `AGENTS.md` |
| `/model` | 显示当前模型和本地可用模型 |
| `/model qwen3:8b` | 切换模型（会清空当前对话） |
| `/clear` | 清空对话上下文，工具和模型不变 |
| `/tools` | 列出当前可用工具 |
| `/help` | 帮助 |
| `/exit` | 退出（`Ctrl+C` / `Ctrl+D` 同样） |

---

## 六、工具能力与限制

| 工具 | 输入 | 限制 | 典型用途 |
|---|---|---|---|
| `list_dir` | `path` | 最多 200 项 | 摸清目录结构 |
| `read_file` | `path`, `offset`, `limit` | 单次最多 2000 行，大文件分段读 | 看代码 |
| `write_file` | `path`, `content` | 受写权限限制，覆盖同名文件 | 新建文件 |
| `replace_in_file` | `path`, `old`, `new` | 原文必须唯一匹配，否则报错 | 改代码（首选） |
| `shell` | `command` | 60 秒超时，输出截断 8000 字，黑名单拦截 | 跑测试、装依赖、git |
| `web_search` | `query`, `top_k` | 最多 10 条，走 Bing | 查版本号、报错信息 |
| `fetch_url` | `url`, `max_chars` | 2MB / 25 秒，默认 4000 字，只能公网 | 看文档 |

**为什么有 `replace_in_file`**：4B 模型整文件重写时容易吃掉没动的行。这个工具只替换匹配到的片段，原文不唯一就拒绝执行，比“重写整个文件”安全得多。

**shell 黑名单覆盖**：`rm -rf`、`rmdir /s`、`del /f /s /q`、`format X:`、`shutdown`、`reboot`、`diskpart`、`mkfs`、`dd if=`、`Remove-Item -Recurse -Force`、`reg delete`、`bcdedit`、`cipher /w`、`chkdsk /f`、`git clean -f`。这些在执行前就被拒绝，模型会收到拒绝原因。

---

## 七、配置

优先级：**命令行参数 > 环境变量 > 配置文件 > 内置默认**。

### 配置文件位置

| 系统 | 路径 |
|---|---|
| Windows | `%APPDATA%\ollama-agent\config.json` |
| macOS / Linux | `~/.config/ollama-agent/config.json` |

示例：

```json
{
  "model": "qwen3.5:4b",
  "host": "http://127.0.0.1:11434",
  "num_ctx": 4096,
  "max_steps": 12,
  "think": false,
  "allow_shell": true,
  "jail": false,
  "write_roots": ["D:\\"]
}
```

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `model` | string | `qwen3.5:4b` | 模型名，必须和 `ollama list` 一致 |
| `host` | string | `http://127.0.0.1:11434` | 远程 Ollama 也能填 |
| `num_ctx` | int | `4096` | 上下文窗口，越大越吃内存、CPU 推理越慢 |
| `max_steps` | int | `12` | 单次任务最多几轮工具调用 |
| `think` | bool | `false` | 是否开思维链 |
| `allow_shell` | bool | `true` | 关掉就只剩文件工具 |
| `jail` | bool | `false` | `true` = 读也限制在工作目录 |
| `write_roots` | array | 家目录 + 当前盘符 | 额外可写目录 |

### 环境变量

| 变量 | 对应 |
|---|---|
| `OLLAMA_MODEL` | `model` |
| `OLLAMA_HOST` | `host` |
| `OLLAMA_NUM_CTX` | `num_ctx` |
| `NO_COLOR` | 设置后关闭彩色输出 |

临时换模型不用改配置：

```bash
OLLAMA_MODEL=qwen3:8b agent          # macOS / Linux
set OLLAMA_MODEL=qwen3:8b && agent   # Windows CMD
```

---

## 八、权限模型

| 能力 | 默认范围 | 怎么收紧 | 怎么放宽 |
|---|---|---|---|
| 读文件 | 整个磁盘 | `--jail` 或 `"jail": true` | 已全开 |
| 写文件 | 工作目录 + 家目录 + 当前盘符 | 改 `write_roots` 配置 | `--write-root` 或配置里加目录 |
| 执行命令 | 除黑名单外全部 | `--no-shell` 或 `"allow_shell": false` | 已全开 |

几点必须清楚：

- **不是沙箱。** shell 以你自己用户的身份运行。不可信任务请用独立用户或虚拟机。
- **管理员操作照样失败。** 改服务、写 `C:\Program Files` 需要 UAC，会返回 `Access is denied`，这是系统拦的，不是 agent 拦的。
- **写 `C:` 根目录会被拒。** 报错里会列出当前可写范围。

---

## 九、常见问题

**连不上 Ollama**

```
请求失败：连不上 Ollama（http://127.0.0.1:11434）：Connection refused
```

先确认服务在跑：

```bash
ollama serve
curl http://127.0.0.1:11434/api/version
```

**模型不调工具，只会聊天**

说明这个模型不支持原生 tool calling，或者名字写错了。换个模型：

```bash
agent --list-models
agent -m qwen3.5:4b
```

**回答很慢**

4B 在 CPU 上单轮 20～30 秒是正常的，一次工具链要好几轮。可以关掉 `--think`、把 `--num-ctx` 调到 2048、或换更小的模型。

**工具调用总是失败 / 参数为空**

模型能力不足时会出现。加 `--think` 提高准确率，或者换 8B 以上模型。

**`/init` 生成的内容有臆测**

`AGENTS.md` 是模型写的，可能加上它自己脑补的约定。用之前扫一眼，不对就手工改，下次启动会加载你改过的版本。

**中文乱码**

Windows 控制台默认 GBK，程序内部已做双编码解码。终端里还乱码就执行：

```cmd
chcp 65001
```

**颜色代码乱飞（`[36m` 之类）**

终端不支持 ANSI。设 `NO_COLOR=1` 关掉。

**`agent` 命令找不到**

pip 装的脚本目录不在 PATH：

```bash
python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
```

把输出目录加进 PATH，或者直接用 `python -m ollama_agent`。

---

## 十、卸载

```bash
pip uninstall ollama-agent
```

配置文件在 `%APPDATA%\ollama-agent\` 或 `~/.config/ollama-agent/`，想彻底清干净就手动删。

项目里生成的 `AGENTS.md` 不会被删，那属于你的项目。

---

## 十一、开发

```bash
git clone https://github.com/OWNER/ollama-agent.git
cd ollama-agent
pip install -e .
python -m unittest discover -s tests -v
```

```
src/ollama_agent/
├── cli.py            参数解析、REPL、渲染
├── config.py         多级配置合并
├── llm.py            Ollama /api/chat 客户端（流式 + tools）
├── loop.py           主循环 + 三道防死循环闸
├── project_scan.py   目录扫描 + AGENTS.md
└── tools.py          工具 schema + ToolBox
```

CI 跑 Ubuntu / Windows / macOS × Python 3.9 / 3.12。

---

## English quick start

A terminal coding agent for local Ollama models. Stdlib only, no API keys.

```bash
pip install git+https://github.com/OWNER/ollama-agent.git
cd your-project
agent            # interactive
agent "task"     # one-shot
```

Requires a model with native tool calling (`ollama pull qwen3.5:4b`).
Reads are unrestricted by default; writes are limited to cwd + `$HOME` (plus the current drive on Windows). Use `--jail` to sandbox reads, `--no-shell` to disable command execution.

## License

MIT
