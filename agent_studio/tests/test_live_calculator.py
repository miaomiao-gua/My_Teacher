#!/usr/bin/env python
"""真链路复测：模型会不会**主动**用图形计算器，并且只写公式。

为什么值得单独测：工具注册了、提示词写了，不代表模型真会选它。
学生的一句「画个圆和抛物线」必须落到 graph_calculator 上，
而不是回一句「好的，我画一下」然后什么都没有（用户实测踩过这个坑）。

跑法（服务需在跑）：py -3.14 agent_studio/tests/test_live_calculator.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

BASE = "http://127.0.0.1:5001"
SESSION = os.environ.get("CALC_TEST_SESSION", "live_calc_probe")   # 一次性会话，跑完删掉

QUESTIONS = [
    "帮我画个图：圆 x^2+y^2=16，再画抛物线 y=x^2-4，并用文字标出圆心",
    "在同一张图上画 y<=x+1 的区域，还有参数 a 控制的直线 y=a*x-2",
]


def run(message: str) -> dict:
    body = json.dumps({"message": message, "session_id": SESSION}).encode()
    req = urllib.request.Request(f"{BASE}/agent/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    out = {"tools": [], "ui": [], "text": "", "errors": [], "specs": []}
    with urllib.request.urlopen(req, timeout=600) as resp:
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
                out.setdefault("results", []).append(
                    ("OK " if ev.get("ok") else "FAIL ") + str(ev.get("summary", ""))[:130].replace("\n", " "))
            elif t == "ui":
                spec = ev.get("spec") or {}
                out["ui"].append([i.get("type") for i in spec.get("items", [])])
                out["specs"].append(spec)
            elif t == "error":
                out["errors"].append(str(ev.get("message"))[:160])
            elif t == "text":
                out["text"] += ev.get("delta") or ""
    return out


def main() -> int:
    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        if not ok:
            failures += 1
        print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))

    for i, q in enumerate(QUESTIONS, 1):
        print(f"\n===== 第 {i} 问：{q} =====")
        r = run(q)
        print("  调用工具: " + (", ".join(r["tools"]) or "（无）"))
        for line in r.get("results", [])[:3]:
            print("    " + line)
        if r["text"]:
            print("  模型说: " + r["text"][:150].replace("\n", " "))
        check("调用了图形计算器", "graph_calculator" in r["tools"], str(r["tools"]))
        # 注意别写成 [s for s in ... for it in ...]：那样收上来的是外层 spec 而不是组件本身
        calcs = [it for s in r["specs"] for it in (s.get("items") or []) if it.get("type") == "calculator"]
        check("画面上真的出现了计算器", bool(calcs))
        rows = (calcs[0].get("expressions") or []) if calcs else []
        shown = [(x.get("expr") if isinstance(x, dict) else x) for x in rows]
        if calcs:
            print("  公式行: " + str(shown)[:200])
            check("公式是非空的", len(rows) > 0, str(len(rows)) + " 行")
        if i == 1:
            check("圆用了隐式方程", any("x^2+y^2" in str(s) for s in shown))
            check("有文字标注", any(isinstance(x, dict) and x.get("text") for x in rows), str(shown)[:120])
        else:
            check("出现不等式区域", any("<=" in str(s) or ">=" in str(s) for s in shown))
            check("参数滑块 a 出现",
                  any((p or {}).get("name") == "a" for p in (calcs[0].get("params") or [])) if calcs else False)
        check("没有报错", not r["errors"], str(r["errors"])[:120])

    print()
    if failures == 0:
        print("全部通过 ✓")
    else:
        print(f"{failures} 项失败 ✗")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
