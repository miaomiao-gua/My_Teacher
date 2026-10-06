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


def _already_running(host: str, port: int) -> bool:
    """这个端口上是不是已经有一个**我们自己的**服务在跑？

    为什么要问这一句：学生双击启动器时，如果上一次的服务还开着（比如最小化的黑窗口），
    uvicorn 会抛一句 `[Errno 10048] address already in use` 然后退出 ——
    对非程序员来说这就是「打不开了」。这里改成先说人话：已经开着了，直接看界面。
    """
    try:
        import httpx

        r = httpx.get(f"http://{host}:{port}/agent/health", timeout=2.0)
        if r.status_code != 200:
            return False
        data = r.json()
        return bool(data.get("ok")) and "tools" in data
    except Exception:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 教学 Agent 工作台")
    parser.add_argument("--host", default=os.environ.get("AGENT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENT_PORT", "5001")))
    parser.add_argument("--reload", action="store_true", help="代码改动自动重启（开发用）")
    parser.add_argument("--log-level", default="info")
    parser.add_argument("--force", action="store_true", help="端口上已经有服务时也强行再起一个（一般不用）")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("缺少 uvicorn。请用装好依赖的解释器运行：py -3.14 run.py")
        raise SystemExit(1)

    if not args.force and not args.reload and _already_running(args.host, args.port):
        print("=" * 62)
        print("  已经有一个服务在跑了（不用再开一个）")
        print(f"  界面： http://{args.host}:{args.port}/agent/app")
        print(f"  桌宠： http://{args.host}:{args.port}/agent/pet")
        print("  想关掉它：关掉那个写着「AI 教学 Agent 工作台」的黑窗口。")
        print("=" * 62)
        return

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
