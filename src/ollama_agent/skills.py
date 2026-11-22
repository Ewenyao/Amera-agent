"""Skills: markdown snippets the user can toggle on, injected into the system prompt.

Two scopes:
  project  <workspace>/.agent/skills/<name>.md
  global   <config dir>/skills/<name>.md      (e.g. ~/.config/ollama-agent/skills)

A skill file may start with YAML-ish front matter:

    ---
    name: commit
    description: 生成 Conventional Commits 格式的提交信息
    ---
    正文规则...

Project skills win when names collide.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import config_path

RENDER_LIMIT = 800  # 技能正文合计上限，别把 4096 的窗口吃光


@dataclass
class Skill:
    name: str
    description: str
    body: str
    scope: str
    path: Path
    enabled: bool = False

    @property
    def size(self) -> int:
        return len(self.body)

    def render(self) -> str:
        head = f"### {self.name}"
        if self.description:
            head += f" — {self.description}"
        return f"{head}\n{self.body.strip()}"


def global_skills_dir() -> Path:
    return config_path().parent / "skills"


def project_skills_dir(root) -> Path:
    return Path(root) / ".agent" / "skills"


def _parse(text: str, fallback_name: str):
    name, desc, body = fallback_name, "", text
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
    return name, desc, body.strip()


def load_skills(root, enabled=None) -> list:
    """先读全局，再用项目级覆盖同名项。"""
    enabled = set(enabled or [])
    found = {}

    for scope, directory in (("global", global_skills_dir()),
                             ("project", project_skills_dir(root))):
        try:
            files = sorted(directory.glob("*.md"))
        except OSError:
            continue
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name, desc, body = _parse(text, path.stem)
            if not body:
                continue
            found[name] = Skill(name=name, description=desc, body=body,
                                scope=scope, path=path, enabled=name in enabled)

    return sorted(found.values(), key=lambda s: (s.scope != "project", s.name))


def render_enabled(skills, limit: int = RENDER_LIMIT):
    """拼成系统提示里的一段。超出上限的会被跳过，返回 (文本, 被跳过的名字)。"""
    active = [s for s in skills if s.enabled]
    if not active:
        return "", []

    blocks, used, skipped = [], 0, []
    for skill in active:
        text = skill.render()
        if used + len(text) > limit:
            skipped.append(skill.name)
            continue
        blocks.append(text)
        used += len(text)

    if not blocks:
        return "", skipped
    return "## 已启用的技能\n" + "\n\n".join(blocks) + "\n", skipped


# ---------------------------------------------------------------- 相关性筛选

STOP_TERMS = {
    "the", "and", "for", "with", "this", "that", "you", "are", "can", "was",
    "把", "帮", "我", "的", "了", "是", "在", "和", "就", "一个", "这个", "一下",
}


def _terms(text: str) -> set:
    """英文按词，中文按 2-gram。够用就行，重点是别把无关技能塞进来。"""
    low = (text or "").lower()
    words = {w for w in re.findall(r"[a-z0-9_\-]{3,}", low)}
    grams = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", low):
        if len(run) == 1:
            continue
        grams.update(run[i:i + 2] for i in range(len(run) - 1))
    return {t for t in (words | grams) if t not in STOP_TERMS}


def score_skill(skill, task_terms: set) -> int:
    """名字命中权重最高，描述次之，正文最低。"""
    if not task_terms:
        return 0
    name_terms = _terms(skill.name.replace("-", " ").replace("_", " "))
    desc_terms = _terms(skill.description)
    body_terms = _terms(skill.body)
    score = 3 * len(name_terms & task_terms)
    score += 2 * len(desc_terms & task_terms)
    score += len(body_terms & task_terms)
    return score


def select_skills(skills, task: str, max_skills: int = 3):
    """按任务挑技能。返回 (选中的技能, 是否命中)。

    一个都没命中时返回全部启用技能（fail-open），避免因为措辞差异把规则丢掉。
    """
    enabled = [s for s in skills if s.enabled]
    if len(enabled) <= 1:
        return enabled, True

    terms = _terms(task)
    ranked = sorted(((score_skill(s, terms), s) for s in enabled),
                    key=lambda pair: -pair[0])
    hits = [s for score, s in ranked if score > 0][:max_skills]
    if not hits:
        return enabled, False
    return hits, True


def ensure_example(root):
    """给没有技能的项目放一个示例，降低上手成本。返回新建路径或 None。"""
    target_dir = project_skills_dir(root)
    if target_dir.exists() and any(target_dir.glob("*.md")):
        return None
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    path = target_dir / "commit.md"
    if path.exists():
        return None
    path.write_text(
        "---\n"
        "name: commit\n"
        "description: 生成 Conventional Commits 格式的提交信息\n"
        "---\n"
        "写提交信息时：一行标题不超过 72 字符，格式 `type(scope): summary`；"
        "type 只能是 feat/fix/docs/refactor/test/chore；正文说明为什么改，不要复述改了什么。\n",
        encoding="utf-8",
    )
    return path
