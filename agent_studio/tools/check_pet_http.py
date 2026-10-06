#!/usr/bin/env python
"""打一遍桌宠的接口（形象清单 / 换形象 / 按 id 取模型文件 / 防穿越）。

用法：py -3.14 tools/check_pet_http.py [http://127.0.0.1:5001]
需要服务已经在跑（start_agent.cmd）。这也是「换形象」这条链路唯一的端到端自检。
"""
from __future__ import annotations

import json
import sys

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5001"
fails = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global fails
    if not ok:
        fails += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


def main() -> int:
    import httpx

    r = httpx.get(BASE + "/agent/live2d/models", timeout=30.0)
    check("GET /agent/live2d/models = 200", r.status_code == 200, str(r.status_code))
    if r.status_code != 200:
        return 1
    data = r.json()
    models = data.get("models") or []
    cur = data.get("current") or {}
    check("清单里有形象", len(models) > 0, f"{len(models)} 个")
    check("标出了当前形象", bool(cur.get("id")), str(cur.get("id")))
    print("     可选：" + "、".join(f"{m['name']}({m['id']})" for m in models[:8]))

    # 每个模型的入口文件都该能取到（这是「换形象」会不会白屏的关键）
    for m in models[:6]:
        rr = httpx.get(BASE + m["url"], timeout=30.0)
        check(f"取模型文件 {m['name']}", rr.status_code == 200 and len(rr.content) > 100,
              f"HTTP {rr.status_code} {len(rr.content)}B")

    # 换形象 → 再读一次，确认记住了
    if len(models) > 1:
        target = next((m for m in models if m["id"] != cur.get("id")), models[0])
        rr = httpx.post(BASE + "/agent/pet/model", json={"id": target["id"]}, timeout=30.0)
        check(f"POST /agent/pet/model 换成「{target['name']}」", rr.status_code == 200 and rr.json().get("ok"),
              str(rr.status_code))
        again = httpx.get(BASE + "/agent/live2d/models", timeout=30.0).json()
        check("换完以后 current 就是它", (again.get("current") or {}).get("id") == target["id"],
              str((again.get("current") or {}).get("id")))
        # 换回默认，别把学生的选择留在测试用的那个上
        httpx.post(BASE + "/agent/pet/model", json={"id": cur["id"]}, timeout=30.0)
        back = httpx.get(BASE + "/agent/live2d/models", timeout=30.0).json()
        check("能换回来", (back.get("current") or {}).get("id") == cur["id"])

    # 不存在的形象 / 穿越
    rr = httpx.post(BASE + "/agent/pet/model", json={"id": "根本没有这个"}, timeout=30.0)
    check("换不存在的形象会被拒（不是 200）", rr.status_code != 200, str(rr.status_code))
    rr = httpx.get(BASE + "/agent/live2d/model/my_teacher/..%2f..%2f..%2fdata%2fconfig.json", timeout=30.0)
    check("路径穿越被挡住", rr.status_code != 200, f"HTTP {rr.status_code}")

    print("\n" + ("全部通过 ✓" if fails == 0 else f"失败 {fails} 项 ✗"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
