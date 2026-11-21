"""Tools the agent can call. Pure stdlib, works on Windows/macOS/Linux."""

from __future__ import annotations

import ipaddress
import json
import locale
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from html import unescape as html_unescape
from pathlib import Path

MAX_FETCH_BYTES = 2_000_000
FETCH_TIMEOUT = 25
SHELL_TIMEOUT = 60

# 只挡破坏性操作。判权限的事交给操作系统，别在这里层层设卡。
SHELL_BLACKLIST = [
    r"\brm\s+(-[a-zA-Z]*\s+)*-[a-zA-Z]*[rf]", r"\brmdir\s+/s", r"\bdel\s+/[fsq]",
    r"\bformat\s+[a-zA-Z]:", r"\bshutdown\b", r"\breboot\b", r"\bdiskpart\b",
    r"\bmkfs(\.|/|\s)", r"\bdd\s+if=", r"Remove-Item.*-Recurse.*-Force",
    r":\(\)\s*\{", r"\breg\s+delete\b", r"\bbcdedit\b", r"\bcipher\s+/w",
    r"\bchkdsk\s+/f", r"\bsudo\s+rm\b", r"\bgit\s+clean\s+-[a-z]*f",
]


def _schema(props, required):
    return {"type": "object", "properties": props, "required": required}


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出目录下的文件和子目录。想知道有哪些文件时先用它。",
            "parameters": _schema({"path": {"type": "string", "description": "目录路径，默认当前目录"}}, ["path"]),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本文件内容。大文件用 offset/limit 分段读。",
            "parameters": _schema({
                "path": {"type": "string", "description": "文件路径"},
                "offset": {"type": "integer", "description": "起始行号，从 1 开始"},
                "limit": {"type": "integer", "description": "最多读多少行，默认 200"},
            }, ["path"]),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "把内容写入文本文件，覆盖同名文件，父目录自动创建。",
            "parameters": _schema({
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "完整文件内容"},
            }, ["path", "content"]),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_in_file",
            "description": "把文件里的一段文本替换成另一段。改代码优先用它，比整文件重写安全。",
            "parameters": _schema({
                "path": {"type": "string", "description": "文件路径"},
                "old": {"type": "string", "description": "要替换的原文，必须唯一匹配"},
                "new": {"type": "string", "description": "替换后的文本，传空字符串表示删除"},
            }, ["path", "old", "new"]),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "执行一条 shell 命令并返回输出。可跑 python/pip/git/node 等普通命令。",
            "parameters": _schema({"command": {"type": "string", "description": "要执行的命令"}}, ["command"]),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "用 Bing 搜索，返回标题/链接/摘要。需要最新信息时先搜，再看具体页面。",
            "parameters": _schema({
                "query": {"type": "string", "description": "搜索关键词"},
                "top_k": {"type": "integer", "description": "返回几条，默认 5，最多 10"},
            }, ["query"]),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "抓取公网 http/https 链接的文本内容，HTML 会自动剥成纯文本。",
            "parameters": _schema({
                "url": {"type": "string", "description": "完整链接，必须带 http:// 或 https://"},
                "max_chars": {"type": "integer", "description": "最多返回多少字符，默认 4000"},
            }, ["url"]),
        },
    },
]

SHELL_TOOL_NAMES = {"shell"}


