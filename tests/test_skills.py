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
