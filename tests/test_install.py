import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from ollama_agent import install as I

SAMPLE = """---
name: systematic-debugging
description: Use when encountering any bug
---

# Systematic Debugging

## Overview

**Core principle:** ALWAYS find root cause before attempting fixes.

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

## When to Use

- Test failures
- Bugs in production
- Performance problems

## The Process

You MUST complete each phase before the next. Read the error before guessing.
"""


class ParseTests(unittest.TestCase):
    def test_parse_repo_variants(self):
        for url in ("https://github.com/obra/superpowers",
                    "https://github.com/obra/superpowers.git",
                    "git@github.com:obra/superpowers.git"):
            self.assertEqual(I.parse_repo(url), ("obra", "superpowers"))

    def test_parse_repo_rejects_garbage(self):
        with self.assertRaises(I.InstallError):
            I.parse_repo("https://example.com/foo")


class CondenseTests(unittest.TestCase):
    def test_keeps_iron_law_and_core_principle(self):
        name, desc, body = I.split_frontmatter(SAMPLE)
        text = I.condense("", body, 400)
        self.assertIn("NO FIXES WITHOUT ROOT CAUSE", text)
        self.assertIn("find root cause", text)

    def test_drops_trigger_list(self):
        _, _, body = I.split_frontmatter(SAMPLE)
        text = I.condense("", body, 400)
        self.assertNotIn("Test failures", text)
        self.assertNotIn("Bugs in production", text)

    def test_respects_limit(self):
        _, _, body = I.split_frontmatter(SAMPLE)
        for limit in (60, 120, 400):
            self.assertLessEqual(len(I.condense("", body, limit)), limit)

    def test_clip_is_word_safe(self):
        self.assertEqual(I.clip("hello world foo", 12), "hello world…")


class ExtractTests(unittest.TestCase):
    def make_zip(self, files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, text in files.items():
                zf.writestr(name, text)
        return buf.getvalue()

    def test_finds_skill_md(self):
        blob = self.make_zip({
            "repo-main/skills/a/SKILL.md": SAMPLE,
            "repo-main/skills/b/SKILL.md": "---\nname: b\ndescription: d\n---\nbody b",
            "repo-main/README.md": "not a skill",
        })
        items = I.extract(blob)
        self.assertEqual(sorted(i["name"] for i in items), ["b", "systematic-debugging"])

    def test_name_falls_back_to_dir(self):
        blob = self.make_zip({"repo-main/skills/plain/SKILL.md": "no frontmatter here"})
        self.assertEqual(I.extract(blob)[0]["name"], "plain")


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.dest = self.tmp / "skills"
        self.blob = None
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("r-main/skills/x/SKILL.md", SAMPLE)
        self.blob = buf.getvalue()
        self._orig = I.download_zip
        I.download_zip = lambda owner, repo: self.blob

    def tearDown(self):
        I.download_zip = self._orig
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.tmp.rmdir()

    def test_install_writes_file(self):
        res = I.install("https://github.com/o/r", self.dest)
        self.assertEqual(len(res["added"]), 1)
        f = self.dest / "systematic-debugging.md"
        self.assertTrue(f.exists())
        text = f.read_text(encoding="utf-8")
        self.assertIn("name: systematic-debugging", text)
        self.assertIn("source: https://github.com/o/r", text)

    def test_skip_existing_without_force(self):
        I.install("https://github.com/o/r", self.dest)
        res = I.install("https://github.com/o/r", self.dest)
        self.assertEqual(res["added"], [])
        self.assertEqual(res["skipped"][0][1], "已存在")

    def test_force_overwrites(self):
        I.install("https://github.com/o/r", self.dest)
        res = I.install("https://github.com/o/r", self.dest, force=True)
        self.assertEqual(len(res["added"]), 1)

    def test_installed_file_is_loadable(self):
        from ollama_agent.skills import load_skills
        I.install("https://github.com/o/r", self.tmp / ".agent" / "skills")
        loaded = load_skills(self.tmp)
        self.assertEqual(loaded[0].name, "systematic-debugging")
        self.assertTrue(loaded[0].body)


if __name__ == "__main__":
    unittest.main()
