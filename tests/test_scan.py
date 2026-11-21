import tempfile
import unittest
from pathlib import Path

from ollama_agent.project_scan import scan_project, save_context, load_context, AGENTS_FILE


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "README.md").write_text("# demo\n一个演示项目\n", encoding="utf-8")
        (self.tmp / "main.py").write_text("print(1)\n", encoding="utf-8")
        (self.tmp / "node_modules").mkdir()
        (self.tmp / "node_modules" / "junk.js").write_text("x" * 100, encoding="utf-8")
        (self.tmp / "sub").mkdir()
        (self.tmp / "sub" / "package.json").write_text('{"scripts":{"dev":"vite"}}', encoding="utf-8")

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.tmp.rmdir()

    def test_ignores_dependency_dirs(self):
        text = scan_project(str(self.tmp))
        self.assertNotIn("junk.js", text)

    def test_detects_marker_in_subdir(self):
        text = scan_project(str(self.tmp))
        self.assertIn("package.json", text)
        self.assertIn("sub", text)

    def test_reads_readme_head(self):
        self.assertIn("演示项目", scan_project(str(self.tmp)))

    def test_context_roundtrip(self):
        self.assertEqual(load_context(self.tmp), "")
        save_context(self.tmp, "# p\n说明")
        self.assertTrue((self.tmp / AGENTS_FILE).exists())
        self.assertIn("说明", load_context(self.tmp))


if __name__ == "__main__":
    unittest.main()
