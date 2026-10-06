#!/usr/bin/env python
"""验证「抓帧」这条路能不能用：无头 Edge 渲染桌宠页 + 抓带 alpha 的 PNG。

为什么先单独验：整条"真透明桌宠"都押在这一点上 ——
`Emulation.setDefaultBackgroundColorOverride` + `Page.captureScreenshot(png)`
到底给不给 alpha。给，就能做真透明；不给，只能退回色键（边缘有色边）。

跑法：py -3.14 tools/probe_frames.py
输出：data/frames_probe.png（抓到的第一帧）+ 透明的像素占比等数字。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from desktop import frames  # noqa: E402

BASE = "http://127.0.0.1:5001"


def main() -> int:
    url = f"{BASE}/agent/pet?frames=1"
    print(f"起无头 Edge 渲染：{url}")
    src = frames.FrameSource(url, size=(320, 430), profile=ROOT / "data" / "browser_frames")
    t0 = time.time()
    if not src.start():
        print(f"  ✗ 起不来：{src.last_error}")
        return 1
    print(f"  ✓ 就绪（{time.time() - t0:.1f}s）")

    got = []
    try:
        # 连抓几帧：第一帧可能还没渲染完
        for i in range(6):
            img = src.grab()
            if img is not None:
                got.append(img)
            time.sleep(0.5)
    finally:
        src.stop()

    if not got:
        print(f"  ✗ 一帧都没抓到：{src.last_error}")
        return 1

    last = got[-1]
    w, h = last.size
    alpha = last.split()[3]
    hist = alpha.histogram()
    total = w * h
    transparent = sum(hist[:8])            # alpha < 8 ≈ 全透明
    opaque = sum(hist[200:])               # alpha ≥ 200 ≈ 实体（角色本体）
    print(f"  帧尺寸 {w}×{h}，共抓 {len(got)} 帧，平均 {src.avg_ms:.0f}ms/帧")
    print(f"  透明像素 {transparent * 100.0 / total:.1f}%  实体像素 {opaque * 100.0 / total:.1f}%")
    out = ROOT / "data" / "frames_probe.png"
    last.save(out)
    print(f"  已存：{out}")

    ok = transparent > total * 0.3 and opaque > 200
    print("\n结论：", "✅ 有真透明，可以走真·透明桌宠（B 方案）" if ok
          else "❌ 没拿到 alpha 或角色没画出来（要退回色键或排查 WebGL）")
    if not ok:
        print("  排查提示：opaque≈0 → WebGL 没画出来（试 --use-angle=swiftshader）；"
              "transparent≈0 → 透明底色没设上")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
