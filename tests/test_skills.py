import os
import tempfile
import unittest
from pathlib import Path

from ollama_agent import skills as S


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["APPDATA"] = str(self.tmp / "config")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "config")
        self.root = self.tmp / "proj"
        (self.root / ".agent" / "skills").mkdir(parents=True)

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.tmp.rmdir()

    def add(self, name, desc, body="", scope="project"):
        d = S.project_skills_dir(self.root) if scope == "project" else S.global_skills_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n{body or desc}", encoding="utf-8")

    def test_frontmatter_parsed(self):
        name, desc, body = S._parse("---\nname: commit\ndescription: 写提交信息\n---\n正文", "x")
        self.assertEqual((name, desc, body), ("commit", "写提交信息", "正文"))

    def test_no_frontmatter_uses_filename(self):
        self.add("plain", "", body="就是一段规则")
        skills = S.load_skills(self.root)
        self.assertEqual(skills[0].name, "plain")
        self.assertEqual(skills[0].body, "就是一段规则")

    def test_project_overrides_global(self):
        self.add("same", "", body="全局版本", scope="global")
        self.add("same", "", body="项目版本")
        skills = S.load_skills(self.root)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].body, "项目版本")

    def test_load_has_no_enabled_state(self):
        """技能加载不带 enabled：每次启动都是干净的。"""
        self.add("a", "规则A")
        skills = S.load_skills(self.root)
        self.assertFalse(hasattr(skills[0], "enabled"))

    def test_render_all(self):
        self.add("a", "规则A")
        self.add("b", "规则B")
        text, skipped = S.render_skills(S.load_skills(self.root))
        self.assertIn("规则A", text)
        self.assertIn("规则B", text)
        self.assertEqual(skipped, [])

    def test_render_limit_skips_overflow(self):
        self.add("big", "长规则一", body="x" * 600)
        self.add("more", "长规则二", body="y" * 600)
        text, skipped = S.render_skills(S.load_skills(self.root), limit=700)
        self.assertEqual(skipped, ["more"])
        self.assertNotIn("yyy", text)

    def test_ensure_example_creates_once(self):
        p1 = S.ensure_example(self.root)
        self.assertIsNotNone(p1)
        self.assertTrue(p1.exists())
        self.assertIsNone(S.ensure_example(self.root))


class MatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["APPDATA"] = str(self.tmp / "config")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "config")
        self.root = self.tmp / "proj"
        (self.root / ".agent" / "skills").mkdir(parents=True)

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.tmp.rmdir()

    def add(self, name, desc, body=""):
        (S.project_skills_dir(self.root) / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n{body or desc}", encoding="utf-8")

    def test_picks_matching(self):
        self.add("commit", "生成 Conventional Commits 提交信息")
        self.add("translate", "把文本翻译成英文")
        picked = S.match_skills(S.load_skills(self.root), "帮我写一条提交信息")
        self.assertEqual([s.name for s in picked], ["commit"])

    def test_no_match_returns_empty(self):
        self.add("commit", "生成提交信息")
        self.add("translate", "翻译成英文")
        self.assertEqual(S.match_skills(S.load_skills(self.root), "今天天气怎么样"), [])

    def test_single_skill_always_returned(self):
        self.add("commit", "生成提交信息")
        picked = S.match_skills(S.load_skills(self.root), "完全无关的话")
        self.assertEqual(len(picked), 1)

    def test_cap(self):
        for i in range(5):
            self.add(f"s{i}", "提交信息 规范")
        picked = S.match_skills(S.load_skills(self.root), "写提交信息", max_skills=2)
        self.assertEqual(len(picked), 2)


if __name__ == "__main__":
    unittest.main()
