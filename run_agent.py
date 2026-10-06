#!/usr/bin/env python
"""启动 Agent 服务（**新项目 agent_studio**）。

    py -3.14 run_agent.py                 # http://127.0.0.1:5001
    py -3.14 run_agent.py --port 5002
    py -3.14 run_agent.py --reload        # 改代码自动重启（调试内核时用）

与老服务并存：
    老 Flask（Galgame 界面）  :5000   →  py -3.14 server/app.py
    Agent 内核（新）          :5001   →  py -3.14 run_agent.py   ← 就是这个

⚠️ 历史坑（2026-10-06 修）：这个文件以前写的是 `agent.server:app`，
   指向仓库根那个**九月的旧包 `agent/`**（16 个工具、没有桌宠/自动归档/新界面）。
   结果就是「双击 start_agent.cmd 打开的是老界面，新做的东西一个都看不到」——
   而我调试时一直手动跑 `agent_studio/run.py`，所以这个错一直没暴露。
   现在统一转给 `agent_studio/run.py`，只有一份实现，不会再分叉。
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_STUDIO = HERE / "agent_studio"
ENTRY = AGENT_STUDIO / "run.py"


def main() -> None:
    if not ENTRY.is_file():
        print(f"❌ 找不到 {ENTRY}")
        print("   这个启动器已经改为启动 agent_studio/（新项目）；"
              "仓库根那个九月的 agent/ 是旧包，已废弃。")
        raise SystemExit(1)

    print("[启动] 新项目：agent_studio/")
    if (HERE / "agent" / "server.py").is_file():
        print("[提示] 仓库根的 agent/ 是**旧包（已废弃）**，不要改那里；"
              "内核代码在 agent_studio/kernel/。")

    # 切到 agent_studio 再执行 run.py：让工作目录、相对路径、日志位置
    # 跟「在 agent_studio 里直接启动」完全一致（数据目录是按文件位置算的，不受影响）。
    os.chdir(AGENT_STUDIO)
    sys.path.insert(0, str(AGENT_STUDIO))
    runpy.run_path(str(ENTRY), run_name="__main__")


if __name__ == "__main__":
    main()
