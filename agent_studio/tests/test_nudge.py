#!/usr/bin/env python
"""内核兜底逻辑的单测：催促不能污染对话历史 + 催不动就强制调用工具。

背景（用户实测）：
  学生说「继续」→ 模型回「我马上为您备课…稍等片刻」→ 内核注入一条催促 →
  **但那条催促被写进了会话历史**，于是下一轮模型开始复述「检测到你写占位文字…」，
  彻底不干活。用户看到的现象就是「卡住了、在原地打转」。

这个测试用假 LLM 直接打在 _loop 上，把两件事钉死：
  1. 催促只进「本次请求的消息列表」，**会话历史里绝不能出现**
  2. 催一次还不动手 → 下一次请求必须带 tool_choice="required"

跑法（agent_studio 目录下）：py -3.14 tests/test_nudge.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

from kernel.config import LLMConfig                          # noqa: E402
from kernel.llm import Chunk                                  # noqa: E402
from kernel.registry import ToolContext, get                  # noqa: E402
from kernel.runtime import AgentRuntime                       # noqa: E402
from kernel.session import STORE                              # noqa: E402
from kernel.tools import load_builtin_tools                   # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


class FakeLLM:
    """按剧本回话的假模型，并记录每次调用用的 tool_choice。"""

    def __init__(self, script):
        self.script = list(script)
        self.seen_tool_choice = []
        self.seen_messages = []

    async def stream(self, messages, tools=None, tool_choice=None):
        self.seen_tool_choice.append(tool_choice)
        self.seen_messages.append([dict(m) for m in messages])
        text = self.script.pop(0) if self.script else ""
        if text:
            yield Chunk("text", text=text)
        yield Chunk("done", finish_reason="stop")


async def main() -> int:
    load_builtin_tools()
    rt = AgentRuntime()
    cfg = LLMConfig(provider="openai", model="fake", api_key="x")

    print("===== 1) 催促只进请求、不进历史 =====")
    sess = STORE.get_or_create("nudge_test", username="nudge")
    sess.messages.clear()
    sess.add_user("给我讲一下9709")
    messages = sess.history("（system）")
    llm = FakeLLM([
        "劳逸结合我正在为您准备9709课程的内容，请稍等片刻。",   # 第 1 次：只说不做
        "好的。",                                              # 第 2 次：被催之后随便答一句
    ])
    emitted = []

    async def emit(ev):
        emitted.append(ev)

    steps, reason = await rt._loop(llm, cfg, sess, messages, emit)
    notices = [e for e in emitted if e.type == "notice"]
    check("催促确实触发了", len(notices) >= 1, f"{len(notices)} 条提示")
    nudge_in_history = any("你刚才只是在描述计划" in (m.get("content") or "")
                           for m in sess.messages if m.get("role") == "user")
    check("催促没有写进会话历史（关键回归）", not nudge_in_history)
    nudge_in_request = any("你刚才只是在描述计划" in (m.get("content") or "")
                           for m in llm.seen_messages[-1] if m.get("role") == "user")
    check("催促确实进了本次请求", nudge_in_request)
    assistants = [m for m in sess.messages if m.get("role") == "assistant"]
    check("历史里只有真实回复", all((m.get("content") or "").strip() for m in assistants),
          f"{len(assistants)} 条 assistant")

    print("\n===== 2) 催了还不动手 → 强制工具调用 =====")
    sess2 = STORE.get_or_create("nudge_test2", username="nudge")
    sess2.messages.clear()
    sess2.add_user("给我讲一下9709")
    messages2 = sess2.history("（system）")
    llm2 = FakeLLM([
        "我这就为你准备课程，请稍等片刻。",      # 只说不做 → 催
        "好的，马上为您准备，稍等片刻。",        # 还是只说不做 → 强制
        "仍然在准备中，请稍候。",                # 假模型就是不动手（真实场景也会发生）
    ])
    await rt._loop(llm2, cfg, sess2, messages2, emit)
    check("第三次请求带上了 tool_choice=required",
          "required" in llm2.seen_tool_choice,
          f"实际: {llm2.seen_tool_choice}")

    print("\n===== 3) 工具真的被调用后，强制要解除 =====")
    sess3 = STORE.get_or_create("nudge_test3", username="nudge")
    sess3.messages.clear()
    sess3.add_user("算一下 2+2")
    messages3 = sess3.history("（system）")

    class ToolLLM(FakeLLM):
        async def stream(self, messages, tools=None, tool_choice=None):
            self.seen_tool_choice.append(tool_choice)
            if len(self.seen_tool_choice) == 1:
                yield Chunk("text", text="我这就为你准备，请稍等片刻。")
            elif len(self.seen_tool_choice) == 2:
                yield Chunk("text", text="马上就好，稍等。")
            elif len(self.seen_tool_choice) == 3:
                from kernel.llm import ToolCallRequest

                yield Chunk("tool_calls", calls=[ToolCallRequest(id="c1", name="calc", args={"expression": "2+2"})])
            else:
                yield Chunk("text", text="2+2=4。")
            yield Chunk("done", finish_reason="stop")

    llm3 = ToolLLM([])
    await rt._loop(llm3, cfg, sess3, messages3, emit)
    check("第 3 次强制、第 4 次解除",
          llm3.seen_tool_choice[2] == "required" and llm3.seen_tool_choice[3] is None,
          f"实际: {llm3.seen_tool_choice}")

    for sid in ("nudge_test", "nudge_test2", "nudge_test3"):
        STORE.drop(sid)

    print("\n" + ("全部通过 ✓" if _failures == 0 else f"{_failures} 项失败 ✗"))
    return 0 if _failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
