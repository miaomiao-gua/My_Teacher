#!/usr/bin/env python
"""验证分层桌宠窗口：真的是**逐像素透明**吗？

判定方法（不靠肉眼）：
1. 先把桌宠要占的那块屏幕区域截下来（before）；
2. 让桌宠显示 3 秒（用的是 Pillow 画的那只，不依赖浏览器）；
3. 再截同一块（after）；
4. 对比：**变化的像素应该只有角色本体那一小块（形态像人），
   而不是整整一个矩形**。如果是矩形 → 说明窗口是不透明的白块（失败）。

跑法：py -3.14 tools/probe_petwin.py     （会在桌面右下角露一下，3 秒后自己消失）
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from desktop import core, petwin  # noqa: E402


def main() -> int:
    print("[1] 纯函数：预乘 alpha")
    px = bytes((200, 100, 50, 255)) + bytes((200, 100, 50, 128)) + bytes((200, 100, 50, 0))
    out = petwin.premultiply(px)
    ok1 = out[0:4] == bytes((200, 100, 50, 255))
    ok2 = abs(out[4] - 100) <= 1 and abs(out[5] - 50) <= 1 and out[7] == 128
    ok3 = out[8:12] == b"\x00\x00\x00\x00"
    print(f"  不透明原样保留 {'✓' if ok1 else '✗'}；半透明按 a/255 压暗 {'✓' if ok2 else '✗'}"
          f"；全透明归零 {'✓' if ok3 else '✗'}")

    print("[2] 纯函数：点得穿")
    alphas = {(5, 5): 0, (10, 10): 255, (20, 20): 30}
    got = petwin.hit_test(lambda x, y: alphas.get((x, y), 0), 5, 5)
    got2 = petwin.hit_test(lambda x, y: alphas.get((x, y), 0), 10, 10)
    got3 = petwin.hit_test(lambda x, y: alphas.get((x, y), 0), 20, 20)
    print(f"  透明处点得穿 {'✓' if got == petwin.HTTRANSPARENT else '✗'}；"
          f"角色上能抓 {'✓' if got2 == petwin.HTCLIENT else '✗'}；"
          f"边缘(30)算角色 {'✓' if got3 == petwin.HTCLIENT else '✗'}")

    print("[3] 真窗口（会露 3 秒）")
    import threading

    from PIL import ImageGrab

    from desktop import pet_art

    frame = {"img": pet_art.render_state("happy")}
    size = frame["img"].size
    user32 = __import__("ctypes").windll.user32
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    x, y = sw - size[0] - 60, sh - size[1] - 120
    region = (x, y, x + size[0], y + size[1])
    before = ImageGrab.grab(bbox=region, all_screens=True).convert("RGB")

    win = petwin.LivePetWindow(lambda: frame["img"], (x, y), size, fps=8.0, title="MyTeacherPetProbe")
    win.start_in_thread()
    time.sleep(1.5)
    after = ImageGrab.grab(bbox=region, all_screens=True).convert("RGB")
    win.stop()
    time.sleep(0.6)

    if win.last_error:
        print(f"  窗口报错：{win.last_error}")

    from PIL import ImageChops

    diff = ImageChops.difference(before, after).convert("L")
    # 用直方图数，比逐像素 getdata 快得多（而且不会触发 Pillow 的弃用告警）
    hist = diff.histogram()
    changed = sum(hist[13:])
    total = size[0] * size[1]
    ratio = changed * 100.0 / total
    # 角色本体的不透明占比：拿那一帧自己的 alpha 算（应当和 changed 接近）
    ah = frame["img"].split()[3].histogram()
    opaque = sum(ah[25:])
    opaque_ratio = opaque * 100.0 / total
    print(f"  屏幕变化的像素 {ratio:.1f}% ；角色本体占 {opaque_ratio:.1f}%")
    print(f"  帧数 {win.frame_count}，平均 {win.last_frame_ms:.0f}ms/帧")

    ok_alpha = ok1 and ok2 and ok3
    ok_click = got == petwin.HTTRANSPARENT and got2 == petwin.HTCLIENT and got3 == petwin.HTCLIENT
    # 透明判定：变化的像素应接近"角色本体"的占比；如果是整块矩形，ratio 会接近 100%
    ok_window = ratio < 60 and ratio > 3
    print("\n结论：", "✅ 逐像素透明生效（桌面没有被白块盖住）" if (ok_alpha and ok_click and ok_window)
          else f"❌ 有问题（变化占比 {ratio:.1f}%，若≈100% 说明窗口不透明）")
    return 0 if (ok_alpha and ok_click and ok_window) else 1


if __name__ == "__main__":
    raise SystemExit(main())
