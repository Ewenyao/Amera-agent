import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ollama_agent.tools import ToolBox, _strip_progress


class TimeoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.box = ToolBox(self.tmp, shell_timeout=2)

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.tmp.rmdir()

    def test_timeout_returns_fast(self):
        """超时必须真的在几秒内返回，不能等子进程自己退出。"""
        t0 = time.time()
        out = self.box.dispatch("shell", {"command": f'"{sys.executable}" -c "import time; time.sleep(30)"'})
        spent = time.time() - t0
        self.assertLess(spent, 12, f"超时没生效，用了 {spent:.1f}s")
        self.assertIn("超时", out)
        self.assertIn("进程树已终止", out)

    def test_timeout_hint_for_git(self):
        out = self.box.dispatch(
            "shell",
            {"command": f'"{sys.executable}" -c "import time; time.sleep(30)" && git clone x y'})
        self.assertIn("超时", out)
        self.assertIn("--depth 1", out)

    def test_no_blocking_on_stdin(self):
        """stdin 必须接 /dev/null，不然子进程会卡在等输入。"""
        t0 = time.time()
        out = self.box.dispatch("shell", {"command": f'"{sys.executable}" -c "input()"'})
        self.assertLess(time.time() - t0, 8)
        self.assertIn("exit", out)


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.box = ToolBox(self.tmp)

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.tmp.rmdir()

    def test_collapse_cr(self):
        self.assertEqual(_strip_progress("a\rb\rc\n"), "c\n")
        self.assertEqual(_strip_progress("done\n"), "done\n")

    def test_shell_output_collapsed(self):
        script = self.tmp / "prog.py"
        script.write_text('import sys; sys.stdout.write("10%\\r20%\\r100%\\n")', encoding="utf-8")
        out = self.box.dispatch("shell", {"command": f'"{sys.executable}" "{script}"'})
        self.assertIn("100%", out)
        self.assertNotIn("20%", out)

    def test_git_style_progress_kept_final(self):
        script = self.tmp / "err.py"
        script.write_text(
            'import sys; sys.stderr.write("Updating files: 52%\\rUpdating files: 100%\\r\\n")',
            encoding="utf-8")
        out = self.box.dispatch("shell", {"command": f'"{sys.executable}" "{script}"'})
        self.assertIn("100%", out)
        self.assertNotIn("52%", out)


if __name__ == "__main__":
    unittest.main()
