"""CLI entry point: `agent` opens an interactive session, `agent "task"` runs once."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import __version__
from .config import resolve
from .llm import OllamaClient, OllamaError
from .loop import Agent
from .project_scan import AGENTS_FILE, INIT_PROMPT, load_context, save_context, scan_project
from .tools import TOOL_SCHEMAS, ToolBox

BOLD, DIM, CYAN, YELLOW, RESET = "\033[1m", "\033[2m", "\033[36m", "\033[33m", "\033[0m"


def use_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if os.name == "nt":
        return bool(os.environ.get("WT_SESSION") or os.environ.get("TERM")
                    or os.environ.get("ANSICON"))
    return True


class Painter:
    def __init__(self):
        self.on = use_color()

    def __call__(self, text, style=""):
        return f"{style}{text}{RESET}" if self.on and style else text


def build_system_prompt(cfg, toolbox):
    root = cfg["root"]
    writable = ", ".join([root, *cfg["write_roots"]])
    read_line = ("- 读：没有目录限制，整个磁盘都能读。" if cfg["read_all"]
                 else f"- 读：只能读 {root} 里面的文件。")
    shell_line = ("- 执行：可以跑 python、pip、git、node 等普通命令；破坏性操作会被拦截。"
                  if cfg["allow_shell"] else "- 执行：本次会话禁止执行命令。")
    prompt = (
        f"你是跑在用户电脑上的命令行助手，工作目录是 {root}。\n"
        "权限边界：\n"
        f"{read_line}\n"
        f"- 写：可以写 {writable}。\n"
        f"{shell_line}\n"
        "规则：\n"
        "1. 需要看文件、改文件、跑命令或查网上信息时，必须调用工具，不要猜。\n"
        "2. 每次只调一个工具，拿到结果再决定下一步。\n"
        "3. 改已有文件优先用 replace_in_file，别整文件重写。\n"
        "4. 工具报错就把真实报错告诉用户，不要编造理由。\n"
        "5. 任务完成后用中文简短说结论，不要再调工具。\n"
        "6. 同一个工具不要连续调用超过两次；查不到就直说。\n"
    )
    ctx = load_context(root)
    if ctx:
        prompt += f"\n## 项目上下文（{AGENTS_FILE}）\n{ctx}\n"
    return prompt


def check_capabilities(client, cfg, paint, quiet=False):
    """非千问模型也能用，前提是它声明了 tools 能力。查不到就别拦着。"""
    caps = client.capabilities()
    if not caps:
        return
    if "tools" not in caps:
        if not quiet:
            print(paint(f"  注意：{cfg['model']} 没有 tools 能力，只能聊天，调不了工具。"
                        f"（{', '.join(caps)}）", YELLOW))
    if cfg["think"] and "thinking" not in caps:
        cfg["think"] = False
        client.think = False
        if not quiet:
            print(paint(f"  注意：{cfg['model']} 不支持 thinking，已自动关闭思维链。", DIM))


def make_stack(cfg, paint):
    client = OllamaClient(host=cfg["host"], model=cfg["model"], num_ctx=cfg["num_ctx"],
                          think=cfg["think"])
    toolbox = ToolBox(cfg["root"], allow_shell=cfg["allow_shell"],
                      write_roots=cfg["write_roots"], read_all=cfg["read_all"])
    agent = Agent(client, toolbox, build_system_prompt(cfg, toolbox), max_steps=cfg["max_steps"])
    return client, toolbox, agent


class Printer:
    """流式渲染：正文直接写，思维链加暗色前缀。"""

    def __init__(self, paint):
        self.paint = paint
        self.thinking = False

    def text(self, chunk):
        if self.thinking:
            sys.stdout.write("\n")
            self.thinking = False
        sys.stdout.write(chunk)
        sys.stdout.flush()

    def think(self, chunk):
        if not self.thinking:
            sys.stdout.write(self.paint("\n  ┆ ", DIM))
            self.thinking = True
        sys.stdout.write(self.paint(chunk.replace("\n", "\n  ┆ "), DIM))
        sys.stdout.flush()

    def end(self):
        if self.thinking:
            sys.stdout.write("\n")
            self.thinking = False
        sys.stdout.write("\n")


def run_init(cfg, client, paint):
    print(paint("  扫描目录…", DIM), end="", flush=True)
    scan = scan_project(cfg["root"])
    print(paint(f" {len(scan)} 字符", DIM), flush=True)

    messages = [
        {"role": "system", "content": "你是文档工程师，负责把代码仓库整理成清晰的说明。"},
        {"role": "user", "content": INIT_PROMPT + scan},
    ]
    printer = Printer(paint)
    t0 = time.time()
    try:
        text, _ = client.chat(messages, tools=None, on_text=printer.text)
    except OllamaError as exc:
        print(f"  失败：{exc}")
        return None
    printer.end()
    if not text.strip():
        print(paint("  模型没吐内容，换个模型再试（/model ...）", YELLOW))
        return None
    path = save_context(cfg["root"], text)
    print(paint(f"  已写入 {path}（{len(text)} 字，{time.time() - t0:.1f}s）", CYAN))
    print(paint("  本次会话已生效；下次启动自动加载。", DIM))
    return path


HELP = """命令:
  /init          扫描当前目录生成 AGENTS.md
  /clear         清空对话上下文
  /model [名字]  查看或切换模型
  /tools         列出可用工具
  /exit          退出
