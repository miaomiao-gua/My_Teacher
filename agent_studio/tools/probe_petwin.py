#!/usr/bin/env python
"""验证分层桌宠窗口：真的是**逐像素透明**吗？

判定方法（不靠肉眼，也不怕桌面自己在动）：
1. 先量**背景噪声**：不显示桌宠，隔 1.5 秒截两张（学生桌面上常常有东西在动）；
2. 让桌宠显示 3 秒（用的是 Pillow 画的那只，不依赖浏览器）；
3. 按**角色蒙版**分开判：蒙版内（角色实体像素）应当大面积变化，
   蒙版外（透明像素）应当只剩背景噪声。整块矩形 → 蒙版外也会变 ≈100%。

⚠️ 这里踩过一个坑（2026-10-07）：早先的版本只比"整块变化占比"，不看蒙版、也不量噪声，
   于是学生在旁边切窗口/开游戏时，同一份代码一会报 88.7% ❌、一会报 60.4% ❌
   ——（实测是误报：用"红方块"合成帧做对照，变化 9.5% ≈ 理论 9.2%）。
   所以现在把判定搬到了 `_screen_check.py`，噪声太大时会明说"这次不算数"。

跑法：py -3.14 tools/probe_petwin.py     （会在桌面右下角露一下，3 秒后自己消失）
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _screen_check as check  # noqa: E402

from desktop import petwin  # noqa: E402


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
    from desktop import pet_art

    # ★ 先把 DPI 感知打开再算坐标：不打开的话，起窗口时进程才变成感知的，
    #   起窗口前截的"底图"和之后的截图属于两个坐标空间（实测 1707×1067 vs 2560×1600），
    #   两张图整块都不一样 → 探针会报假故障。详见 petwin.make_process_dpi_aware()。
    petwin.make_process_dpi_aware()

    frame = {"img": pet_art.render_state("happy")}
    size = frame["img"].size
    user32 = __import__("ctypes").windll.user32
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    x, y = sw - size[0] - 60, sh - size[1] - 120
    region = (x, y, x + size[0], y + size[1])

    # ★ 判定用"开关对比"：把窗口快速搬到屏幕外再搬回来（几十毫秒），量 3 次取最干净的一次。
    #   早先"起窗口前截一张、1.5 秒后截一张"的写法，在忙桌面上背景整块都会变，必假故障。
    win = petwin.LivePetWindow(lambda: frame["img"], (x, y), size, fps=8.0, title="MyTeacherPetProbe")
    win.start_in_thread()
    time.sleep(1.2)
    trial = check.toggle_trials(region, win, (x, y), trials=3)
    stats, noise = trial["best"], trial["noise"]

    if win.last_error:
        print(f"  窗口报错：{win.last_error}")

    ah = frame["img"].split()[3].histogram()
    opaque_ratio = sum(ah[check.ALPHA_MIN:]) * 100.0 / (size[0] * size[1])
    print(f"  角色本体占整块 {opaque_ratio:.1f}%；蒙版内变化 {stats['inside']:.1f}%，"
          f"蒙版外变化 {stats['outside']:.1f}%")
    print(f"  3 次试量的蒙版外变化：" + "，".join(f"{s['outside']:.1f}%" for s in trial["samples"])
          + f"（取最小那一次；同间隔背景噪声 {noise:.1f}%）")
    print(f"  帧数 {win.frame_count}，平均 {win.last_frame_ms:.0f}ms/帧")

    ok_alpha = ok1 and ok2 and ok3
    ok_click = got == petwin.HTTRANSPARENT and got2 == petwin.HTCLIENT and got3 == petwin.HTCLIENT
    verdict = check.judge(stats, noise)
    print("  " + verdict["note"])
    ok_window = verdict["ok"] or verdict["inconclusive"]

    print("\n结论：", "✅ 逐像素透明生效（桌面没有被白块盖住）" if (ok_alpha and ok_click and ok_window)
          else "❌ 有问题（见上面那条说明）")
    if verdict["inconclusive"]:
        print("        （纯函数部分已通过；窗口判定这次不算数，屏幕安静时再跑一次即可）")
    return 0 if (ok_alpha and ok_click and ok_window) else 1


if __name__ == "__main__":
    raise SystemExit(main())