def _is_private_host(host: str) -> bool:
    if not host:
        return True
    low = host.lower()
    if low in ("localhost", "0.0.0.0", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(low)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            return True
    return False


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = html_unescape(text)
    text = re.sub(r"[^\S\n]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _plain(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_unescape(re.sub(r"(?s)<[^>]+>", "", fragment))).strip()


def _decode_output(raw: bytes) -> str:
    """Windows 控制台多半是 GBK，Unix 是 UTF-8。两边都试一遍。"""
    for enc in ("utf-8", locale.getpreferredencoding(False) or "utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


class ToolError(Exception):
    pass


class ToolBox:
    """工具执行器。读默认不设限，写只允许落在给定目录里。"""

    def __init__(self, root: Path, allow_shell: bool = True,
                 write_roots=None, read_all: bool = True,
                 shell_timeout: int = SHELL_TIMEOUT, fetch_timeout: int = FETCH_TIMEOUT):
        self.root = Path(root).expanduser().resolve()
        self.allow_shell = allow_shell
        self.read_all = read_all
        self.write_roots = [Path(p).expanduser().resolve() for p in (write_roots or [])]
        self.shell_timeout = shell_timeout
        self.fetch_timeout = fetch_timeout

    # ---------------------------------------------------------------- path

    def _resolve(self, rel, write: bool = False) -> Path:
        if rel is None or not str(rel).strip():
            raise ToolError("path 不能为空")
        p = Path(str(rel)).expanduser()
        full = (self.root / p).resolve() if not p.is_absolute() else p.resolve()

        if write:
            for base in [self.root, *self.write_roots]:
                try:
                    full.relative_to(base)
                    return full
                except ValueError:
                    continue
            scope = ", ".join(str(x) for x in [self.root, *self.write_roots])
            raise ToolError(f"没有写权限: {full}。可写范围: {scope}")

        if self.read_all:
            return full
        try:
            full.relative_to(self.root)
        except ValueError:
            raise ToolError(f"路径越界，只能操作 {self.root} 内的文件: {full}")
        return full

    def _show(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    # ---------------------------------------------------------------- tools

    def list_dir(self, path="."):
        d = self._resolve(path or ".")
        if not d.exists():
            return f"目录不存在: {path}"
        if not d.is_dir():
            return f"不是目录: {path}"
        rows = []
        for item in sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
            if item.is_dir():
                rows.append(f"DIR   {item.name}/")
            else:
                try:
                    size = item.stat().st_size
                except OSError:
                    size = 0
                rows.append(f"FILE  {item.name}  {size} B")
            if len(rows) >= 200:
                rows.append("...（还有更多，已截断）")
                break
        return "\n".join(rows) or "(空目录)"

    def read_file(self, path, offset=1, limit=200):
        f = self._resolve(path)
        if not f.exists():
            return f"文件不存在: {path}"
        if f.is_dir():
            return f"这是目录不是文件: {path}"
        try:
            offset = max(1, int(offset or 1))
            limit = max(1, min(int(limit or 200), 2000))
        except (TypeError, ValueError):
            offset, limit = 1, 200
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError as exc:
            return f"读不了: {exc}"
        chunk = lines[offset - 1: offset - 1 + limit]
        body = "".join(chunk).rstrip("\n")
        head = f"[{path} 第 {offset}-{offset + len(chunk) - 1} 行，共 {len(lines)} 行]\n"
        return head + (body or "(这一段是空的)")

    def write_file(self, path, content):
        f = self._resolve(path, write=True)
        f.parent.mkdir(parents=True, exist_ok=True)
        text = "" if content is None else str(content)
        f.write_text(text, encoding="utf-8")
        return f"已写入 {self._show(f)}，{len(text)} 字符，{text.count(chr(10)) + 1} 行"

    def replace_in_file(self, path, old, new):
        f = self._resolve(path, write=True)
        if not f.exists():
            return f"文件不存在: {path}"
        text = f.read_text(encoding="utf-8", errors="replace")
        old_s, new_s = str(old), ("" if new is None else str(new))
        count = text.count(old_s)
        if count == 0:
            return "没找到要替换的原文。先用 read_file 看清实际内容再改。"
        if count > 1:
            return f"原文出现了 {count} 次，不唯一。请给更长的上下文让它唯一。"
        f.write_text(text.replace(old_s, new_s, 1), encoding="utf-8")
        return f"已替换 {self._show(f)} 里的 1 处内容"

    def shell(self, command):
        if not self.allow_shell:
            return "shell 工具已禁用（--no-shell）。只能使用文件工具。"
        cmd = str(command or "").strip()
        if not cmd:
            return "command 不能为空"
        for pat in SHELL_BLACKLIST:
            if re.search(pat, cmd, re.IGNORECASE):
                return f"命令被拒绝（命中破坏性操作黑名单）: {cmd}"
        try:
            proc = subprocess.run(cmd, shell=True, cwd=str(self.root), capture_output=True,
                                  timeout=self.shell_timeout)
        except subprocess.TimeoutExpired:
            return f"命令超时（>{self.shell_timeout}s）: {cmd}"
        except OSError as exc:
            return f"执行失败: {exc}"
        out = _decode_output(proc.stdout or b"")
        err = _decode_output(proc.stderr or b"")
        text = out + (("\n[stderr]\n" + err) if err.strip() else "")
        text = text.strip() or "(无输出)"
        if proc.returncode != 0:
            text = f"[exit {proc.returncode}]\n{text}"
        if len(text) > 8000:
            text = text[:8000] + "\n...（输出过长，已截断）"
        return text

    def web_search(self, query, top_k=5):
        q = str(query or "").strip()
        if not q:
            return "query 不能为空"
        try:
            n = max(1, min(int(top_k or 5), 10))
        except (TypeError, ValueError):
            n = 5

        params = urllib.parse.urlencode({"q": q, "setlang": "zh-CN", "count": str(n * 2)})
        ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        last_err = "未知错误"

        for host in ("https://cn.bing.com/search?", "https://www.bing.com/search?"):
            url = host + params
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": ua,
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml",
                })
                with urllib.request.urlopen(req, timeout=self.fetch_timeout) as resp:
                    page = resp.read(MAX_FETCH_BYTES).decode("utf-8", "replace")
            except Exception as exc:
                last_err = f"{urllib.parse.urlparse(host).netloc}: {type(exc).__name__} {exc}"
                continue

            items = self._parse_bing(page)
            if not items:
                last_err = f"{urllib.parse.urlparse(host).netloc}: 没解析到结果"
                continue
            lines = [f"[Bing · {len(items)} 条 · 显示前 {min(n, len(items))} 条]"]
            for i, (title, link, snip) in enumerate(items[:n], 1):
                lines.append(f"{i}. {title}\n   {link}\n   {snip}")
            return "\n".join(lines)

        return f"搜索失败（{q}）：{last_err}。换关键词，或直接 fetch_url 已知网址。"

    @staticmethod
    def _parse_bing(page):
        out = []
        for blk in re.findall(r'<li class="b_algo".*?</li>', page, re.S):
            h = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', blk, re.S)
            if not h:
                continue
            link = html_unescape(h.group(1))
            if not link.startswith("http"):
                continue
            title = _plain(h.group(2))
            p = re.search(r"<p[^>]*>(.*?)</p>", blk, re.S)
            snip = _plain(p.group(1)) if p else ""
            if title:
                out.append((title[:90], link, snip[:180]))
        return out

    def fetch_url(self, url, max_chars=4000):
        raw_url = str(url or "").strip()
        if not re.match(r"^https?://", raw_url, re.I):
            return "只支持 http/https 链接"

        host = urllib.parse.urlparse(raw_url).hostname or ""
        if _is_private_host(host):
            return f"拒绝访问内网或本机地址: {host}"

        req = urllib.request.Request(raw_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) agent/1.0",
            "Accept": "text/html,application/json,text/plain,*/*",
        })
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.fetch_timeout) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                chunk = resp.read(MAX_FETCH_BYTES)
                status = resp.status
                final = resp.geturl()
        except urllib.error.HTTPError as e:
            return f"HTTP {e.code} {e.reason}: {raw_url}"
        except urllib.error.URLError as e:
            return f"连不上 {host}: {e.reason}"
        except (socket.timeout, TimeoutError):
            return f"超时（>{self.fetch_timeout}s）: {raw_url}"
        except Exception as e:
            return f"抓取失败 {type(e).__name__}: {e}"

        if not chunk:
            return f"抓到了，但内容为空（HTTP {status}）"
        if b"\x00" in chunk[:1024] or ctype.startswith(("image/", "audio/", "video/", "font/")):
            return f"这是二进制内容（{ctype or '未知'}，{len(chunk)} 字节），只返回文本。"

        charset = "utf-8"
        m = re.search(r"charset=([\w-]+)", ctype)
        if m:
            charset = m.group(1)
        try:
            text = chunk.decode(charset, errors="replace")
        except LookupError:
            text = chunk.decode("utf-8", errors="replace")

        if "html" in ctype or "<html" in text[:500].lower():
            text = _html_to_text(text)

        text = text.strip()
        limit = max(200, min(int(max_chars or 4000), 8000))
        head = f"[HTTP {status} · {ctype or 'text'} · {len(chunk)} 字节 · {time.time() - t0:.1f}s]"
        if final and final != raw_url:
            head += f"\n[重定向到 {final}]"
        if not text:
            return f"{head}\n（没有可提取的文本，可能是 JS 渲染的空壳页）"
        if len(text) < 600:
            text += "\n（正文很少，多半是动态页面。换个来源，别重复抓）"
        if len(text) > limit:
            return f"{head}\n{text[:limit]}\n...（共 {len(text)} 字符，已截断）"
        return f"{head}\n{text}"

    # ---------------------------------------------------------------- dispatch

    def dispatch(self, name, args):
        table = {
            "list_dir": self.list_dir,
            "read_file": self.read_file,
            "write_file": self.write_file,
            "replace_in_file": self.replace_in_file,
            "shell": self.shell,
            "web_search": self.web_search,
            "fetch_url": self.fetch_url,
        }
        fn = table.get(name)
        if fn is None:
            return f"没有这个工具: {name}"
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip().startswith("{") else {}
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        try:
            return fn(**args)
        except ToolError as exc:
            return f"工具拒绝执行: {exc}"
        except TypeError as exc:
            return f"参数错误: {exc}"
        except Exception as exc:  # noqa: BLE001
            return f"工具异常 {type(exc).__name__}: {exc}"
