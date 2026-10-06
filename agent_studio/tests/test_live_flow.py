#!/usr/bin/env python
"""真人链路复测：把用户实际踩过的坑用同一句话再跑一遍。

跑法（服务需在跑）：
    py -3.14 tests/test_live_flow.py            # 全跑
    py -3.14 tests/test_live_flow.py --only 9709

两个场景：
  1) 「给我讲一下9709」——用户实测：模型回了 27 字「请稍等片刻」就结束，再问一次直接空回复。
     现在应该被「只说不做」兜底抓住并真的动手（备课），或者至少给出明确的失败提示而不是静默。
  2) 「帮我找9709的教材」——应该先真实搜索、列出候选、**等用户确认**，不擅自下载。
"""

from __future__ import annotations

import argparse
import json
import urllib.request

BASE = "http://127.0.0.1:5001"


def run(message: str, session_id: str) -> dict:
    body = json.dumps({"message": message, "session_id": session_id}).encode()
    req = urllib.request.Request(f"{BASE}/agent/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    out = {"tools": [], "ui": [], "notices": [], "errors": [], "text": "", "reason": None, "steps": 0}
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
                out["tools"].append(ev["name"])
            elif t == "tool.result":
                out.setdefault("results", []).append(
                    ("OK " if ev.get("ok") else "FAIL ") + str(ev.get("summary", ""))[:110].replace("\n", " "))
            elif t == "ui":
                out["ui"].append([i.get("type") for i in (ev.get("spec") or {}).get("items", [])])
            elif t == "notice":
                out["notices"].append(str(ev.get("message"))[:80])
            elif t == "error":
                out["errors"].append(str(ev.get("message"))[:150])
            elif t == "text":
                out["text"] += ev.get("delta") or ""
            elif t == "run.end":
                out["reason"] = ev.get("reason")
                out["steps"] = ev.get("steps")
    return out


def show(title: str, r: dict) -> None:
    print(f"\n{'=' * 70}\n{title}")
    print(f"  调用工具: {r['tools'] or '（无）'}")
    for line in r.get("results", [])[:4]:
        print("   ", line)
    print(f"  界面: {r['ui'] or '（无）'}")
    if r["notices"]:
        print(f"  系统提示: {r['notices']}")
    if r["errors"]:
        print(f"  错误: {r['errors']}")
    print(f"  结束: reason={r['reason']} steps={r['steps']} 正文={len(r['text'])} 字")
    print(f"  正文开头: {r['text'][:150]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    args = parser.parse_args()

    problems = []
    if args.only in ("", "9709"):
        r = run("给我讲一下9709", "live_9709")
        show("场景 1：『给我讲一下9709』（用户实测过的失败样本）", r)
        # 判定：要么真的动手了（调工具），要么给出了明确错误 —— 绝不能是「几句空话 + 静默结束」
        acted = bool(r["tools"])
        complained = bool(r["errors"])
        if not acted and not complained and len(r["text"]) < 60:
            problems.append("场景1：又是「说了不做 + 静默结束」")

    if args.only in ("", "materials"):
        r2 = run("帮我找一下 A-Level 9709 数学的官方教材和大纲，先给我看看有哪些可以下", "live_mat")
        show("场景 2：『帮我找9709的教材』（应该先搜索、列候选、等确认）", r2)
        searched = any(t in ("search_materials", "check_link") for t in r2["tools"])
        downloaded = "download_material" in r2["tools"]
        if not searched:
            problems.append("场景2：没有真的去搜")
        if downloaded:
            problems.append("场景2：没等用户确认就下载了")

    print(f"\n{'=' * 70}")
    print("符合预期 ✓" if not problems else "问题：\n  - " + "\n  - ".join(problems))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
