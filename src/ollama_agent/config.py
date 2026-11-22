"""Config loading: CLI args > env vars > config file > defaults."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

APP_NAME = "ollama-agent"


def find_bash():
    """技能里的 .sh 需要真 bash。Windows 上找 Git Bash。"""
    if os.name != "nt":
        return None
    env = os.environ.get("AGENT_BASH")
    if env and Path(env).exists():
        return env
    candidates = [
        r"D:/git/Git/bin/bash.exe",
        r"C:/Program Files/Git/bin/bash.exe",
        r"C:/Program Files (x86)/Git/bin/bash.exe",
        str(Path.home() / "AppData" / "Local" / "Programs" / "Git" / "bin" / "bash.exe"),
    ]
    for cand in candidates:
        if Path(cand).exists():
            return cand
    return shutil.which("bash")


def config_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME / "config.json"


def save_file(data: dict) -> Path:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def load_file() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def default_write_roots() -> list:
    """默认只允许写工作目录和家目录，别的盘要靠 --write-root 显式加。"""
    roots = [str(Path.home())]
    if os.name == "nt":
        # Windows 上再放开当前目录所在盘的盘符，避免到处都是“没权限”
        try:
            drive = Path.cwd().drive
            if drive:
                roots.append(drive + os.sep)
        except OSError:
            pass
    return roots


def resolve(args) -> dict:
    """把 argparse 结果 + env + 配置文件合成一份设置。"""
    cfg = load_file()

    env = os.environ
    model = (args.model or env.get("OLLAMA_MODEL") or cfg.get("model")
             or "qwen3.5:4b")
    host = (args.host or env.get("OLLAMA_HOST") or cfg.get("host")
            or "http://127.0.0.1:11434")
    num_ctx = int(args.num_ctx or env.get("OLLAMA_NUM_CTX") or cfg.get("num_ctx") or 8192)
    max_steps = int(args.max_steps or cfg.get("max_steps") or 12)
    shell_timeout = int(args.shell_timeout or env.get("AGENT_SHELL_TIMEOUT")
                        or cfg.get("shell_timeout") or 180)
    think = bool(args.think or cfg.get("think", False))

    read_all = not args.jail
    if cfg.get("jail") is True:
        read_all = False

    if args.write_root:
        write_roots = [str(Path(p).expanduser()) for p in args.write_root]
    elif cfg.get("write_roots"):
        write_roots = [str(Path(p).expanduser()) for p in cfg["write_roots"]]
    else:
        write_roots = default_write_roots()

    allow_shell = not args.no_shell
    if cfg.get("allow_shell") is False:
        allow_shell = False

    bash = cfg.get("bash_path") or find_bash()

    return {
        "model": model,
        "host": host,
        "num_ctx": num_ctx,
        "max_steps": max_steps,
        "shell_timeout": shell_timeout,
        "bash_path": bash,
        "think": think,
        "read_all": read_all,
        "write_roots": write_roots,
        "allow_shell": allow_shell,
        "root": str(Path(args.root or os.getcwd()).expanduser().resolve()),
    }