其它输入都当任务发给模型。"""


def repl(cfg, client, toolbox, agent, paint):
    printer = Printer(paint)
    messages = []
    thin = paint("─" * 64, DIM)
    ctx = paint("已加载 AGENTS.md", CYAN) if load_context(cfg["root"]) else \
        paint("没有 AGENTS.md，可以先跑 /init", YELLOW)
    print(thin)
    print(f"  {paint('agent', BOLD)}  {cfg['model']}   工具 {len(TOOL_SCHEMAS)} 个   "
          f"上下文 {cfg['num_ctx']}   {'思维链开' if cfg['think'] else '思维链关'}")
    print(f"  cwd    {cfg['root']}")
    print(f"  服务   {cfg['host']}")
    print(f"  上下文 {ctx}")
    print(thin)
    print(paint("  直接说要做的事。/help 看命令，/exit 退出。\n", DIM))

    while True:
        try:
            raw = input(paint("> ", BOLD)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not raw:
            continue

        if raw.startswith("/"):
            cmd, _, arg = raw.partition(" ")
            cmd = cmd.lower()
            if cmd in ("/exit", "/quit", "/q"):
                return
            if cmd == "/help":
                print(paint(HELP, DIM) + "\n")
                continue
            if cmd == "/clear":
                messages.clear()
                print(paint("  上下文已清空。\n", DIM))
                continue
            if cmd == "/tools":
                for t in TOOL_SCHEMAS:
                    fn = t["function"]
                    if fn["name"] == "shell" and not cfg["allow_shell"]:
                        continue
                    print(f"  {fn['name']:<16} {fn['description']}")
                print()
                continue
            if cmd == "/model":
                if not arg.strip():
                    names = client.tags()
                    print(f"  当前 {cfg['model']}")
                    if names:
                        print(paint("  可用: " + ", ".join(names), DIM))
                    print()
                    continue
                cfg["model"] = arg.strip()
                client.model = cfg["model"]
                messages.clear()
                check_capabilities(client, cfg, paint)
                print(paint(f"  已切到 {cfg['model']}，上下文已清空。\n", CYAN))
                continue
            if cmd == "/init":
                if run_init(cfg, client, paint):
                    agent.system_prompt = build_system_prompt(cfg, toolbox)
                print()
                continue
            print(paint(f"  不认识 {cmd}，/help 看看。\n", YELLOW))
            continue

        messages.append({"role": "user", "content": raw})
        print()
        t0 = time.time()
        try:
            answer, messages = agent.run(
                messages,
                on_text=printer.text,
                on_think=printer.think,
                on_tool=lambda n, a: print(paint(f"  ▸ {n} {str(a)[:120]}", CYAN)),
                on_result=lambda n, r: print(paint(f"    {r.splitlines()[0][:100] if r.strip() else '(空)'}", DIM)),
            )
        except OllamaError as exc:
            printer.end()
            print(paint(f"  请求失败：{exc}\n", YELLOW))
            messages.pop()
            continue
        printer.end()
        print(paint(f"  ({time.time() - t0:.1f}s · 输入 {client.prompt_tokens} tok · "
                    f"生成 {client.eval_tokens} tok)\n", DIM))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="agent",
        description="Local Ollama coding agent in your terminal.",
        epilog='examples:\n  agent\n  agent "列出当前目录的文件"\n  agent --init\n  agent -m qwen3:8b --think',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("task", nargs="*", help="task to run once; omit for interactive mode")
    ap.add_argument("-q", "--query", help="single-shot query")
    ap.add_argument("-m", "--model", help="model name (default: $OLLAMA_MODEL or qwen3.5:4b)")
    ap.add_argument("--host", help="Ollama base url (default: http://127.0.0.1:11434)")
    ap.add_argument("--num-ctx", type=int, help="context window (default 4096)")
    ap.add_argument("--max-steps", type=int, help="max tool rounds (default 12)")
    ap.add_argument("--think", action="store_true", help="enable thinking (slower)")
    ap.add_argument("--no-shell", action="store_true", help="disable the shell tool")
    ap.add_argument("--jail", action="store_true", help="restrict reads to the workspace")
    ap.add_argument("--read-all", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--write-root", action="append", help="extra writable dir (repeatable)")
    ap.add_argument("--root", help="workspace dir (default: current dir)")
    ap.add_argument("--init", action="store_true", help="scan cwd, write AGENTS.md, exit")
    ap.add_argument("--list-models", action="store_true", help="list local models and exit")
    ap.add_argument("--version", action="version", version=f"ollama-agent {__version__}")
    args = ap.parse_args(argv)

    paint = Painter()
    cfg = resolve(args)

    if not Path(cfg["root"]).is_dir():
        print(paint(f"工作目录不存在: {cfg['root']}", YELLOW), file=sys.stderr)
        return 2
    os.chdir(cfg["root"])

    client, toolbox, agent = make_stack(cfg, paint)
    check_capabilities(client, cfg, paint)

    if args.list_models:
        names = client.tags()
        if not names:
            print(paint("连不上 Ollama，或者没有模型。先跑 `ollama serve`。", YELLOW))
            return 1
        for n in names:
            caps = client.capabilities(n)
            mark = "tools" if "tools" in caps else paint("no tools", YELLOW)
            extra = ",".join(c for c in caps if c not in ("completion", "tools"))
            print(f"{n:<24} {mark:<9} {extra}")
        return 0

    if args.init:
        return 0 if run_init(cfg, client, paint) else 1

    task = (args.query or " ".join(args.task)).strip()
    if task:
        printer = Printer(paint)
        messages = [{"role": "user", "content": task}]
        try:
            agent.run(messages, on_text=printer.text, on_think=printer.think,
                      on_tool=lambda n, a: print(paint(f"  ▸ {n} {str(a)[:120]}", CYAN)),
                      on_result=lambda n, r: print(paint(f"    {r.splitlines()[0][:100] if r.strip() else '(空)'}", DIM)))
        except OllamaError as exc:
            printer.end()
            print(paint(f"请求失败：{exc}", YELLOW), file=sys.stderr)
            return 1
        printer.end()
        return 0

    repl(cfg, client, toolbox, agent, paint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
