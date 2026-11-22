"""CLI entry point: `agent` opens an interactive session, `agent "task"` runs once."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import __version__
from .config import resolve
from .install import InstallError, install as install_skills
from .llm import OllamaClient, OllamaError
from .loop import Agent
from .project_scan import AGENTS_FILE, INIT_PROMPT, load_context, save_context, scan_project
from .skills import (ensure_example, global_skills_dir, load_skills, match_skills,
                    global_skills_raw_dir, project_skills_dir, render_skills,
                    skills_raw_dir)
from .tools import TOOL_SCHEMAS, ToolBox

BOLD, DIM, CYAN, YELLOW, RESET = "\033[1m", "\033[2m", "\033[36m", "\033[33m", "\033[0m"
SKILL_CHARS = 400


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


def build_system_prompt(cfg, toolbox, allowed=None):
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
        "\n## 能力边界\n"
        "- 你没有 subagent 能力。技能里要求“派生子代理/并行代理”的步骤，直接告诉用户做不到，"
        "不要假装已经执行。\n"
        "- shell 默认走 cmd；命令里带 .sh 或以 bash 开头时自动改用 Git Bash。跑技能脚本时把 "
        "cwd 设成 skill://<技能名>，脚本路径用相对路径。\n"
        "- 技能里的附件用 read_file 读，路径写成 skill://<技能名>/<附件名>。\n"
    )
    ctx = load_context(root)
    if ctx:
        prompt += f"\n## 项目上下文（{AGENTS_FILE}）\n{ctx}\n"

    names = [getattr(x, "name", x) for x in (allowed or [])]
    if names:
        prompt += ("\n## 可用技能\n" + "、".join(names) + "\n")
        prompt += ("需要某个技能的完整步骤时，调用 use_skill(name) 取全文；"
                   "不要凭技能名猜内容。用不到就别调。\n")
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
                      write_roots=cfg["write_roots"], read_all=cfg["read_all"],
                      shell_timeout=cfg["shell_timeout"],
                      skill_roots=[skills_raw_dir(cfg["root"]), global_skills_raw_dir()],
                      bash_path=cfg.get("bash_path"))
    skills = load_skills(cfg["root"])
    agent = Agent(client, toolbox, build_system_prompt(cfg, toolbox, skills),
                  max_steps=cfg["max_steps"])
    return client, toolbox, agent, skills


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
  /skills        选择本轮会话要加载的技能（交互式）
  /tools         列出可用工具
  /exit          退出
其它输入都当任务发给模型。"""


def _log_skipped(skipped, paint):
    if skipped:
        print(paint(f"  技能总长超上限，未注入：{', '.join(skipped)}", YELLOW))


def handle_skills(cfg, agent, toolbox, skills, manual, paint):
    """交互式选择本轮会话要加载的技能。单次提示，不做循环，避免吃掉后续输入。"""
    skills = load_skills(cfg["root"])

    if not skills:
        print(paint(f"  没有技能。把 .md 放进：{project_skills_dir(cfg['root'])}", DIM))
        print(paint(f"  或全局目录：{global_skills_dir()}", DIM))
        print(paint("  现在生成一个示例？[y/N]: ", DIM), end="")
        try:
            ans = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return skills, manual
        if ans == "y":
            path = ensure_example(cfg["root"])
            if path:
                print(paint(f"  已创建 {path}", CYAN))
                skills = load_skills(cfg["root"])
        if not skills:
            print()
            return skills, manual

    manual = {n: True for n in manual if any(s.name == n for s in skills)}
    print()
    width = min(max((len(s.name) for s in skills), default=10), 24)
    for i, s in enumerate(skills, 1):
        mark = paint("✓", CYAN) if s.name in manual else " "
        name = s.name if len(s.name) <= width else s.name[:width - 1] + "…"
        print(f"  [{mark}] {i:>2}. {name:<{width}} {s.size:>4}字  {s.scope:<7} {s.description[:36]}")
    print(paint(f"  已加载 {len(manual)}/{len(skills)}；手动加载的执行时不再确认", DIM))
    print(paint("  输入编号切换（空格分隔），回车返回: ", DIM), end="")

    try:
        line = input().strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return skills, manual
    print()

    if line:
        for token in line.replace(",", " ").split():
            if not token.isdigit():
                continue
            idx = int(token) - 1
            if 0 <= idx < len(skills):
                name = skills[idx].name
                if name in manual:
                    manual.pop(name, None)
                else:
                    manual[name] = True

    picked = [s for name in manual for s in skills if s.name == name]
    toolbox.allowed_skills = {s.name for s in picked}
    agent.system_prompt = build_system_prompt(cfg, toolbox, [s.name for s in picked])
    names = "、".join(s.name for s in picked) or "无"
    print(paint(f"  已启用：{names}", CYAN))
    print(paint("  执行时我会按需读取完整内容，不再预塞进提示。", DIM))
    print()
    return skills, manual


