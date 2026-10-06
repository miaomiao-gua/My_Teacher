#!/usr/bin/env python
"""启动器自测：**必须启动新项目 agent_studio，不能指向旧的 agent/ 包**。

为什么专门为启动写测试：
2026-10-06 发现仓库根的 `run_agent.py` 里写的是 `agent.server:app` ——
那是九月的旧包（16 个工具、没有桌宠/自动归档/新界面）。后果是
**学生双击 start_agent.cmd 看到的是老界面，新做的东西一个都看不到**；
而我调试时手动跑的是 `agent_studio/run.py`，所以一直没暴露。
这种「验错了入口」的错必须用测试钉死：谁都别再改回去。

跑法：py -3.14 tests/test_launcher.py
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # agent_studio/
REPO = ROOT.parent                                     # 仓库根
sys.path.insert(0, str(ROOT))

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    print("\n[1] 入口指向的是新项目")
    run_py = (ROOT / "run.py").read_text(encoding="utf-8")
    check('agent_studio/run.py 启动的是 "kernel.server:app"', '"kernel.server:app"' in run_py)

    root_launcher = REPO / "run_agent.py"
    check("仓库根有 run_agent.py（学生双击的那条路）", root_launcher.is_file())
    if root_launcher.is_file():
        text = root_launcher.read_text(encoding="utf-8")
        check("它指向 agent_studio", "agent_studio" in text)
        check("它真的转交给 agent_studio/run.py", "run_path" in text and "run.py" in text)
        # 只在**真正的调用参数**里找旧包名：注释/文档里提到 `agent.server:app`
        # 是故意的（写清楚这个坑），不该被判成错误。
        import ast

        bad = []
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Call):
                for arg in list(node.args) + [k.value for k in node.keywords]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "agent.server" in arg.value:
                        bad.append(arg.value)
        check("没有任何调用把旧包 agent.server 当启动目标", not bad, str(bad))

    cmd = REPO / "start_agent.cmd"
    if cmd.is_file():
        ctext = cmd.read_text(encoding="utf-8", errors="replace")
        check("start_agent.cmd 调的是 run_agent.py", "run_agent.py" in ctext)

    print("\n[2] 「已经有一个在跑了」要好好说话（不能抛端口占用）")
    import run as runner  # agent_studio/run.py（import 不会执行 main）

    check("有 _already_running() 这个判断", hasattr(runner, "_already_running"))
    port = free_port()
    check(f"没人监听的端口 {port} 返回 False", runner._already_running("127.0.0.1", port) is False)

    live = runner._already_running("127.0.0.1", 5001)
    if live:
        check("真服务在跑时返回 True（双击两次不会报错）", True, "5001 上有服务")
    else:
        print("  ·  5001 上没有服务，跳过「已在跑」的正向验证（不算失败）")

    print("\n[3] 桌面壳也不许指向旧包")
    shell = (ROOT / "desktop" / "shell.py").read_text(encoding="utf-8")
    check("desktop/shell.py 起的是本项目内核（kernel.server / run.py）",
          ("kernel.server" in shell) or ("run.py" in shell))

    print("\n" + ("全部通过 ✓" if _failures == 0 else f"失败 {_failures} 项 ✗"))
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
