"""屏幕像素判定的公共部分（probe_petwin / probe_livepet 共用）。

**为什么需要这个文件**（2026-10-07 实测踩到的）：
最朴素的判定是"桌宠前后截两张屏幕，变化的像素应该只有角色那一小块"。
这在**学生自己的桌面上不成立** —— 他会在你测的时候切窗口、开游戏，
背景自己就在动。于是同一个探针、同一份代码：
    probe_livepet  第一次报 53.1% ✓，第二次报 93.9% ✗
    probe_petwin   第一次报 88.7% ✗，第二次报 60.4% ✗
而用"全透明帧/红方块帧"做对照实验，桌宠本体是**逐像素透明**的（红方块 9.5% ≈ 理论 9.2%）。
结论：误报来自桌面自己在动，不是桌宠的问题。

所以判定改成三步（这个文件就是这三步）：
1. 先量**背景噪声**：不显示桌宠，隔同样的时间截两张，看有多少像素自己变了；
2. 只看**角色蒙版内/外**的变化比例（蒙版 = 窗口当前那帧的 alpha 通道）：
   真透明 → 蒙版内变、蒙版外≈噪声；不透明方块 → 蒙版外也会大面积变（≈100%）；
3. 噪声太大（桌面正在剧烈变化）就说"这次不算数"，而不是武断报 ❌。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Tuple

THR = 16          # 亮度差 ≥16 才算"变了"（低于这个基本是压缩/抖动噪声）
ALPHA_MIN = 25    # alpha ≥25 算角色实体（petwin.hit_test 用的就是这个阈值）
NOISE_TOO_HIGH = 25.0   # 背景噪声超过这个百分比 → 失败时改判"本次不算数"
OUTSIDE_LIMIT = 20.0    # 开关间隔（0.4 秒）内，蒙版外允许变化的百分比上限


def grab(region: Tuple[int, int, int, int]) -> Any:
    """截一块屏幕（PIL RGB）。"""
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=region, all_screens=True).convert("RGB")


def noise_ratio(region: Tuple[int, int, int, int], seconds: float = 1.5,
                thr: int = THR) -> float:
    """背景噪声：不显示桌宠，隔 `seconds` 截两张，返回变化像素百分比。"""
    a = grab(region)
    time.sleep(seconds)
    b = grab(region)
    return changed_percent(a, b, thr=thr)


def changed_percent(before: Any, after: Any, thr: int = THR) -> float:
    from PIL import ImageChops

    if before.size != after.size:
        w = min(before.size[0], after.size[0])
        h = min(before.size[1], after.size[1])
        before, after = before.crop((0, 0, w, h)), after.crop((0, 0, w, h))
    hist = ImageChops.difference(before, after).convert("L").histogram()
    total = before.size[0] * before.size[1]
    return sum(hist[thr:]) * 100.0 / total if total else 0.0


def window_mask(win: Any) -> Tuple[bytes, int, int]:
    """窗口当前那帧的 alpha 蒙版（LivePetWindow 在 _compose 里存下来的）。"""
    return (getattr(win, "_alpha_bytes", b"") or b"",
            int(getattr(win, "_w", 0) or 0), int(getattr(win, "_h", 0) or 0))


def masked_change(before: Any, after: Any, alpha_bytes: bytes, w: int, h: int,
                  thr: int = THR, alpha_min: int = ALPHA_MIN) -> Dict[str, float]:
    """蒙版内/外分别有多少像素变了。

    返回 {inside, outside, inside_ratio_of_screen, mask_pixels}：
      inside  = 角色实体像素里变了多少（%）
      outside = 透明像素里变了多少（%）—— 真透明时应当 ≈ 背景噪声
    """
    from PIL import Image, ImageChops

    if not alpha_bytes or w <= 0 or h <= 0:
        return {"inside": 0.0, "outside": changed_percent(before, after, thr),
                "mask_pixels": 0, "inside_ratio_of_screen": 0.0}

    w = min(w, before.size[0], after.size[0])
    h = min(h, before.size[1], after.size[1])
    before = before.crop((0, 0, w, h))
    after = after.crop((0, 0, w, h))

    diff = ImageChops.difference(before, after).convert("L")
    changed = diff.point(lambda v: 255 if v >= thr else 0)
    mask = Image.frombytes("L", (w, h), alpha_bytes[: w * h]).point(
        lambda v: 255 if v >= alpha_min else 0)
    inside_map = ImageChops.multiply(changed, mask)

    n_mask = sum(mask.histogram()[255:])
    n_changed = sum(changed.histogram()[255:])
    n_changed_in = sum(inside_map.histogram()[255:])
    n_total = w * h
    n_out = n_total - n_mask
    return {
        "inside": (n_changed_in * 100.0 / n_mask) if n_mask else 0.0,
        "outside": ((n_changed - n_changed_in) * 100.0 / n_out) if n_out else 0.0,
        "mask_pixels": n_mask,
        "inside_ratio_of_screen": n_mask * 100.0 / n_total,
    }


def toggle_trials(region: Tuple[int, int, int, int], win: Any, pos: Tuple[int, int],
                  trials: int = 3, gap: float = 0.12, off_dx: int = 4000) -> Dict[str, Any]:
    """把窗口在"屏幕里/屏幕外"之间快速来回搬，量 N 次，取**最干净**的那次。

    为什么这么量（2026-10-07 实测后改的）：
    · 不用"关掉窗口再重建"：那条路要几百毫秒，忙桌面上背景早就变了；
      用 `SetWindowPos` 把窗口搬到屏幕外只要几十毫秒，背景来不及变（也不会留下错误的位置）。
    · 为什么要 N 次取最小：背景自己在动只可能**增加**变化量，不可能减少，
      所以"蒙版外变化最小的那次"最接近桌宠本身的贡献（其余几次被背景污染了）。

    返回 {"best": 最佳的 masked_change, "samples": [...], "noise": 同间隔背景噪声}。
    注意：桌宠会快速闪一下（搬走又搬回），这是判定用的开关，不是故障。
    """
    samples = []
    for _ in range(max(1, trials)):
        win.move(pos[0], pos[1])
        time.sleep(gap)
        on = grab(region)
        mask = window_mask(win)
        win.move(pos[0] + off_dx, pos[1])          # 搬到屏幕外 = 关掉
        time.sleep(gap)
        off = grab(region)
        samples.append(masked_change(on, off, *mask))
    win.move(pos[0] + off_dx, pos[1])
    time.sleep(gap)
    a = grab(region)
    time.sleep(gap)
    b = grab(region)
    noise = changed_percent(a, b)
    win.move(pos[0], pos[1])                       # 搬回来
    best = min(samples, key=lambda s: s["outside"]) if samples else {}
    return {"best": best, "samples": samples, "noise": noise}


def judge(stats: Dict[str, float], noise: float, *, inside_min: float = 50.0,
          outside_limit: float = OUTSIDE_LIMIT) -> Dict[str, Any]:
    """把上面量到的数字判成一句话。返回 {ok, note, inconclusive}。

    判定逻辑（2026-10-07 实测后定的）：
    · **主要看蒙版外的变化量**：开关只隔 0.4 秒，真透明 → 蒙版外几乎不变；
      不透明方块 → 蒙版外整块都变（≈100%）。这个数字本身就是"这段时间背景稳不稳"的证据，
      所以即使随后量到的背景噪声很大，只要蒙版外很小，就说明**测量那一刻**背景是稳的 → 算通过。
    · 噪调用只在**判失败**时才用来解释：蒙版外很大 + 背景本来就在剧烈变化 → "不算数，重跑"，
      而不是咬定桌宠不透明（早先就吃过这个亏：同一份代码被误报成 ❌ 好几次）。
    """
    if stats.get("mask_pixels", 0) == 0:
        return {"ok": False, "inconclusive": False,
                "note": "✗ 窗口里没有不透明像素（形象没画出来？）"}
    ok_in = stats["inside"] >= inside_min
    ok_out = stats["outside"] <= outside_limit
    parts = (f"角色蒙版内变化 {stats['inside']:.1f}%（要 ≥{inside_min:.0f}%），"
             f"蒙版外变化 {stats['outside']:.1f}%（要 ≤{outside_limit:.0f}%），"
             f"参考：同间隔背景噪声 {noise:.1f}%")
    if ok_in and ok_out:
        return {"ok": True, "inconclusive": False, "note": "✓ 逐像素透明生效：" + parts}
    if not ok_out and noise >= NOISE_TOO_HIGH:
        return {"ok": True, "inconclusive": True,
                "note": f"⚠️ 本次不算数：蒙版外也在变，而且背景噪声有 {noise:.1f}%（桌面正在剧烈变化），"
                        f"分不清是桌宠还是背景。让屏幕静一下再跑一次。"}
    if not ok_out:
        return {"ok": False, "inconclusive": False,
                "note": "✗ 蒙版外也大面积变化 —— 窗口像个不透明方块（不是逐像素透明）：" + parts}
    return {"ok": False, "inconclusive": False,
            "note": "✗ 角色没出现在屏幕上（蒙版内几乎没变）：" + parts}
