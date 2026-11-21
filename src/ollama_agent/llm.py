"""Ollama client: native /api/chat, streaming, tool calling, no third-party deps."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:4b"


class OllamaError(Exception):
    pass


class OllamaClient:
    def __init__(self, host: str = DEFAULT_HOST, model: str = DEFAULT_MODEL,
                 num_ctx: int = 4096, think: bool = False,
                 temperature: float = 0.6, timeout: int = 1800):
        self.host = host.rstrip("/")
        self.model = model
        self.num_ctx = num_ctx
        self.think = think
        self.temperature = temperature
        self.timeout = timeout
        self.prompt_tokens = 0
        self.eval_tokens = 0

    # ------------------------------------------------------------------ api

    def tags(self):
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [m.get("name") for m in data.get("models", [])]
        except Exception:
            return []

    def capabilities(self, model: str = None):
        """返回模型能力列表，例如 ['completion', 'tools', 'thinking']。查不到返回 []。"""
        name = model or self.model
        try:
            req = urllib.request.Request(
                f"{self.host}/api/show",
                data=json.dumps({"model": name}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return list(data.get("capabilities") or [])
        except Exception:
            return []

    def supports(self, feature: str, model: str = None) -> bool:
        caps = self.capabilities(model)
        return feature in caps if caps else True  # 查不到就别拦着

    def _payload(self, messages, tools):
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "think": bool(self.think),
            "options": {
                "num_ctx": int(self.num_ctx),
                "temperature": self.temperature,
            },
        }
        if tools:
            payload["tools"] = tools
        return payload

    def chat(self, messages, tools=None, on_text=None, on_think=None):
        """返回 (text, tool_calls)。on_text/on_think 是流式回调，收到片段就调一次。"""
        req = urllib.request.Request(
            f"{self.host}/api/chat",
            data=json.dumps(self._payload(messages, tools), ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        text_parts, calls = [], []
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.URLError as exc:
            raise OllamaError(
                f"连不上 Ollama（{self.host}）：{exc.reason}。确认 `ollama serve` 在跑。"
            ) from exc
        except socket.timeout as exc:
            raise OllamaError(f"请求超时：{self.host}") from exc

        try:
            for raw in resp:
                if not raw.strip():
                    continue
                try:
                    chunk = json.loads(raw.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    continue

                if chunk.get("error"):
                    raise OllamaError(str(chunk["error"]))

                msg = chunk.get("message") or {}
                think = msg.get("thinking")
                if think:
                    if on_think:
                        on_think(think)
                content = msg.get("content")
                if content:
                    text_parts.append(content)
                    if on_text:
                        on_text(content)
                for call in msg.get("tool_calls") or []:
                    calls.append(call)

                if chunk.get("done"):
                    self.prompt_tokens += int(chunk.get("prompt_eval_count") or 0)
                    self.eval_tokens += int(chunk.get("eval_count") or 0)
        finally:
            resp.close()

        return "".join(text_parts), calls
