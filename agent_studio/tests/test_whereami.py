#!/usr/bin/env python
"""「Agent 知不知道自己在哪里」的自测。

三件事必须成立（都是学生真的会问的）：
  1. 系统提示词里带上当前 学科 / 教材 / 对话名字（不然它只能瞎猜）
  2. list_conversations 能列出全部对话，并标出当前所在的那一条
  3. 换个文件夹/改个名字之后，提示词里的「当前位置」跟着变

跑法（agent_studio 目录下）：py -3.14 tests/test_whereami.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

from kernel.prompts import build_system_prompt                    # noqa: E402
from kernel.registry import ToolContext, describe_tools, get     # noqa: E402
from kernel.session import STORE                                  # noqa: E402
from kernel.tools import load_builtin_tools                       # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


async def main() -> int:
    load_builtin_tools()

    print("===== 1) 提示词里要有「你现在在哪里」 =====")
    sess = STORE.get_or_create("whereami_test", username="wa_test")
    sess.subject, sess.textbook, sess.title = "数学P1", "9709", "三角函数图像变换"
    sess.add_user("我们在哪个文件夹？")
    sess.save()
    prompt = build_system_prompt(sess, describe_tools())
    check("提示词含学科", "数学P1" in prompt)
    check("提示词含教材", "9709" in prompt)
    check("提示词含对话名字", "三角函数图像变换" in prompt)
    check("提示词说明了层级位置", "未分类 / 未指定教材" not in prompt and "/" in prompt)
    line = next((l for l in prompt.splitlines() if "历史栏里的位置" in l), "")
    print("     实际那一行:", line.strip()[:110])

    print("\n===== 2) 没归类时也要如实说 =====")
    plain = STORE.get_or_create("whereami_plain", username="wa_test")
    plain.subject, plain.textbook, plain.title = "", "", ""
    p2 = build_system_prompt(plain, describe_tools())
    check("提示词说明未归类", "未分类" in p2 or "还没有归到任何学科" in p2)

    print("\n===== 3) list_conversations 能列出并标出当前 =====")
    ctx = ToolContext(session=sess, call_id="t", username="wa_test")
    res = await get("list_conversations").call(ctx, {})
    check("工具调通", bool(res.ok))
    check("摘要里说了当前位置", "数学P1" in (res.summary or "") and "9709" in (res.summary or ""))
    rows = ((res.data or {}).get("conversations")) or []
    check("列出了对话", len(rows) > 0, f"{len(rows)} 条")
    check("当前那条被标记 ▶", any("▶" in str(r[2]) for r in rows))
    print("     当前:", (res.data or {}).get("current"))

    print("\n===== 4) 按关键词筛选 =====")
    res2 = await get("list_conversations").call(ctx, {"keyword": "三角"})
    check("关键词筛选有效", bool(res2.ok) and len((res2.data or {}).get("conversations") or []) >= 1)

    print("\n===== 5) 改名/移动后，提示词要跟着变 =====")
    sess.subject, sess.textbook, sess.title = "物理", "人教版必修一", "牛顿第二定律"
    sess.save()
    p3 = build_system_prompt(sess, describe_tools())
    check("新学科进了提示词", "物理" in p3)
    check("旧学科不在提示词里", "数学P1" not in p3 or "数学P1" in sess.messages.__str__() is False)

    # 清理
    for sid in ("whereami_test", "whereami_plain"):
        STORE.drop(sid)

    print("\n" + ("全部通过 ✓" if _failures == 0 else f"{_failures} 项失败 ✗"))
    return 0 if _failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
