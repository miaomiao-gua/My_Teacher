#!/usr/bin/env python
"""验收「真·透明 Live2D 桌宠」：让它真的出现在桌面上几秒，然后**用像素判定**。

判定三件事：
  ① 帧是从哪个模式来的（live2d / pillow 降级）+ 抓帧帧率
  ② 桌面上角色**蒙版内**变了、**蒙版外**只剩背景噪声（= 逐像素透明生效）
  ③ 画面里有角色的实体像素（不是空窗口）

⚠️ 判定法踩过的坑（2026-10-07）：
   · 只看"整块变化占比"，不量背景噪声、不分蒙版 → 同一份代码第一次报 53.1% ✓、
     第二次报 93.9% ✗。对照实验（红方块合成帧 9.5% ≈ 理论 9.2%）证明桌宠本身是透明的，
     误报来自**学生桌面自己在动**（他在旁边开游戏/切窗口）。
   · 更隐蔽的一条：进程的 DPI 感知是**起窗口时**才打开的（petwin.make_process_dpi_aware），
     而起窗口前进程不感知（1707×1067）、之后感知（2560×1600）—— 底图和对比图
     落在两个坐标空间里，整块画面都会"变化"。现在探针自己先把 DPI 感知打开再算区域。
   判定逻辑在 `_screen_check.py`：先量噪声、只看蒙版内外，噪声太大就明说"这次不算数"。

跑法：py -3.14 tools/probe_livepet.py [秒数]
      （默认 8 秒；这段时间桌面上会有一只透明的角色，之后自己消失）
      需要 127.0.0.1:5001 上的服务在跑。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _screen_check as check  # noqa: E402

from desktop import livepet, petwin  # noqa: E402

BASE = "http://127.0.0.1:5001"


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0

    data_dir = ROOT / "data"
    # ★ 先把 DPI 感知打开再算坐标：否则起窗口时进程才变成感知的，
    #   量到的"屏幕尺寸/区域"和截图会落在两个坐标空间（实测 1707×1067 vs 2560×1600），
    #   底图与对比图整块都不一样 → 假故障。详见 petwin.make_process_dpi_aware()。
    petwin.make_process_dpi_aware()
    user32 = __import__("ctypes").windll.user32
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    size = (340, 460)
    pos = (sw - size[0] - 80, sh - size[1] - 140)
    region = (pos[0], pos[1], pos[0] + size[0], pos[1] + size[1])

    pet = livepet.LivePet(BASE, data_dir, pos, size=size, fps=10.0)
    t = threading.Thread(target=pet.run, daemon=True, name="livepet-probe")
    t.start()

    # 无头浏览器启动 + Live2D 模型加载要 4~5 秒，**抓帧线程在那之后才开始出帧**。
    # 我第一版只等 4.5 秒就下结论，量到"3 帧"其实是还没开始跑（假故障）。
    time.sleep(11.0)
    print(f"形象来源：{pet.mode}（{pet.last_note}）")
    if pet.source:
        n0 = pet.source.frames
        time.sleep(3.0)
        rate = (pet.source.frames - n0) / 3.0
        print(f"抓帧实测：{rate:.1f} fps（平均 {pet.source.avg_ms:.0f}ms/帧）")

    time.sleep(max(0.0, seconds - 14.0))       # 参数 = 角色在桌面上露多久（判定放在最后做）

    # 判定期间清掉气泡：气泡会挂在角色**上方**、把窗口撑高，
    # 蒙版外的判定就变成"气泡那一块"了（会把好的一次误判成蒙版外大面积变化）。
    pet.bubble_text = ""
    pet.bubble_until = 0.0
    time.sleep(0.3)

    # ★ 判定用"开关对比"：把桌宠窗口快速搬到屏幕外再搬回来（几十毫秒），量 3 次取最干净的一次。
    #   早先隔 1.5~6 秒截两张的写法，在忙桌面上背景整块都会变 → 假故障（实测 53%↔94% 反复横跳）。
    frames = pet.window.frame_count if pet.window else 0
    if pet.window:
        trial = check.toggle_trials(region, pet.window, pos, trials=3)
        stats, noise = trial["best"], trial["noise"]
    else:
        trial, stats, noise = {"samples": []}, {}, 0.0

    print(f"背景噪声 {noise:.1f}%（同间隔 0.12 秒量；跟下面的蒙版判定可比）")
    print(f"蒙版内变化 {stats.get('inside', 0):.1f}%，蒙版外变化 {stats.get('outside', 0):.1f}%"
          f"（角色占整块 {stats.get('inside_ratio_of_screen', 0):.1f}%）；抓到 {frames} 帧")
    if trial.get("samples"):
        print("  3 次试量的蒙版外变化：" + "，".join(f"{s['outside']:.1f}%" for s in trial["samples"])
              + "（取最小那一次）")
    if pet.window:
        print(f"  合成 {pet.window.last_frame_ms:.0f}ms/帧，贴图 {getattr(pet.window, 'paint_ms', 0):.0f}ms/帧")
    if pet.source:
        print(f"  抓帧线程：{pet.source.frames} 帧，平均 {pet.source.avg_ms:.0f}ms，错误={pet.source.last_error or '无'}")

    verdict = check.judge(stats, noise) if stats else {"ok": False, "inconclusive": False,
                                                      "note": "✗ 窗口没起来"}
    ok_frames = frames > 3

    time.sleep(0.5)

    print("\n结论：")
    print(f"  形象 = {pet.mode}")
    print("  " + verdict["note"])
    print(f"  有画面 {'✓' if ok_frames else '✗ 一帧都没抓到'}")
    if verdict["inconclusive"]:
        print("  （这次窗口判定不算数，屏幕安静时再跑一次即可）")
    return 0 if (verdict["ok"] and ok_frames) else 1


if __name__ == "__main__":
    raise SystemExit(main())
