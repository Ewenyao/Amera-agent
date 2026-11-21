import tempfile
import unittest
from pathlib import Path

from ollama_agent.loop import Agent
from ollama_agent.tools import ToolBox


class FakeClient:
    """按脚本依次返回 (text, tool_calls)，用于测循环兜底。"""

    def __init__(self, script):
        self.script = list(script)
        self.i = 0
        self.prompt_tokens = 0
        self.eval_tokens = 0
        self.tool_offered = None

    def chat(self, messages, tools=None, on_text=None, on_think=None):
        self.tool_offered = tools
        item = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        text, calls = item
        if text and on_text:
            on_text(text)
        return text, calls


def call(name, **args):
    return {"function": {"name": name, "arguments": args}}


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.box = ToolBox(self.tmp)

    def tearDown(self):
        for p in sorted(self.tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
        self.tmp.rmdir()

    def agent(self, script, **kw):
        client = FakeClient(script)
        return client, Agent(client, self.box, "sys", **kw)

    def test_plain_answer(self):
        client, ag = self.agent([("你好", [])])
        answer, messages = ag.run([{"role": "user", "content": "hi"}])
        self.assertEqual(answer, "你好")
        self.assertEqual(client.i, 1)

    def test_tool_then_answer(self):
        _, ag = self.agent([
            ("", [call("list_dir", path=".")]),
            ("目录已看过", []),
        ])
        answer, messages = ag.run([{"role": "user", "content": "看看目录"}])
        self.assertEqual(answer, "目录已看过")
        self.assertTrue(any(m.get("role") == "tool" for m in messages))

    def test_duplicate_call_blocked(self):
        _, ag = self.agent([
            ("", [call("list_dir", path=".")]),
            ("", [call("list_dir", path=".")]),
            ("好了", []),
        ])
        _, messages = ag.run([{"role": "user", "content": "x"}])
        tool_msgs = [m["content"] for m in messages if m.get("role") == "tool"]
        self.assertEqual(len(tool_msgs), 2)
        self.assertIn("已经执行过", tool_msgs[1])

    def test_network_streak_capped(self):
        script = [("", [call("web_search", query=f"q{i}")]) for i in range(4)] + [("done", [])]
        _, ag = self.agent(script)
        _, messages = ag.run([{"role": "user", "content": "x"}], )
        tool_msgs = [m["content"] for m in messages if m.get("role") == "tool"]
        self.assertTrue(any("连续调用" in m for m in tool_msgs))

    def test_forced_summary_when_steps_exhausted(self):
        script = [("", [call("list_dir", path=f"./s{i}")]) for i in range(2)] + [("兜底结论", [])]
        _, ag = self.agent(script, max_steps=2)
        answer, _ = ag.run([{"role": "user", "content": "x"}])
        self.assertEqual(answer, "兜底结论")

    def test_shell_excluded_when_disabled(self):
        self.box.allow_shell = False
        client, ag = self.agent([("ok", [])])
        ag.run([{"role": "user", "content": "hi"}])
        names = [t["function"]["name"] for t in (client.tool_offered or [])]
        self.assertNotIn("shell", names)
        self.assertIn("read_file", names)


if __name__ == "__main__":
    unittest.main()
