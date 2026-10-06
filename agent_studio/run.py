#!/usr/bin/env python
"""启动 AI 教学 Agent 工作台（本项目独立运行，不依赖老项目）。

    py -3.14 run.py                     # http://127.0.0.1:5001
    py -3.14 run.py --port 5002
    py -3.14 run.py --reload            # 改代码自动重启（开发用）

Windows 上也可以直接双击 start.cmd。
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
    parser = argparse.ArgumentParser(description="AI 教学 Agent 工作台")
    parser.add_argument("--host", default=os.environ.get("AGENT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENT_PORT", "5001")))
    parser.add_argument("--reload", action="store_true", help="代码改动自动重启（开发用）")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("缺少 uvicorn。请用装好依赖的解释器运行：py -3.14 run.py")
        raise SystemExit(1)

    from kernel.config import load_agent_settings

    provider = load_agent_settings().get("llm_provider", "cloud")
    print("=" * 62)
    print("  AI 教学 Agent 工作台")
    print(f"  界面： http://{args.host}:{args.port}/")
    print(f"  模型： {provider}（可用 /agent/settings 切换）")
    print(f"  自检： http://{args.host}:{args.port}/agent/health")
    print("=" * 62)
    uvicorn.run("kernel.server:app", host=args.host, port=args.port,
                reload=args.reload, log_level=args.log_level)


if __name__ == "__main__":
    main()
