"""Project scanning and AGENTS.md generation/loading."""

from __future__ import annotations

import json
import os
from pathlib import Path

AGENTS_FILE = "AGENTS.md"
CONTEXT_LIMIT = 1500

SKIP_DIRS = {
    ".git", ".svn", ".hg", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".idea", ".vscode", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist",
    "build", "target", ".next", ".nuxt", "workspace", "site-packages", ".tox",
    "coverage", ".coverage", "vendor", "Pods",
}

MARKERS = [
    ("package.json", "Node project"), ("pyproject.toml", "Python pyproject"),
    ("requirements.txt", "Python requirements"), ("setup.py", "Python setup.py"),
    ("Pipfile", "Python Pipfile"), ("Cargo.toml", "Rust project"), ("go.mod", "Go module"),
    ("pom.xml", "Java Maven"), ("build.gradle", "Java Gradle"), ("Gemfile", "Ruby"),
    ("composer.json", "PHP"), ("Makefile", "Makefile"), ("Dockerfile", "Docker"),
    ("docker-compose.yml", "Docker Compose"), ("CMakeLists.txt", "CMake"),
    (".git", "git repo"), ("AGENTS.md", "existing AGENTS.md"),
]

INIT_PROMPT = """根据下面的扫描结果，写一份给 AI 编码助手看的项目说明（AGENTS.md）。

要求：
- 第一行 `# 项目名`，紧接着一句话说清这个项目是干什么的
- 技术栈与主要依赖
- 目录结构要点：只列关键目录及其职责，不要贴完整文件树
- 怎么运行、怎么测试：只写能从扫描结果里看出来的，看不出就写「未发现」
- 4～6 条工作约定或注意事项
- 只用扫描结果里的事实，不要编造。不要写「根据扫描结果」这类废话
- 直接输出 markdown 正文，全文不要用代码块包裹

扫描结果：
"""


def _head(path, limit=700):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit).strip()
    except OSError:
        return ""


def scan_project(root, max_depth=3, max_files=600):
    root = str(Path(root).resolve())
    ext_count, tree_lines, top_files = {}, [], []
    total = 0

    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        dirnames[:] = sorted(d for d in dirnames if d.lower() not in SKIP_DIRS)
        if depth >= max_depth:
            dirnames[:] = []

        for name in filenames:
            total += 1
            ext = os.path.splitext(name)[1].lower() or "(no ext)"
            ext_count[ext] = ext_count.get(ext, 0) + 1
            if depth <= 1 and total <= max_files:
                top_files.append(os.path.join(rel, name).lstrip(".\\/"))
            if total >= max_files:
                break

        if depth <= 2 and (filenames or dirnames):
            line = f"{rel if rel != '.' else '.'}/"
            if dirnames:
                line += "  dirs: " + ", ".join(dirnames[:6])
            if filenames:
                line += "  files: " + ", ".join(sorted(filenames)[:6])
            if len(line) < 220:
                tree_lines.append(line)
        if total >= max_files:
            break

    langs = sorted(ext_count.items(), key=lambda kv: -kv[1])[:8]
    langs_text = ", ".join(f"{e}×{n}" for e, n in langs)

    markers = []
    try:
        level1 = [root] + [os.path.join(root, d) for d in sorted(os.listdir(root))
                           if os.path.isdir(os.path.join(root, d)) and d.lower() not in SKIP_DIRS]
    except OSError:
        level1 = [root]
    for base in level1:
        for name, label in MARKERS:
            if os.path.exists(os.path.join(base, name)):
                rel = os.path.relpath(os.path.join(base, name), root)
                markers.append(f"{rel} ({label})")

    hints = []
    for readme in ("README.md", "readme.md", "README_CN.md", "README.rst", "README.txt"):
        p = os.path.join(root, readme)
        if os.path.exists(p):
            hints.append(f"--- {readme} head ---\n{_head(p, 600)}")
            break
    pkg = os.path.join(root, "package.json")
    if os.path.exists(pkg):
        try:
            data = json.loads(_head(pkg, 4000) or "{}")
            scripts = ", ".join((data.get("scripts") or {}).keys())
            deps = ", ".join(list((data.get("dependencies") or {}).keys())[:8])
            hints.append(f"--- package.json ---\nscripts: {scripts or 'none'}\ndeps: {deps or 'none'}")
        except Exception:
            pass
    req = os.path.join(root, "requirements.txt")
    if os.path.exists(req):
        hints.append("--- requirements.txt head ---\n" + _head(req, 300))
    pyproj = os.path.join(root, "pyproject.toml")
    if os.path.exists(pyproj):
        hints.append("--- pyproject.toml head ---\n" + _head(pyproj, 500))

    parts = [
        f"workspace: {root}",
        f"files (deps dirs ignored): {total}",
        f"extensions: {langs_text or 'none'}",
        f"project markers: {', '.join(markers) or 'none found'}",
    ]
    if top_files:
        parts.append("top-level files: " + ", ".join(top_files[:20]))
    if tree_lines:
        parts.append("tree:\n" + "\n".join(tree_lines[:20]))
    if hints:
        parts.append("\n".join(hints))
    return "\n\n".join(parts)


def load_context(root, limit=CONTEXT_LIMIT) -> str:
    p = Path(root) / AGENTS_FILE
    if not p.exists():
        return ""
    return _head(p, limit).strip()


def save_context(root, text) -> Path:
    p = Path(root) / AGENTS_FILE
    if p.exists():
        try:
            p.replace(p.with_suffix(".md.bak"))
        except OSError:
            pass
    p.write_text(str(text).strip() + "\n", encoding="utf-8")
    return p
