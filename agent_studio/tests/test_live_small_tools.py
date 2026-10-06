#!/usr/bin/env python
"""真链路：模型会不会主动用这三件小工具，并且**拼出完整的界面**。

这是「小工具」这一版最该验的一件事：
工具注册了、提示词写了，都只是「能」；真的把一节完整的课用界面搭出来，才算「会」。

跑法（服务需在跑）：py -3.14 agent_studio/tests/test_live_small_tools.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = "http://127.0.0.1:5001"
SESSION = os.environ.get("TOOLS_TEST_SESSION", "live_tools_probe")

# 每道题只声明「**产出的界面里必须有什么**」，不规定走哪条路：
# 模型既可以直接调 geometry_board，也可以用 render_ui 写 {"type":"geometry",...}，
# 两条路都对（组件是同一个）。用「走了哪个工具」当验收标准是我第一版的错。
INTERACTIVE = {"geometry", "datalab", "solver", "calculator", "quiz", "slider", "board"}

CASES = [
    {
        "ask": "帮我讲一下勾股定理，我想让学生自己动手拖一拖、自己发现规律",
        "want_types": ["geometry"],
        "min_types": 1,
    },
    {
        "ask": "用界面讲一下平均数和中位数有什么区别，最好能改数字看变化",
        "want_types": ["datalab"],
        "min_types": 1,
    },
    {
        "ask": "讲一下一元二次方程的判别式，要能把过程一步步展开，学生还能改系数",
        "want_types": ["solver"],
        "min_types": 1,
    },
    {
        "ask": "给我搭一节完整的课：勾股定理。要有能动手操作的、有数据、有解方程、还有一道小测，"
               "用标签页组织起来",
        "want_types": ["geometry"],
        "min_types": 4,          # 「完整」的量化标准：至少四种组件
        "min_interactive": 2,    # 而且至少两件是能动手的
        "max_text": 9000,
    },
]


def run(message: str) -> dict:
    body = json.dumps({"message": message, "session_id": SESSION}).encode()
    req = urllib.request.Request(f"{BASE}/agent/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    out = {"tools": [], "specs": [], "text": "", "errors": [], "results": []}
    with urllib.request.urlopen(req, timeout=900) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except Exception:
                continue
            t = ev.get("type")
            if t == "tool.call":
                out["tools"].append(ev.get("name"))
            elif t == "tool.result":
                out["results"].append(("OK " if ev.get("ok") else "FAIL ") +
                                      str(ev.get("summary", ""))[:150].replace("\n", " "))
            elif t == "ui":
                spec = ev.get("spec") or {}
                out["specs"].append(spec)
            elif t == "error":
                out["errors"].append(str(ev.get("message"))[:160])
            elif t == "text":
                out["text"] += ev.get("delta") or ""
    return out


def component_types(spec) -> list:
    types = []
    stack = [spec]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("type"):
                types.append(str(node["type"]))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return types


def main() -> int:
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        if not ok:
            failures += 1
        print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))

    for i, case in enumerate(CASES, 1):
        print(f"\n===== 第 {i} 问：{case['ask']} =====")
        r = run(case["ask"])
        print("  调用工具: " + (", ".join(r["tools"]) or "（无）"))
        for line in r["results"][:3]:
            print("    " + line)
        all_types = []
        for spec in r["specs"]:
            all_types.extend(component_types(spec))
        uniq = sorted(set(all_types))
        print("  界面组件: " + (", ".join(uniq) or "（没有界面）"))
        missing = [t for t in case["want_types"] if t not in uniq]
        check("界面里有 " + "/".join(case["want_types"]) + "（走哪条工具都行）",
              not missing, "缺 " + str(missing) + "；实际 " + str(uniq))
        check(f"界面至少有 {case['min_types']} 种组件（不是光说不练）",
              len(uniq) >= case["min_types"], f"{len(uniq)} 种：{', '.join(uniq)}")
        if case.get("min_interactive"):
            n_inter = len([t for t in uniq if t in INTERACTIVE])
            check(f"其中至少 {case['min_interactive']} 件是能动手的",
                  n_inter >= case["min_interactive"], f"{n_inter} 件：{', '.join(t for t in uniq if t in INTERACTIVE)}")
        check("没有报错", not r["errors"], str(r["errors"])[:140])
        limit = case.get("max_text", 4000)
        check(f"正文没有失控（< {limit} 字）", len(r["text"]) < limit, f"{len(r['text'])} 字")

    print()
    if failures == 0:
        print("全部通过 ✓")
    else:
        print(f"{failures} 项失败 ✗")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
