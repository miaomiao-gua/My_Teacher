#!/usr/bin/env python
"""量「抓 Live2D 帧」的真实速度，并试几种能提速的写法。

为什么要量：桌宠动起来需要至少 ~8fps。无头 Edge + 软件 WebGL 下抓一帧
（PNG 编码 + base64）可能很贵，如果只有 1~2fps，那还不如用 Pillow 画的那只
（5ms/帧、很顺）。这个脚本就是来定这个结论的。

跑法：py -3.14 tools/bench_frames.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from desktop import frames  # noqa: E402

BASE = "http://127.0.0.1:5001"


def measure(size, seconds: float = 6.0, optimize: bool = True, label: str = "") -> None:
    src = frames.FrameSource(f"{BASE}/agent/pet?frames=1", size=size,
                             profile=ROOT / "data" / "browser_frames", fps=30.0)
    src.optimize_for_speed = optimize
    t0 = time.time()
    if not src.start():
        print(f"  {label:28} ✗ 起不来：{src.last_error}")
        return
    ready = time.time() - t0
    n0 = src.frames
    time.sleep(seconds)
    got = src.frames - n0
    fps = got / seconds
    alive = src.latest is not None
    print(f"  {label:28} 就绪 {ready:4.1f}s | {got} 帧/{seconds:.0f}s = {fps:5.1f} fps"
          f" | 单帧 {src.avg_ms:5.0f}ms | 有画面 {alive}")
    src.stop()


def main() -> int:
    print("抓帧速度实测（判据：≥8fps 才够让桌宠动起来顺）\n")
    measure((340, 460), label="340×460 原始")
    measure((240, 330), label="240×330 缩小")
    measure((180, 250), label="180×250 更小")
    measure((240, 330), optimize=False, label="240×330 不开加速")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
