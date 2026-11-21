"""Agent loop: plan-free ReAct over native tool calling, with hard guards against loops."""

from __future__ import annotations

import json

NET_TOOLS = {"web_search", "fetch_url"}
SUMMARIZE = ("工具轮数用完了，不要再调用任何工具。基于已获得的信息直接给结论；"
             "信息不够就说明缺什么。")


class Agent:
    def __init__(self, client, toolbox, system_prompt: str, max_steps: int = 12,
                 max_messages: int = 26):
        self.client = client
        self.toolbox = toolbox
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.max_messages = max_messages

    def _trim(self, messages):
        if len(messages) <= self.max_messages:
            return messages
        # 保留 system + 用户首轮，其余留最近的部分
        return messages[:2] + messages[-(self.max_messages - 3):]

    def run(self, messages, on_text=None, on_think=None, on_tool=None, on_result=None):
        """messages 不含 system；返回 (最终文本, 完整 messages)。"""
        msgs = [{"role": "system", "content": self.system_prompt}] + list(messages)
        tools = list(self.toolbox.TOOL_SCHEMAS) if hasattr(self.toolbox, "TOOL_SCHEMAS") else None
        if tools is None:
            from .tools import TOOL_SCHEMAS
            tools = TOOL_SCHEMAS
        if not getattr(self.toolbox, "allow_shell", True):
            tools = [t for t in tools if t["function"]["name"] != "shell"]

        seen, streak, answer = set(), ["", 0], ""
        for step in range(1, self.max_steps + 1):
            text, calls = self.client.chat(msgs, tools, on_text=on_text, on_think=on_think)
            if not calls:
                answer = text
                msgs.append({"role": "assistant", "content": text})
                break

            msgs.append({"role": "assistant", "content": text, "tool_calls": calls})
            for call in calls:
                fn = (call.get("function") or {})
                name = fn.get("name") or ""
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args or "{}")
                    except json.JSONDecodeError:
                        args = {}
                if not isinstance(args, dict):
                    args = {}

                sig = name + "|" + json.dumps(args, ensure_ascii=False, sort_keys=True)
                if name in NET_TOOLS:
                    if streak[0] == name:
                        streak[1] += 1
                    else:
                        streak[0], streak[1] = name, 1
                else:
                    streak[0], streak[1] = "", 0

                if on_tool:
                    on_tool(name, args)

                if streak[1] > 3:
                    result = (f"{name} 已经连续调用 {streak[1]} 次，到此为止。"
                              f"不要再调用工具，直接用已有信息回答。")
                elif sig in seen:
                    result = "这个调用刚才已经执行过，结果一样。换思路或直接回答。"
                else:
                    seen.add(sig)
                    result = self.toolbox.dispatch(name, args)
                    if not isinstance(result, str):
                        result = json.dumps(result, ensure_ascii=False)

                if on_result:
                    on_result(name, result)
                msgs.append({"role": "tool", "content": result, "tool_name": name})

            msgs = self._trim(msgs)

        if not (answer or "").strip():
            msgs.append({"role": "user", "content": SUMMARIZE})
            text, _ = self.client.chat(msgs, None, on_text=on_text)
            answer = text
            msgs.append({"role": "assistant", "content": text})

        return (answer or "").strip(), msgs[1:]
