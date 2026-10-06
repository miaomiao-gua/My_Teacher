#!/usr/bin/env python
"""检查 agent_ui.js 里改动的两处是否在同一个作用域（防止 ReferenceError）。

背景：`notice` 分支里加了一句 `refreshTree()`；如果 refreshTree 定义在**另一个函数**里，
运行到「已自动归档」提示时就会抛 ReferenceError，而这个分支平时不走到，很难发现。
"""
from __future__ import annotations

import re
from pathlib import Path

JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "agent_ui.js"
lines = JS.read_text(encoding="utf-8").split("\n")


def enclosing(lineno: int):
    """从缩进和花括号深度还原「这行属于哪个 function」。"""
    stack = []
    depth = 0
    for i, line in enumerate(lines[:lineno], 1):
        depth += line.count("{") - line.count("}")
        m = re.match(r"\s*function\s+(\w+)", line)
        if m:
            stack.append((depth, m.group(1), i))
        while stack and depth < stack[-1][0]:
            stack.pop()
    return stack[-1] if stack else None


targets = {
    "notice 分支": "case 'notice'",
    "refreshTree 定义": "function refreshTree",
    "refreshTree 调用点": "if (String(d.message || '').indexOf('归档') >= 0) refreshTree();",
    "桌宠按钮": "window.open('/agent/pet'",
    "版本号": "const AgentUI = { version:",
}
info = {}
for name, pat in targets.items():
    hit = [i for i, l in enumerate(lines, 1) if pat in l]
    if not hit:
        print(f"✗ 找不到：{name}（{pat}）")
        continue
    ln = hit[0]
    info[name] = (ln, enclosing(ln))
    print(f"✓ {name}: 第 {ln} 行，所属函数 = {enclosing(ln)}")

a = info.get("notice 分支")
b = info.get("refreshTree 定义")
if a and b:
    same = (a[1] and b[1] and a[1][1] == b[1][1]) or (a[1] is None and b[1] is None)
    print(("✓ " if same else "✗ ") + f"两者作用域一致 = {same}")