def prepare_skills(cfg, agent, toolbox, skills, task, manual, confirmed, declined, paint):
    """决定本轮哪些技能获准使用：手动启用的直接算，自动匹配的先问一次。"""
    approved = [n for n in manual if any(s.name == n for s in skills)]

    matched = [s for s in match_skills(skills, task)
               if s.name not in approved and s.name not in declined]
    if matched:
        label = "、".join(s.name for s in matched)
        print(paint(f"  这轮想用技能：{label}", CYAN))
        try:
            ans = input(paint("  允许？[Y/n] ", BOLD)).strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        if ans in ("n", "no", "不", "否"):
            declined.update(s.name for s in matched)
            print(paint("  已跳过，用基础规则执行。", DIM))
        else:
            for skill in matched:
                approved.append(skill.name)
                confirmed.add(skill.name)

    approved = [n for n in dict.fromkeys(approved + [c for c in confirmed if c not in approved])
                if any(s.name == n for s in skills)]
    toolbox.allowed_skills = set(approved)
    agent.system_prompt = build_system_prompt(cfg, toolbox, approved)
    return approved


def repl(cfg, client, toolbox, agent, paint, skills):
    manual, confirmed, declined = {}, set(), set()
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
    if skills:
        print(f"  技能   {len(skills)} 个已安装，未启用（/skills 选）")
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
        if raw.lower() in ("exit", "quit", ":q"):
            return

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
            if cmd == "/skills":
                if arg.strip().startswith("install "):
                    url = arg.strip()[len("install "):].strip()
                    try:
                        res = install_skills(url, project_skills_dir(cfg["root"]),
                                             SKILL_CHARS, False)
                        print(paint(f"  写入 {len(res['added'])} 个技能到 {res['dest']}", CYAN))
                        for name, size in res["added"]:
                            print(f"    + {name:<28} {size:>4}字")
                        if res["skipped"]:
                            print(paint("  跳过：" + "、".join(n for n, _ in res["skipped"]), YELLOW))
                        skills = load_skills(cfg["root"])
                    except InstallError as exc:
                        print(paint(f"  安装失败：{exc}", YELLOW))
                    print()
                    continue
                skills, manual = handle_skills(cfg, agent, toolbox, skills, manual, paint)
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

        prepare_skills(cfg, agent, toolbox, skills, raw, manual, confirmed,
                       declined, paint)
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
    ap.add_argument("--shell-timeout", type=int,
                    help="seconds before a shell command is killed (default 180)")
    ap.add_argument("--think", action="store_true", help="enable thinking (slower)")
    ap.add_argument("--no-shell", action="store_true", help="disable the shell tool")
    ap.add_argument("--jail", action="store_true", help="restrict reads to the workspace")
    ap.add_argument("--read-all", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--write-root", action="append", help="extra writable dir (repeatable)")
    ap.add_argument("--root", help="workspace dir (default: current dir)")
    ap.add_argument("--init", action="store_true", help="scan cwd, write AGENTS.md, exit")
    ap.add_argument("--list-models", action="store_true", help="list local models and exit")
    ap.add_argument("--install-skills", metavar="URL",
                    help="download SKILL.md files from a GitHub repo into the skills dir")
    ap.add_argument("--skills-global", action="store_true",
                    help="with --install-skills: install to the global skills dir")
    ap.add_argument("--skill-chars", type=int, default=SKILL_CHARS,
                    help="per-skill body limit when installing (default 400)")
    ap.add_argument("--force", action="store_true", help="overwrite existing skill files")
    ap.add_argument("--version", action="version", version=f"ollama-agent {__version__}")
    args = ap.parse_args(argv)

    paint = Painter()
    cfg = resolve(args)

    if not Path(cfg["root"]).is_dir():
        print(paint(f"工作目录不存在: {cfg['root']}", YELLOW), file=sys.stderr)
        return 2
    os.chdir(cfg["root"])

    client, toolbox, agent, skills = make_stack(cfg, paint)
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

    if args.install_skills:
        dest = global_skills_dir() if args.skills_global else project_skills_dir(cfg["root"])
        try:
            res = install_skills(args.install_skills, dest, args.skill_chars, args.force)
        except InstallError as exc:
            print(paint(str(exc), YELLOW), file=sys.stderr)
            return 1
        print(paint(f"  从 {res['repo']} 读取 {res['total']} 个技能", DIM))
        print(f"  写入 {len(res['added'])} 个 -> {res['dest']}")
        for name, size in res["added"]:
            print(f"    + {name:<28} {size:>4}字")
        if res["skipped"]:
            print(paint("  跳过 " + "、".join(f"{n}（{r}）" for n, r in res["skipped"]), YELLOW))
        print(paint("  下一步：进 agent 敲 /skills 选择要加载的\n", DIM))
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

    repl(cfg, client, toolbox, agent, paint, skills)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
