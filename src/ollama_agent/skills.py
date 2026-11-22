"""Skills: markdown rules loaded per session.

Two scopes:
  project  <workspace>/.agent/skills/<name>.md
  global   <config dir>/skills/<name>.md

Nothing is persisted: every agent start begins with zero skills loaded.
The user picks some in /skills (manual, pre-approved for this session); the
agent also auto-picks relevant ones per task and asks before using those.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import config_path

RENDER_LIMIT = 1200  # 注入系统提示的总字数上限


@dataclass
class Skill:
    name: str
    description: str
    body: str
    scope: str
    path: Path

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


def load_skills(root) -> list:
    """全局先读，项目同名覆盖。"""
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
            found[name] = Skill(name, desc, body, scope, path)
    return sorted(found.values(), key=lambda s: (s.scope != "project", s.name))


def render_skills(skills, limit: int = RENDER_LIMIT):
    """拼成系统提示的一段。超上限的跳过，返回 (文本, 被跳过的名字)。"""
    if not skills:
        return "", []

    blocks, used, skipped = [], 0, []
    for skill in skills:
        text = skill.render()
        if used + len(text) > limit:
            skipped.append(skill.name)
            continue
        blocks.append(text)
        used += len(text)

    if not blocks:
        return "", skipped
    return "## 本次可用的技能\n" + "\n\n".join(blocks) + "\n", skipped


# ---------------------------------------------------------------- 相关性判断

STOP_TERMS = {
    "the", "and", "for", "with", "this", "that", "you", "are", "can", "was",
    "把", "帮", "我", "的", "了", "是", "在", "和", "就", "一个", "这个", "一下",
}


def _terms(text: str) -> set:
    """英文按词，中文按 2-gram。"""
    low = (text or "").lower()
    words = set(re.findall(r"[a-z0-9_\-]{3,}", low))
    grams = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", low):
        if len(run) > 1:
            grams.update(run[i:i + 2] for i in range(len(run) - 1))
    return {t for t in (words | grams) if t not in STOP_TERMS}


def score_skill(skill: Skill, task_terms: set) -> int:
    if not task_terms:
        return 0
    name_terms = _terms(skill.name.replace("-", " ").replace("_", " "))
    desc_terms = _terms(skill.description)
    body_terms = _terms(skill.body)
    return (3 * len(name_terms & task_terms)
            + 2 * len(desc_terms & task_terms)
            + len(body_terms & task_terms))


def match_skills(skills, task: str, max_skills: int = 3):
    """只返回和任务相关的技能，一个都没命中就返回空列表。"""
    if not skills:
        return []
    if len(skills) == 1:
        return list(skills)

    terms = _terms(task)
    ranked = sorted(((score_skill(s, terms), s) for s in skills), key=lambda p: -p[0])
    return [s for score, s in ranked if score > 0][:max_skills]


def ensure_example(root):
    """给没有技能的项目放一个示例。返回新建路径或 None。"""
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
        "写提交信息时：标题不超过 72 字符，格式 `type(scope): summary`；"
        "type 只能是 feat/fix/docs/refactor/test/chore；正文说为什么改，不要复述改了什么。\n",
        encoding="utf-8",
    )
    return path
