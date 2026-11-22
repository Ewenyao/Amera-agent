import os
import tempfile
import unittest
from pathlib import Path

from ollama_agent import skills as S
from ollama_agent.config import save_skills, load_file


class SkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "config"
        os.environ["APPDATA"] = str(self.home)
        os.environ["XDG_CONFIG_HOME"] = str(self.home)
        self.root = self.tmp / "proj"
        (self.root / ".agent" / "skills").mkdir(parents=True)

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.tmp.rmdir()

    def write(self, scope, name, text):
        d = S.project_skills_dir(self.root) if scope == "project" else S.global_skills_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.md").write_text(text, encoding="utf-8")

    def test_frontmatter_parsed(self):
        name, desc, body = S._parse("---\nname: commit\ndescription: 写提交信息\n---\n正文", "x")
        self.assertEqual(name, "commit")
        self.assertEqual(desc, "写提交信息")
        self.assertEqual(body, "正文")

    def test_no_frontmatter_uses_filename(self):
        self.write("project", "plain", "就是一段规则")
        skills = S.load_skills(self.root)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].name, "plain")
        self.assertEqual(skills[0].body, "就是一段规则")

    def test_project_overrides_global(self):
        self.write("global", "same", "全局版本")
        self.write("project", "same", "项目版本")
        skills = S.load_skills(self.root)
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0].body, "项目版本")
        self.assertEqual(skills[0].scope, "project")

    def test_enabled_flag(self):
        self.write("project", "a", "规则A")
        self.write("project", "b", "规则B")
        skills = S.load_skills(self.root, ["b"])
        flags = {s.name: s.enabled for s in skills}
        self.assertEqual(flags, {"a": False, "b": True})

    def test_render_only_enabled(self):
        self.write("project", "a", "规则A")
        self.write("project", "b", "规则B")
        skills = S.load_skills(self.root, ["a"])
        text, skipped = S.render_enabled(skills)
        self.assertIn("规则A", text)
        self.assertNotIn("规则B", text)
        self.assertEqual(skipped, [])

    def test_render_limit_skips_overflow(self):
        self.write("project", "big", "x" * 600)
        self.write("project", "more", "y" * 600)
        skills = S.load_skills(self.root, ["big", "more"])
        text, skipped = S.render_enabled(skills, limit=700)
        self.assertEqual(skipped, ["more"])
        self.assertNotIn("yyy", text)

    def test_ensure_example_creates_once(self):
        p1 = S.ensure_example(self.root)
        self.assertIsNotNone(p1)
        self.assertTrue(p1.exists())
        self.assertIsNone(S.ensure_example(self.root))

    def test_save_skills_roundtrip(self):
        save_skills(["a", "b"])
        self.assertEqual(load_file().get("skills"), ["a", "b"])
        save_skills(["b"])
        self.assertEqual(load_file().get("skills"), ["b"])


if __name__ == "__main__":
    unittest.main()


class SelectTests(unittest.TestCase):
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
        p = S.project_skills_dir(self.root) / f"{name}.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body or desc}", encoding="utf-8")

    def test_picks_matching_skill(self):
        self.add("commit", "生成 Conventional Commits 提交信息")
        self.add("translate", "把文本翻译成英文")
        skills = S.load_skills(self.root, ["commit", "translate"])
        picked, matched = S.select_skills(skills, "帮你写一条提交信息")
        self.assertTrue(matched)
        self.assertEqual([s.name for s in picked], ["commit"])

    def test_falls_back_to_all_when_nothing_matches(self):
        self.add("commit", "生成提交信息")
        self.add("translate", "翻译成英文")
        skills = S.load_skills(self.root, ["commit", "translate"])
        picked, matched = S.select_skills(skills, "今天天气怎么样")
        self.assertFalse(matched)
        self.assertEqual(len(picked), 2)

    def test_single_skill_skips_scoring(self):
        self.add("commit", "生成提交信息")
        skills = S.load_skills(self.root, ["commit"])
        picked, matched = S.select_skills(skills, "完全无关的话")
        self.assertTrue(matched)
        self.assertEqual(len(picked), 1)

    def test_caps_number_of_skills(self):
        for i in range(5):
            self.add(f"s{i}", "提交信息 规范")
        skills = S.load_skills(self.root, [f"s{i}" for i in range(5)])
        picked, _ = S.select_skills(skills, "写提交信息", max_skills=2)
        self.assertEqual(len(picked), 2)

    def test_disabled_skills_excluded(self):
        self.add("commit", "生成提交信息")
        self.add("translate", "翻译")
        skills = S.load_skills(self.root, ["commit"])
        picked, _ = S.select_skills(skills, "翻译这段话，也写个提交信息")
        self.assertEqual([s.name for s in picked], ["commit"])
