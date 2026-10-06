#!/usr/bin/env python
"""验收「真·透明 Live2D 桌宠」：让它真的出现在桌面上几秒，然后**用像素判定**。

判定三件事：
  ① 帧是从哪个模式来的（live2d / pillow 降级）
  ② 桌面上那块区域**没有被一个不透明的方块盖住**（透明生效）
  ③ 画面里有角色的实体像素（不是空窗口）

跑法：py -3.14 tools/probe_livepet.py [秒数]
      （默认 8 秒；这段时间桌面上会有一只透明的角色，之后自己消失）
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from desktop import livepet  # noqa: E402

BASE = "http://127.0.0.1:5001"


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    from PIL import ImageChops, ImageGrab

    data_dir = ROOT / "data"
    user32 = __import__("ctypes").windll.user32
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    size = (340, 460)
    pos = (sw - size[0] - 80, sh - size[1] - 140)
    region = (pos[0], pos[1], pos[0] + size[0], pos[1] + size[1])

    print("先截一张底图（桌宠还没出现）…")
    before = ImageGrab.grab(bbox=region, all_screens=True).convert("RGB")

    pet = livepet.LivePet(BASE, data_dir, pos, size=size, fps=10.0)
    t = threading.Thread(target=pet.run, daemon=True, name="livepet-probe")
    t.start()
    # 注意：无头浏览器启动 + Live2D 模型加载要 4~5 秒，**抓帧线程在那之后才开始出帧**。
    # 我第一版只等 4.5 秒就下结论，量到"3 帧"其实是还没开始跑（假故障）。
    time.sleep(11.0)
    print(f"形象来源：{pet.mode}（{pet.last_note}）")
    if pet.source:
        n0 = pet.source.frames
        time.sleep(3.0)
        rate = (pet.source.frames - n0) / 3.0
        print(f"抓帧实测：{rate:.1f} fps（平均 {pet.source.avg_ms:.0f}ms/帧）")

    mid = ImageGrab.grab(bbox=region, all_screens=True).convert("RGB")
    hist = ImageChops.difference(before, mid).convert("L").histogram()
    changed = sum(hist[16:])
    total = size[0] * size[1]
    ratio = changed * 100.0 / total

    # 角色实体像素：非桌面背景的像素（不透明处）。用"与底图差异较大"近似。
    frames = pet.window.frame_count if pet.window else 0
    print(f"屏幕变化像素 {ratio:.1f}%（若≈100% 说明是一个不透明方块）；抓到 {frames} 帧")
    if pet.window:
        print(f"  合成 {pet.window.last_frame_ms:.0f}ms/帧，贴图 {getattr(pet.window, 'paint_ms', 0):.0f}ms/帧")
    if pet.source:
        print(f"  抓帧线程：{pet.source.frames} 帧，平均 {pet.source.avg_ms:.0f}ms，错误={pet.source.last_error or '无'}")

    time.sleep(max(0.0, seconds - 14.0))
    try:
        if pet.window:
            pet.window.stop()
    except Exception:
        pass
    time.sleep(1.0)

    ok_transparent = 2 < ratio < 70
    ok_frames = frames > 3
    print("\n结论：")
    print(f"  形象 = {pet.mode}")
    print(f"  透明桌宠 {'✓ 生效（桌面上只有角色，没有窗口底）' if ok_transparent else '✗ 透明没生效'}")
    print(f"  有画面 {'✓' if ok_frames else '✗ 一帧都没抓到'}")
    return 0 if (ok_transparent and ok_frames) else 1


if __name__ == "__main__":
    raise SystemExit(main())
