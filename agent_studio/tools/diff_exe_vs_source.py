#!/usr/bin/env python
"""对比「源码服务」和「打包后的 exe」差了什么（工具/形象/接口）。

为什么需要它：打包后最容易出的事就是**悄悄少功能** ——
某个工具因为动态导入没被 PyInstaller 看见而没注册、
某个扫盘目录在 frozen 环境下解析成了别的路径。
源码里一切正常，只有双击 exe 的人才碰得到。

用法：py -3.14 tools/diff_exe_vs_source.py
     （会自动起一个 exe 实例，用完关掉；源码那个 5001 要已经在跑）
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 默认用**用户实际会双击的那份**（agent_studio\MyTeacher.exe，旁边就是 data/），
# 而不是 dist_desktop 里那份 —— 因为 exe 的「项目根」是按它自己所在目录算的，
# 放错地方就会看不到 client/ 里的模型（这个坑我踩过：dist_desktop 里只扫到 3 个形象）。
EXE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "MyTeacher.exe"


def wait_port(exe: Path, timeout: float = 60.0) -> int | None:
    lock = ROOT / "data" / "desktop.lock"
    t0 = time.time()
    while time.time() < t0 + timeout:
        time.sleep(1.2)
        try:
            info = json.loads(lock.read_text(encoding="utf-8"))
            if float(info.get("started") or 0) >= t0 - 5 and info.get("port"):
                return int(info["port"])
        except Exception:
            continue
    return None


def snapshot(base: str) -> dict:
    import httpx

    out: dict = {"base": base}
    try:
        g = httpx.get(base + "/agent/tools", timeout=30.0).json()
        out["tools"] = sorted(t.get("name") for t in (g.get("tools") or []))
    except Exception as exc:  # noqa: BLE001
        out["tools_error"] = str(exc)
    try:
        m = httpx.get(base + "/agent/live2d/models", timeout=60.0).json()
        out["models"] = sorted(x.get("name") for x in (m.get("models") or []))
        out["model_dirs"] = sorted(x.get("dir") for x in (m.get("models") or []))
    except Exception as exc:  # noqa: BLE001
        out["models_error"] = str(exc)
    try:
        h = httpx.get(base + "/agent/health", timeout=30.0).json()
        out["health"] = {"tools": h.get("tools"), "chain": [c.get("label") for c in (h.get("chain") or [])]}
    except Exception:
        pass
    return out


def main() -> int:
    print("先看源码服务（5001）…")
    src = snapshot("http://127.0.0.1:5001")

    print("起一个 exe 实例（--no-window --no-pet：不起窗口，只比接口）…")
    proc = subprocess.Popen([str(EXE), "--no-window", "--no-pet"], cwd=str(EXE.parent))
    try:
        port = wait_port(EXE)
        if not port:
            print("exe 起不来，先看 data/logs/desktop.log")
            return 1
        exe = snapshot(f"http://127.0.0.1:{port}")
    finally:
        try:
            proc.terminate(); proc.wait(timeout=10)
        except Exception:
            try: proc.kill()
            except Exception: pass

    print("\n=== 工具 ===")
    a, b = set(src.get("tools") or []), set(exe.get("tools") or [])
    print(f"源码 {len(a)} 个 / exe {len(b)} 个")
    if a - b: print("  ★ exe 缺这些:", sorted(a - b))          # a 有 b 没有 = exe 缺
    if b - a: print("  exe 多了（源码没有）:", sorted(b - a))
    if a == b: print("  完全一致")

    print("\n=== 形象（Live2D 模型）===")
    ma, mb = set(src.get("models") or []), set(exe.get("models") or [])
    print(f"源码 {len(ma)} 个 / exe {len(mb)} 个")
    if ma - mb: print("  只在源码里有:", sorted(ma - mb))
    if mb - ma: print("  只在 exe 里有:", sorted(mb - ma))
    print("\n  源码扫到的目录：")
    for d in src.get("model_dirs") or []:
        print("   ", d)
    print("  exe 扫到的目录：")
    for d in exe.get("model_dirs") or []:
        print("   ", d)

    print("\n=== 健康 ===")
    print("  源码:", src.get("health"))
    print("  exe :", exe.get("health"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
