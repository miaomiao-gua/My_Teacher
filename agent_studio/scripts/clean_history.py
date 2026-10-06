#!/usr/bin/env python
"""清理被内核提示污染的会话历史（一次性维护脚本）。

背景：早期版本把「催促消息」写进了会话历史，历史里于是混进了"学生说的话"——
例如「你刚才只是在描述计划…」「现在必须调用一个工具…」。模型下一轮看到这些，
就开始复述提示词、不干活（用户看到的现象是"卡住/打转"）。

这个脚本把它们从历史里摘掉，并删掉空的 assistant 消息（那些是"模型没返回内容"留下的）。

跑法：py -3.14 scripts/clean_history.py            # 只报告，不改
      py -3.14 scripts/clean_history.py --apply    # 真的清理（会先备份）
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SESSIONS = ROOT / "data" / "sessions"
BACKUP = ROOT / "data" / "_history_backup"

# 内核注入过的提示语特征（这些绝不该出现在"学生说的话"里）
INJECTED_MARKERS = (
    "你刚才只是在描述计划",
    "现在必须调用一个工具",
    "（系统提示：你上一条回复是空的",
    "（系统提示：你上一条回复是空的，学生什么都没看到",
)


def should_drop(msg: dict) -> str:
    role = msg.get("role")
    content = msg.get("content") or ""
    if role == "user" and any(mk in content for mk in INJECTED_MARKERS):
        return "内核提示混进了用户消息"
    if role == "assistant" and not content.strip() and not msg.get("tool_calls"):
        return "空的助手消息（模型没返回内容）"
    # 模型复述内核提示的"自言自语"（例如「（系统提示：检测到你写占位文字…）」）：
    # 它也是污染 —— 留在上下文里会让下一轮继续跑偏。
    if role == "assistant" and ("检测到你在回复中写" in content or "这仍然是占位文字" in content):
        return "模型复述内核提示（污染上下文）"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的清理（默认只报告）")
    args = ap.parse_args()

    total_files = 0
    total_dropped = 0
    for path in sorted(SESSIONS.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        msgs = data.get("messages") or []
        dropped = [(i, should_drop(m)) for i, m in enumerate(msgs) if should_drop(m)]
        if not dropped:
            continue
        total_files += 1
        total_dropped += len(dropped)
        print(f"\n{path.stem}（{data.get('subject') or '-'}/{data.get('textbook') or '-'} "
              f"{data.get('title') or ''}）共 {len(msgs)} 条消息，要删 {len(dropped)} 条：")
        for i, why in dropped:
            print(f"   [{i}] {why}：{(msgs[i].get('content') or '')[:60]}")
        if args.apply:
            BACKUP.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, BACKUP / f"{path.stem}.{int(time.time())}.json")
            data["messages"] = [m for i, m in enumerate(msgs) if not should_drop(m)]
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)

    print(f"\n受影响会话：{total_files} 个，共 {total_dropped} 条消息")
    if not args.apply and total_dropped:
        print("（这是预演。加 --apply 才会真的清理，并会先备份到 data/_history_backup/）")
    elif args.apply:
        print(f"已清理，备份在 {BACKUP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
