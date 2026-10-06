#!/usr/bin/env python
"""清掉**我自己测试**留下的对话 / 提醒，别让用户的历史列表里一堆「只回两个字：收到」。

为什么值得单独写个脚本：自动化探针每跑一次就会新建一个对话（小聊天没有历史时），
跑个十几遍以后学生打开界面看到的就是一列测试垃圾。判断依据用「标题 + 创建时间」两条：
只删标题就是测试用语、且是最近 N 分钟内创建的，绝不碰用户自己的对话。

用法：py -3.14 tools/cleanup_test_sessions.py [--minutes 240] [--apply]
      不带 --apply 只列出会删哪些（默认干跑）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel.config import SESSIONS_DIR, WORKSPACES_DIR  # noqa: E402

# 只有标题**完全等于**这些的才会被考虑（避免误伤真实对话）
JUNK_TITLES = {
    "只回两个字：收到",
    "新对话",
    "你好",
    "收到",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=240.0, help="只看最近这么多分钟内创建的")
    ap.add_argument("--apply", action="store_true", help="真的删（默认只列出来）")
    args = ap.parse_args()

    cut = time.time() - args.minutes * 60
    doomed = []
    for path in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        title = str(data.get("title") or "").strip()
        created = float(data.get("created_at") or data.get("updated_at") or 0)
        msgs = data.get("messages") or []
        # 「新对话」这种标题可能属于真对话：要求内容也很少（≤4 条）才算测试垃圾
        looks_junk = title in JUNK_TITLES and (title != "新对话" or len(msgs) <= 4)
        if looks_junk and created >= cut:
            doomed.append((path, title, created, len(msgs)))

    print(f"最近 {args.minutes:.0f} 分钟内的对话里，像测试残留的有 {len(doomed)} 个：")
    for path, title, created, n in doomed:
        when = time.strftime("%m-%d %H:%M", time.localtime(created))
        print(f"  {path.stem}  {when}  {n} 条消息  「{title}」")

    if not args.apply:
        print("\n（干跑。要真删就加 --apply）")
        return 0

    for path, title, created, n in doomed:
        path.unlink(missing_ok=True)
        ws = WORKSPACES_DIR / path.stem
        if ws.is_dir():
            import shutil

            shutil.rmtree(ws, ignore_errors=True)
    print(f"\n已删 {len(doomed)} 个；剩下 {len(list(SESSIONS_DIR.glob('*.json')))} 个对话。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
