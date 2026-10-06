#!/usr/bin/env python
"""真人链路：它到底知不知道自己「在哪个文件夹」。

跑法（服务需在跑）：py -3.14 tests/test_live_whereami.py

做法：先建一个属于「数学P1 / 9709」的对话，然后问它我们俩现在在哪儿 ——
它要么直接从系统提示词的「你现在在哪里」答出来，要么调 list_conversations。
两种都算对；**答不出、答错、或说不知道**都算失败。
"""

from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:5001"


def post(path: str, payload: dict, timeout: int = 60):
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def chat(session_id: str, message: str) -> dict:
    body = json.dumps({"message": message, "session_id": session_id}).encode()
    req = urllib.request.Request(BASE + "/agent/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    out = {"tools": [], "text": "", "errors": [], "reason": None}
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except Exception:
                continue
            if ev.get("type") == "tool.call":
                out["tools"].append(ev["name"])
            elif ev.get("type") == "text":
                out["text"] += ev.get("delta") or ""
            elif ev.get("type") == "error":
                out["errors"].append(str(ev.get("message"))[:120])
            elif ev.get("type") == "run.end":
                out["reason"] = ev.get("reason")
    return out


def main() -> int:
    created = post("/agent/session/new", {"subject": "数学P1", "textbook": "9709", "title": "位置测试"})
    sid = created["session"]["id"]
    print(f"建了一个对话：{created['session']['subject']} / {created['session']['textbook']} → {sid}")

    r = chat(sid, "我们现在在哪个文件夹里？这个对话叫什么名字？")
    print(f"\n它调用的工具: {r['tools'] or '（无，直接答的）'}")
    print(f"错误: {r['errors'] or '无'}")
    print(f"结束: {r['reason']}")
    print(f"\n它的回答：\n{r['text'][:600]}")

    ok = ("数学P1" in r["text"] and "9709" in r["text"])
    print("\n" + ("回答里正确说出了学科与教材 ✓" if ok else "回答没说出文件夹 ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
