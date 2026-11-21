import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ollama_agent.llm import OllamaClient


class _Handler(BaseHTTPRequestHandler):
    """假 Ollama：按路径返回固定 JSON，用来验证能力判定。"""

    caps_by_model = {
        "qwen3.5:4b": ["completion", "tools", "thinking", "vision"],
        "llama3.2:1b": ["completion", "tools"],
        "mistral:7b": ["completion"],
    }

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/show":
            model = body.get("model", "")
            caps = self.caps_by_model.get(model)
            if caps is None:
                self.send_response(404)
                self.end_headers()
                return
            payload = {"capabilities": caps}
        else:
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            data = json.dumps({"models": [{"name": n} for n in self.caps_by_model]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()


class CapabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def client(self, model):
        return OllamaClient(host=self.host, model=model)

    def test_reads_capabilities(self):
        c = self.client("qwen3.5:4b")
        self.assertEqual(c.capabilities(),
                         ["completion", "tools", "thinking", "vision"])

    def test_tool_capable_non_qwen(self):
        c = self.client("llama3.2:1b")
        self.assertTrue(c.supports("tools"))
        self.assertFalse(c.supports("thinking"))

    def test_model_without_tools(self):
        c = self.client("mistral:7b")
        self.assertFalse(c.supports("tools"))
        self.assertEqual(c.capabilities(), ["completion"])

    def test_unknown_model_does_not_block(self):
        """查不到能力时不能拦着用户，宁放行。"""
        c = self.client("some-unseen:7b")
        self.assertEqual(c.capabilities(), [])
        self.assertTrue(c.supports("tools"))

    def test_tags(self):
        names = self.client("x").tags()
        self.assertIn("llama3.2:1b", names)


if __name__ == "__main__":
    unittest.main()
