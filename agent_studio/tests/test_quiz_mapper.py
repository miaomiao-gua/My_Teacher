#!/usr/bin/env python
"""出题结果 → 答题卡组件的映射测试。

为什么单独测这个：模型给出的「答案」格式五花八门 —— "B" / "B." / "（B）" / 2 / "对" /
完整选项文本 / 选项里自带 correct 标记……**漏掉任何一种，那道题就判不了分**，
而现象是「学生点了没反应」，很难从界面上看出来（真出过：3 道题里有 1 道没标对答案）。

跑法：py -3.14 tests/test_quiz_mapper.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.tools.course import _to_quiz_node          # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


CASES = [
    ("字母答案 B", {"question": "q", "options": ["甲", "乙", "丙"], "answer": "B"}, "乙"),
    ("带点字母 B.", {"question": "q", "options": ["甲", "乙", "丙"], "answer": "B."}, "乙"),
    ("括号字母（B）", {"question": "q", "options": ["甲", "乙", "丙"], "answer": "（B）"}, "乙"),
    ("序号 2", {"question": "q", "options": ["甲", "乙", "丙"], "answer": 2}, "乙"),
    ("序号字符串 '2'", {"question": "q", "options": ["甲", "乙", "丙"], "answer": "2"}, "乙"),
    ("答案就是选项原文", {"question": "q", "options": ["甲", "乙", "丙"], "answer": "乙"}, "乙"),
    ("选项带字母前缀 + 字母答案", {"question": "q", "options": ["A. 甲", "B. 乙", "C. 丙"], "answer": "B"}, "B. 乙"),
    ("选项带字母前缀 + 原文答案", {"question": "q", "options": ["A. 甲", "B. 乙"], "answer": "乙"}, "B. 乙"),
    ("答案带前缀完整文本", {"question": "q", "options": ["A. 甲", "B. 乙"], "answer": "B. 乙"}, "B. 乙"),
    ("选项自带 correct", {"question": "q", "options": [{"label": "甲", "correct": False}, {"label": "乙", "correct": True}]}, "乙"),
    ("判断题：对/错 + 答案「正确」", {"question": "q", "options": ["正确", "错误"], "answer": "正确"}, "正确"),
    ("判断题：T/F + 答案 true", {"question": "q", "options": ["正确", "错误"], "answer": True}, "正确"),
    ("中文答案 甲（含空格）", {"question": "q", "options": [" 甲 ", " 乙 "], "answer": " 甲 "}, "甲"),
]

print("===== 答案格式映射 =====")
for name, raw, expected in CASES:
    node = _to_quiz_node(dict(raw), 1, "知识点", "sess")
    marked = [o["label"] for o in node["options"] if o["correct"]]
    ok = len(marked) == 1 and expected.strip() in marked[0]
    check(name, ok, f"标出={marked} 期望≈{expected}")

print("\n===== 兜底行为 =====")
no_answer = _to_quiz_node({"question": "q", "options": ["甲", "乙"]}, 1, "k", "s")
check("没有答案信息时不乱标（留给上层丢弃）", not any(o["correct"] for o in no_answer["options"]))
empty = _to_quiz_node({"question": "q", "options": []}, 1, "k", "s")
check("没有选项时不崩", empty["options"] == [])

print("\n" + ("全部通过 ✓" if _failures == 0 else f"{_failures} 项失败 ✗"))
sys.exit(0 if _failures == 0 else 1)
