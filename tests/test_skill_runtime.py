import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from ollama_agent import install as I
from ollama_agent import tools as T
from ollama_agent.config import find_bash

SKILL_MD = """---
name: demo
description: demo skill
---
# Demo

NO GUESSING WITHOUT READING THIS LINE.
"""


class SkillRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "proj"
        self.root.mkdir()
        self.raw = self.root / ".agent" / "skills-raw"
        skill = self.raw / "demo"
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
        (skill / "scripts" / "run.sh").write_text("echo hi\n", encoding="utf-8")
        (self.root / ".agent" / "skills").mkdir(parents=True)
        (self.root / ".agent" / "skills" / "demo.md").write_text(
            "---\nname: demo\ndescription: demo skill\n---\n压缩版", encoding="utf-8")

        self.box = T.ToolBox(self.root, skill_roots=[self.raw], bash_path=find_bash())

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.tmp.rmdir()

    def test_use_skill_returns_full_text(self):
        out = self.box.use_skill("demo")
        self.assertIn("NO GUESSING", out)
        self.assertIn("scripts/run.sh", out)

    def test_use_skill_unknown(self):
        self.assertIn("没有原始文件", self.box.use_skill("nope"))

    def test_read_skill_uri(self):
        out = self.box.dispatch("read_file", {"path": "skill://demo/scripts/run.sh"})
        self.assertIn("echo hi", out)

    def test_write_to_skill_uri_rejected(self):
        out = self.box.dispatch("write_file", {"path": "skill://demo/x.txt", "content": "a"})
        self.assertIn("只读", out)

    def test_escape_skill_dir_rejected(self):
        out = self.box.dispatch("read_file", {"path": "skill://demo/../../../etc/passwd"})
        self.assertIn("越出技能目录", out)

    def test_allowed_skills_guard(self):
        self.box.allowed_skills = {"other"}
        self.assertIn("未获准使用", self.box.use_skill("demo"))
        self.box.allowed_skills = {"demo"}
        self.assertIn("NO GUESSING", self.box.use_skill("demo"))

    def test_list_skills(self):
        self.assertIn("demo", self.box.list_skills())

    @unittest.skipUnless(find_bash(), "no bash on this machine")
    def test_bash_with_skill_cwd(self):
        out = self.box.dispatch("shell", {"command": "bash -c 'pwd; ls'", "cwd": "skill://demo"})
        self.assertIn("[bash]", out)
        self.assertIn("scripts", out)

    def test_condensed_fallback_without_raw(self):
        box = T.ToolBox(self.root, skill_roots=[self.tmp / "nothing"])
        self.assertIn("没有原始文件", box.use_skill("demo"))


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "proj"
        self.root.mkdir()
        raw = self.root / ".agent" / "skills-raw" / "demo"
        raw.mkdir(parents=True)
        (raw / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.tmp.rmdir()

    def cfg(self):
        return {"root": str(self.root), "read_all": True, "allow_shell": True,
                "write_roots": [str(self.root)]}

    def test_prompt_lists_names_not_bodies(self):
        from ollama_agent.cli import build_system_prompt
        box = T.ToolBox(self.root, skill_roots=[self.root / ".agent" / "skills-raw"])
        prompt = build_system_prompt(self.cfg(), box, ["demo"])
        self.assertIn("demo", prompt)
        self.assertIn("use_skill", prompt)
        self.assertNotIn("NO GUESSING", prompt)   # 正文不进提示

    def test_prompt_declares_no_subagent(self):
        from ollama_agent.cli import build_system_prompt
        box = T.ToolBox(self.root)
        self.assertIn("subagent", build_system_prompt(self.cfg(), box))


class InstallRawTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dest = self.tmp / ".agent" / "skills"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("r-main/skills/demo/SKILL.md", SKILL_MD)
            zf.writestr("r-main/skills/demo/scripts/run.sh", "echo hi\n")
        self.blob = buf.getvalue()
        self._orig = I.download_zip
        I.download_zip = lambda owner, repo: self.blob

    def tearDown(self):
        I.download_zip = self._orig
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.tmp.rmdir()

    def test_raw_tree_copied(self):
        I.install("https://github.com/o/r", self.dest)
        raw = self.tmp / ".agent" / "skills-raw" / "demo"
        self.assertTrue((raw / "SKILL.md").exists())
        self.assertTrue((raw / "scripts" / "run.sh").exists())

    def test_raw_disabled_still_installs_condensed(self):
        I.install("https://github.com/o/r", self.dest)
        self.assertTrue((self.dest / "demo.md").exists())


class ConfigTests(unittest.TestCase):
    def test_default_num_ctx(self):
        from ollama_agent.config import resolve

        class Args:
            model = host = num_ctx = max_steps = write_root = root = None
            think = jail = no_shell = shell_timeout = False

        os.environ.pop("OLLAMA_NUM_CTX", None)
        self.assertEqual(resolve(Args())["num_ctx"], 8192)


if __name__ == "__main__":
    unittest.main()
