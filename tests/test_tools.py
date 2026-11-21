import os
import tempfile
import unittest
from pathlib import Path

from ollama_agent.tools import ToolBox, ToolError


class WriteScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp) / "proj"
        self.root.mkdir()
        self.extra = Path(self.tmp) / "shared"
        self.extra.mkdir()

    def tearDown(self):
        for p in sorted(Path(self.tmp).rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        Path(self.tmp).rmdir()

    def test_write_inside_root(self):
        box = ToolBox(self.root)
        out = box.write_file("a.txt", "hi")
        self.assertIn("已写入", out)
        self.assertTrue((self.root / "a.txt").exists())

    def test_write_outside_root_rejected(self):
        box = ToolBox(self.root)
        target = str(Path(self.tmp) / "nope.txt")
        with self.assertRaises(ToolError):
            box._resolve(target, write=True)
        res = box.dispatch("write_file", {"path": target, "content": "x"})
        self.assertIn("没有写权限", res)

    def test_extra_write_root_allowed(self):
        box = ToolBox(self.root, write_roots=[self.extra])
        res = box.dispatch("write_file", {"path": str(self.extra / "b.txt"), "content": "x"})
        self.assertIn("已写入", res)
        self.assertTrue((self.extra / "b.txt").exists())

    def test_read_all_vs_jail(self):
        outside = Path(self.tmp) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")

        free = ToolBox(self.root, read_all=True)
        self.assertIn("secret", free.dispatch("read_file", {"path": str(outside)}))

        jailed = ToolBox(self.root, read_all=False)
        res = jailed.dispatch("read_file", {"path": str(outside)})
        self.assertIn("路径越界", res)


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.box = ToolBox(Path.cwd())

    def test_destructive_blocked(self):
        self.assertIn("黑名单", self.box.dispatch("shell", {"command": "rm -rf /tmp/x"}))
        self.assertIn("黑名单", self.box.dispatch("shell", {"command": "format C:"}))

    def test_normal_command_runs(self):
        out = self.box.dispatch("shell", {"command": "echo hello"})
        self.assertIn("hello", out)

    def test_shell_disabled(self):
        box = ToolBox(Path.cwd(), allow_shell=False)
        self.assertIn("已禁用", box.dispatch("shell", {"command": "echo hi"}))


class ReplaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.box = ToolBox(Path(self.tmp))
        Path(self.tmp, "f.py").write_text("a = 1\nb = 2\n", encoding="utf-8")

    def tearDown(self):
        for p in Path(self.tmp).rglob("*"):
            p.unlink() if p.is_file() else None
        Path(self.tmp).rmdir()

    def test_unique_replace(self):
        res = self.box.dispatch("replace_in_file", {"path": "f.py", "old": "a = 1", "new": "a = 10"})
        self.assertIn("已替换", res)
        self.assertIn("a = 10", Path(self.tmp, "f.py").read_text(encoding="utf-8"))

    def test_ambiguous_replace(self):
        Path(self.tmp, "g.py").write_text("x\nx\n", encoding="utf-8")
        res = self.box.dispatch("replace_in_file", {"path": "g.py", "old": "x", "new": "y"})
        self.assertIn("不唯一", res)

    def test_missing_old(self):
        res = self.box.dispatch("replace_in_file", {"path": "f.py", "old": "zzz", "new": "y"})
        self.assertIn("没找到", res)


if __name__ == "__main__":
    unittest.main()
