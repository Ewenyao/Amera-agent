"""Install skills from a GitHub repo (any repo containing SKILL.md files).

superpowers and friends ship `skills/<name>/SKILL.md` with YAML front matter and
several kilobytes of prose. Our prompt budget is 800 characters total, so each
skill is condensed to a rule list before it lands in .agent/skills.
"""

from __future__ import annotations

import io
import re
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ollama-agent/0.1"
BODY_LIMIT = 400
DESC_LIMIT = 110
TIMEOUT = 60


class InstallError(Exception):
    pass


def parse_repo(url: str):
    m = re.search(r"github\.com[/:]([^/\s]+)/([^/\s#?]+)", url or "")
    if not m:
        raise InstallError(f"看不懂这个地址: {url}\n需要形如 https://github.com/owner/repo")
    return m.group(1), m.group(2).removesuffix(".git")


def download_zip(owner: str, repo: str) -> bytes:
    errors = []
    for branch in ("main", "master"):
        url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            errors.append(f"{branch}: HTTP {exc.code}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{branch}: {type(exc).__name__} {exc}")
    raise InstallError("下载失败：" + "；".join(errors)
                       + "\n（国内环境先确认 codeload.github.com 可达）")


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.;:—-") + "…"


SKIP_SECTIONS = re.compile(r"when to use|when not|don'?t use|red flags", re.I)
BULLET = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.*)$")


def _clean(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = text.replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def _sections(body: str):
    out, head, buf = [], "", []
    for line in body.splitlines():
        if line.startswith("## "):
            out.append((head, buf))
            head, buf = line[3:].strip(), []
        elif line.startswith("# "):
            continue
        else:
            buf.append(line)
    out.append((head, buf))
    return out


def _fenced(lines):
    """代码块里的内容往往是硬规则（Iron Law 那种）。"""
    out, buf, inside = [], [], False
    for line in lines:
        if line.strip().startswith("```"):
            if inside:
                text = _clean(" ".join(buf))
                if 8 <= len(text) <= 160:
                    out.append(text)
                buf, inside = [], False
            else:
                inside = True
            continue
        if inside:
            buf.append(line.strip())
    return out


def _paragraphs(lines):
    out, buf = [], []
    for line in lines:
        if not line.strip() or BULLET.match(line):
            if buf:
                out.append(_clean(" ".join(buf)))
                buf = []
        else:
            buf.append(line.strip())
    if buf:
        out.append(_clean(" ".join(buf)))
    return [p for p in out if len(p) >= 30]


def _bullets(lines):
    out, cur = [], None
    for line in lines:
        m = BULLET.match(line)
        if m:
            cur = [m.group(2).strip()]
            out.append(cur)
        elif cur is not None and line.strip() and not line.startswith("#"):
            cur.append(line.strip())
        elif not line.strip():
            cur = None
    return [_clean(" ".join(x)) for x in out if 8 <= len(_clean(" ".join(x))) <= 160]


KEY_SECTION = re.compile(r"law|principle|rule|core", re.I)
STRONG = re.compile(r"\b(must|never|always|principle|first)\b|必须|不要|一律", re.I)


def condense(desc: str, body: str, limit: int = BODY_LIMIT) -> str:
    """只要方法论骨架：铁律代码块 + 每个关键小节的第一段，不塞零碎条目。"""
    sections = _sections(body or "")
    parts, used, seen = [], 0, set()

    def add(text: str) -> bool:
        nonlocal used
        text = (text or "").strip()
        key = text.lower()
        if not text or key in seen or used + len(text) + 1 > limit:
            return False
        seen.add(key)
        parts.append(text)
        used += len(text) + 1
        return True

    # 1) 铁律类小节里的代码块（"NO FIXES WITHOUT..." 这种）
    for head, lines in sections:
        if KEY_SECTION.search(head):
            for rule in _fenced(lines):
                add(rule)

    # 2) 每个保留小节的第一段，优先含 must/never/always 的那段
    for head, lines in sections:
        if SKIP_SECTIONS.search(head):
            continue
        paras = _paragraphs(lines)
        if not paras:
            continue
        strong = [p for p in paras if STRONG.search(p)]
        add(strong[0] if strong else paras[0])
        if len(parts) >= 3:
            break

    return "\n".join(parts)[:limit]


def split_frontmatter(text: str):
    name, desc, body = "", "", text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip().lower()
            val = val.strip().strip('"').strip("'")
            if key == "name" and val:
                name = val
            elif key in ("description", "desc") and val:
                desc = val
        body = text[m.end():]
    return name, desc, body


def extract(zip_bytes: bytes):
    items = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.lower().endswith("skill.md"):
                continue
            try:
                text = zf.read(info).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                continue
            name, desc, body = split_frontmatter(text)
            if not name:
                parts = info.filename.split("/")
                name = parts[-2] if len(parts) > 1 else Path(info.filename).stem
            parts = info.filename.split("/")
            prefix = "/".join(parts[:-1]) + "/"
            items.append({"name": name.strip(), "desc": desc.strip(), "body": body,
                          "prefix": prefix, "dirname": parts[-2] if len(parts) > 1 else name})
    return items


def _copy_raw(zip_bytes: bytes, prefix: str, target: Path):
    """把技能目录下的所有文件搬过来，脚本和模板要用。"""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            if info.is_dir() or not info.filename.startswith(prefix):
                continue
            rel = info.filename[len(prefix):]
            if not rel or ".." in rel.split("/"):
                continue
            dst = target / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(zf.read(info))
            except OSError:
                continue


def install(url: str, dest: Path, limit: int = BODY_LIMIT, force: bool = False,
            dry_run: bool = False, raw_dest: Path = None):
    owner, repo = parse_repo(url)
    raw = download_zip(owner, repo)
    items = extract(raw)
    if not items:
        raise InstallError(f"{owner}/{repo} 里没找到 SKILL.md")

    dest = Path(dest)
    raw_dest = Path(raw_dest) if raw_dest else dest.parent / "skills-raw"
    added, skipped = [], []
    seen = set()

    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    for item in items:
        name = item["name"].replace("/", "-").replace("\\", "-")
        if not name or name in seen:
            continue
        seen.add(name)
        body = condense("", item["body"], limit)
        if not body:
            skipped.append((name, "内容为空"))
            continue
        target = dest / f"{name}.md"
        if target.exists() and not force:
            skipped.append((name, "已存在"))
            continue
        raw_dir = raw_dest / name
        if not dry_run:
            _copy_raw(raw, item["prefix"], raw_dir)
            target.write_text(
                "---\n"
                f"name: {name}\n"
                f"description: {clip(item['desc'], DESC_LIMIT)}\n"
                f"source: https://github.com/{owner}/{repo}\n"
                "---\n"
                f"{body}\n",
                encoding="utf-8",
            )
        added.append((name, len(body)))

    return {"repo": f"{owner}/{repo}", "dest": dest, "added": added, "skipped": skipped,
            "total": len(items)}
