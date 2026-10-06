"""桌面版验收脚本：真的把桌面壳启动起来，截图回来看。

为什么要这么做：桌宠是个**真窗口**，单元测试只能验逻辑，验不了「它到底长什么样、
是不是真在屏幕上」。这个脚本负责：起壳 → 等桌宠心跳 → 截图（桌宠区域 + 整屏）→ 收工。

跑法：py -3.14 desktop/_verify_desktop.py [--port 5011] [--keep]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "_probe_shots"
PY = sys.executable


def read_state(data_dir: Path) -> dict:
    try:
        p = data_dir / "pet_state.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5011)
    ap.add_argument("--keep", action="store_true", help="验完不关（想自己看看时用）")
    ap.add_argument("--seconds", type=float, default=26.0, help="最多等多久")
    ap.add_argument("--exe", action="store_true", help="跑打包好的 exe（而不是源码）")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    log = OUT / "desktop_run.log"
    data_dir = ROOT / "data"        # 源码运行时就是它

    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    if args.exe:
        exe = ROOT / "dist_desktop" / "艾琳老师.exe"
        if not exe.is_file():
            print(f"❌ 还没打包：{exe}")
            return 1
        cmd = [str(exe), "--port", str(args.port)]
        print(f"跑打包好的 exe：{exe}（{exe.stat().st_size / 1024 / 1024:.1f} MB）")
    else:
        cmd = [PY, str(HERE / "shell.py"), "--port", str(args.port)]
        print(f"跑源码：{HERE / 'shell.py'}")

    with log.open("w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT), env=env, stdout=lf, stderr=subprocess.STDOUT,
            creationflags=0x00000008 if os.name == "nt" else 0,   # DETACHED（不需要窗口）
        )
    print(f"启动 pid={proc.pid}，日志 {log}")

    deadline = time.time() + args.seconds
    state: dict = {}
    status: dict = {}
    while time.time() < deadline:
        state = read_state(data_dir)
        if state.get("ts") and time.time() - float(state["ts"]) < 5:
            break
        time.sleep(0.5)
    try:
        import httpx
        status = httpx.get(f"http://127.0.0.1:{args.port}/agent/pet/status", timeout=4).json()
    except Exception as exc:
        status = {"error": str(exc)}

    print("\n--- 桌宠心跳（pet_state.json）---")
    print(json.dumps(state, ensure_ascii=False, indent=1))
    print("--- /agent/pet/status ---")
    print(json.dumps(status, ensure_ascii=False, indent=1)[:1500])

    shots = []
    try:
        from PIL import ImageGrab
        full = ImageGrab.grab()                     # 先整屏：顺便拿到「物理分辨率」
        p2 = OUT / "desktop_full.png"
        full.save(p2)
        shots.append(str(p2))
        print(f"整屏截图 {full.size} → {p2}")
        # 桌宠区域：tk 用的是逻辑像素，高分屏上要按 物理/逻辑 的比例换算，否则位置会偏
        scr = state.get("screen") or [full.width, full.height]
        sx = full.width / float(scr[0] or full.width)
        sy = full.height / float(scr[1] or full.height)
        if state.get("x") is not None:
            x, y = int(state["x"]) * sx, int(state["y"]) * sy
            pw = int((state.get("pet_size") or [128, 148])[0] * sx)
            box = (max(0, int(x - pw * 0.6)), max(0, int(y - pw * 0.8)),
                   int(x + pw * 1.7), int(y + pw * 1.6))
            img = ImageGrab.grab(bbox=box)
            p1 = OUT / "desktop_pet_area.png"
            img.save(p1)
            shots.append(str(p1))
            print(f"桌宠区域截图（缩放 {sx:.2f}，框 {box}）→ {p1}")
    except Exception as exc:
        print(f"截图失败：{type(exc).__name__}: {exc}")

    print("\n--- 启动日志 ---")
    try:
        print(log.read_text(encoding="utf-8")[-2500:])
    except Exception:
        pass

    if not args.keep:
        # 用 Popen.terminate() 直接结束：不要走 taskkill + 管道抓输出 ——
        # 受限环境里抓子进程输出会被拒（起子进程拿不到管道），于是「以为关了其实没关」。
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        time.sleep(0.5)
        alive = proc.poll() is None
        print("已结束。" if not alive else f"⚠️ 没关掉，pid={proc.pid} 还在")
    else:
        print(f"（--keep）进程还在跑：pid={proc.pid}，端口 {args.port}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
