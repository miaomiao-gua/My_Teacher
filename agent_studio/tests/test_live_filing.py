#!/usr/bin/env python
"""真链路：**只用聊天**，看 AI 会不会自己把对话放进正确的文件夹。

这是「新增功能」的验收：学生不说「帮我归档到数学」，只是正常听课，
对话最后应该出现在左栏的「数学 / 9709」下面，而不是「未分类」。

跑法（服务需在跑）：py -3.14 tests/test_live_filing.py
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:5001"

CASES = [
    {"ask": "我在学 A-Level 9709 数学，帮我讲讲 sin 和 cos 的图像有什么关系？",
     "want_subject": "数学", "want_textbook": "9709"},
    {"ask": "物理的受力分析我总搞混，用界面给我讲一下斜面上的物体受哪几个力",
     "want_subject": "物理", "want_textbook": None},
]


def post(path: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def get(path: str) -> dict:
    return json.load(urllib.request.urlopen(BASE + path, timeout=60))


def chat(session_id: str, message: str) -> dict:
    """发一条消息并读完 SSE，顺便记下工具调用与提示"""
    body = json.dumps({"message": message, "session_id": session_id}).encode()
    req = urllib.request.Request(BASE + "/agent/chat", data=body,
                                headers={"Content-Type": "application/json"})
    out = {"tools": [], "notices": [], "errors": [], "text": ""}
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
            elif t == "notice":
                out["notices"].append(str(ev.get("message"))[:120])
            elif t == "error":
                out["errors"].append(str(ev.get("message"))[:120])
            elif t == "text":
                out["text"] += ev.get("delta") or ""
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=int, help="只跑第 N 个场景")
    args = ap.parse_args()

    failures = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        if not ok:
            failures += 1
        print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))

    cases = CASES if not args.only else [CASES[args.only - 1]]
    for i, case in enumerate(cases, 1):
        print(f"\n===== 场景 {i}：{case['ask']} =====")
        created = post("/agent/session/new", {"subject": "", "textbook": ""})
        sid = created.get("id") or (created.get("session") or {}).get("id")
        print(f"  新对话 {sid}（起始归属：未分类）")
        r = chat(sid, case["ask"])
        print("  调用工具: " + (", ".join(r["tools"]) or "（无）"))
        for n in r["notices"][:3]:
            print("  提示: " + n)
        if r["errors"]:
            print("  报错: " + str(r["errors"])[:160])

        info = get(f"/agent/session/{sid}")
        subject = (info.get("subject") or "").strip()
        textbook = (info.get("textbook") or "").strip()
        filed_by = info.get("filed_by")
        print(f"  最终归属: {subject or '（未分类）'} / {textbook or '（未指定教材）'}"
              f"　由 {filed_by or '?'} 归档")
        print(f"  模型理由: {info.get('filed_reason') or '（无）'}")

        check("对话被归档了（不再是未分类）", bool(subject), subject or "还是未分类")
        check("学科判断正确", case["want_subject"] in subject, f"{subject} ≠ {case['want_subject']}")
        if case.get("want_textbook"):
            check("教材判断正确（或老实说未指定）",
                  case["want_textbook"] in textbook or textbook == "未指定教材", textbook)
        # 两条路都算数：模型自己调了工具，或后台兜底归的（filed_by=model），或界面上给了提示
        evidence = ("file_conversation" in r["tools"]) or filed_by == "model" or \
                   any("归档" in n for n in r["notices"])
        check("两条路径至少走通一条（模型自己调工具 / 后台兜底）", evidence,
              f"工具={r['tools']} filed_by={filed_by} 提示={r['notices'][:1]}")
        check("没有报错", not r["errors"], str(r["errors"])[:100])
        check("正文没有把归档当话题聊",
              "归到" not in r["text"][:200] and "文件夹" not in r["text"][:120], r["text"][:80])

        # 左栏树里能看到它挂在正确的学科下
        tree = get("/agent/sessions").get("tree") or []
        found = None
        for subj in tree:
            for book in subj.get("textbooks") or []:
                for s in book.get("sessions") or []:
                    if s.get("id") == sid:
                        found = (subj.get("name"), book.get("name"))
        check("左栏树里挂在正确的位置", bool(found) and case["want_subject"] in (found[0] or ""),
              str(found))

        try:
            urllib.request.urlopen(urllib.request.Request(f"{BASE}/agent/session/{sid}", method="DELETE"),
                                   timeout=30)
            print("  （已清掉这个测试对话）")
        except Exception:
            pass

    print()
    if failures == 0:
        print("全部通过 ✓")
    else:
        print(f"{failures} 项失败 ✗")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
