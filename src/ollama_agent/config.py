"""Config loading: CLI args > env vars > config file > defaults."""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "ollama-agent"


def config_path() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME / "config.json"


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
    num_ctx = int(args.num_ctx or env.get("OLLAMA_NUM_CTX") or cfg.get("num_ctx") or 4096)
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

    return {
        "model": model,
        "host": host,
        "num_ctx": num_ctx,
        "max_steps": max_steps,
        "shell_timeout": shell_timeout,
        "think": think,
        "read_all": read_all,
        "write_roots": write_roots,
        "allow_shell": allow_shell,
        "root": str(Path(args.root or os.getcwd()).expanduser().resolve()),
    }
