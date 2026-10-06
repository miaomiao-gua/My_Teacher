#!/usr/bin/env python
"""启动 Agent 服务。

    py -3.14 run_agent.py                 # http://127.0.0.1:5001
    py -3.14 run_agent.py --port 5002
    py -3.14 run_agent.py --reload        # 改代码自动重启（调试内核时用）

与老服务并存：
    老 Flask（Galgame 界面）  :5000   →  py -3.14 server/app.py
    Agent 内核（新）          :5001   →  py -3.14 run_agent.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="My_Teacher Agent 服务")
    parser.add_argument("--host", default=os.environ.get("AGENT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENT_PORT", "5001")))
    parser.add_argument("--reload", action="store_true", help="代码改动自动重启（开发用）")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("缺少 uvicorn，请用带依赖的解释器运行：py -3.14 run_agent.py")
        raise SystemExit(1)

    print(f"[agent] 启动中 → http://{args.host}:{args.port}/agent")
    print("[agent] 健康检查：curl http://127.0.0.1:%d/agent/health" % args.port)
    uvicorn.run(
        "agent.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
