"""桌宠形象：用 Pillow **现场画**（不往仓库里塞图片二进制，配色也能随时调）。

为什么要现场画而不是放 PNG
--------------------------
1. 仓库里只有代码，改配色/改表情就是改几个数字，不用开画图软件；
2. 状态（眨眼/开心/打盹/思考/警觉）本来就是同一只角色的不同表情，
   用参数画出来天然一致，不会出现「换表情时比例变了」。

透明窗口的坑（已实测）
----------------------
Tk 的透明靠 `-transparentcolor`（把某个色键当透明），它**不支持半透明像素**：
把抗锯齿的图直接贴上去，边缘会跟色键混出一圈**品红色的边**（探针截图里看得很清楚）。
`flatten_for_key()` 就是解决办法：把「半透明」的那一圈像素直接压成轮廓色，
于是边缘变成一条干净的深色描边 —— 观感上跟正常卡通描边一样，且完全看不到色边。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# 色键：正常配色里绝不会出现的品红
KEY = "#ff00ff"

# 配色（改这里就能换一只）
PALETTE = {
    "body": "#f6e7c6",
    "body_dark": "#e8d3a8",
    "belly": "#fff8e8",
    "line": "#7a5a2e",
    "eye": "#3a3128",
    "blush": "#f0a3a3",
    "beak": "#f0a93b",
    "cap": "#3a4a7a",
    "cap_dark": "#2c3860",
    "tassel": "#e0b23a",
}

STATES = ["idle", "blink", "happy", "think", "sleepy", "alert"]

W, H = 128, 148          # 逻辑尺寸（不含投影余量）
SS = 4                   # 超采样倍数：先画 4 倍再缩，边缘才够细


def _rgb(hexstr: str) -> tuple[int, int, int]:
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def render_state(state: str = "idle", palette: dict | None = None,
                 size: tuple[int, int] = (W, H), bob: int = 0) -> Image.Image:
    """画一只角色的某个表情，返回 RGBA。bob = 上下浮动像素（做小动作）。"""
    pal = dict(PALETTE)
    pal.update(palette or {})
    w, h = size
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    def S(v: float) -> float:
        return v * SS

    oy = S(bob)
    line_w = max(2, int(S(2.4)))
    lc = _rgb(pal["line"])

    # ---- 耳朵（先画，压在身体下面）----
    for ex in (S(30), S(72)):
        d.ellipse((ex, S(16) + oy, ex + S(26), S(50) + oy), fill=_rgb(pal["body"]), outline=lc, width=line_w)
        d.ellipse((ex + S(7), S(26) + oy, ex + S(19), S(44) + oy), fill=_rgb(pal["blush"]))

    # ---- 身体（蛋形）----
    d.ellipse((S(12), S(32) + oy, S(116), S(140) + oy), fill=_rgb(pal["body"]), outline=lc, width=line_w)
    # 肚皮
    d.ellipse((S(34), S(84) + oy, S(94), S(132) + oy), fill=_rgb(pal["belly"]))
    # 身体下半的暗部，让它有点体积感
    d.arc((S(16), S(40) + oy, S(112), S(144) + oy), start=20, end=160,
          fill=_rgb(pal["body_dark"]), width=line_w)

    # ---- 手（开心时举起来）----
    arm_y = S(96) if state != "happy" else S(74)
    for ax in (S(14), S(102)):
        d.ellipse((ax, arm_y + oy, ax + S(16), arm_y + S(18) + oy),
                  fill=_rgb(pal["body"]), outline=lc, width=max(2, int(S(2))))

    # ---- 脚 ----
    for fx in (S(40), S(72)):
        d.ellipse((fx, S(132) + oy, fx + S(18), S(144) + oy), fill=_rgb(pal["line"]))

    # ---- 眼睛 ----
    eye_l, eye_r = S(38), S(64)
    eye_top, eye_bot = S(58), S(84)
    if state == "blink" or state == "sleepy":
        # 闭眼：一条小弧
        bend = S(4) if state == "blink" else S(-4)
        for ex in (eye_l, eye_r):
            d.arc((ex, eye_top + bend + oy, ex + S(26), eye_bot + bend + oy),
                  start=200 if state == "blink" else 20,
                  end=340 if state == "blink" else 160,
                  fill=lc, width=max(2, int(S(2.6))))
    elif state == "happy":
        # 笑眼：^ ^
        for ex in (eye_l, eye_r):
            d.arc((ex, eye_top + S(6) + oy, ex + S(26), eye_bot + S(6) + oy),
                  start=190, end=350, fill=lc, width=max(2, int(S(2.6))))
    else:
        grow = S(3) if state == "alert" else 0
        for ex in (eye_l, eye_r):
            d.ellipse((ex - grow, eye_top - grow + oy, ex + S(24) + grow, eye_bot + grow + oy),
                      fill=_rgb(pal["eye"]))
            # 高光
            d.ellipse((ex + S(4) - grow, eye_top + S(5) - grow + oy,
                       ex + S(11), eye_top + S(12) + oy), fill=(255, 255, 255, 235))
            d.ellipse((ex + S(14), eye_top + S(14) + oy, ex + S(18), eye_top + S(18) + oy),
                      fill=(255, 255, 255, 170))
        if state == "think":
            # 眼睛往上看 = 在想事情
            for ex in (eye_l, eye_r):
                d.ellipse((ex + S(6), eye_top + oy, ex + S(18), eye_top + S(7) + oy),
                          fill=_rgb(pal["body"]))

    # ---- 腮红 ----
    for bx in (S(26), S(86)):
        d.ellipse((bx, S(86) + oy, bx + S(16), S(96) + oy), fill=_rgb(pal["blush"]))

    # ---- 嘴 ----
    if state == "happy":
        d.pieslice((S(52), S(86) + oy, S(80), S(110) + oy), start=0, end=180,
                   fill=_rgb(pal["beak"]), outline=lc, width=max(2, int(S(2))))
    elif state == "alert":
        d.ellipse((S(58), S(92) + oy, S(74), S(106) + oy), fill=_rgb(pal["beak"]), outline=lc,
                  width=max(2, int(S(2))))
    else:
        d.polygon([(S(60), S(90) + oy), (S(72), S(90) + oy), (S(66), S(99) + oy)],
                  fill=_rgb(pal["beak"]), outline=lc)

    # ---- 学士帽（点题：这是「老师」的桌宠）----
    cap_y = S(10) + oy
    d.polygon([(S(64), cap_y), (S(112), cap_y + S(12)), (S(64), cap_y + S(24)), (S(16), cap_y + S(12))],
              fill=_rgb(pal["cap"]), outline=lc)
    d.polygon([(S(52), cap_y + S(16)), (S(76), cap_y + S(16)), (S(76), cap_y + S(28)), (S(52), cap_y + S(28))],
              fill=_rgb(pal["cap_dark"]), outline=lc)
    d.line((S(108), cap_y + S(14), S(112), cap_y + S(34)), fill=_rgb(pal["tassel"]), width=max(2, int(S(2))))
    d.ellipse((S(107), cap_y + S(32), S(117), cap_y + S(42)), fill=_rgb(pal["tassel"]), outline=lc)

    # ---- 状态标记 ----
    if state == "sleepy":
        # 三个由小到大的「Z」：三笔（上横、斜、下横）
        for zx, zy, zs in ((94, 34, 9), (104, 22, 12), (116, 8, 15)):
            top_l, top_r = S(zx), S(zx + zs * 0.8)
            d.line((top_l, S(zy), top_r, S(zy)), fill=_rgb(pal["line"]), width=max(2, int(S(2))))
            d.line((top_r, S(zy), top_l, S(zy + zs)), fill=_rgb(pal["line"]), width=max(2, int(S(2))))
            d.line((top_l, S(zy + zs), top_r, S(zy + zs)), fill=_rgb(pal["line"]), width=max(2, int(S(2))))
    if state == "alert":
        d.line((S(112), S(24), S(112), S(44)), fill="#d83b01", width=max(3, int(S(4))))
        d.ellipse((S(108), S(50), S(116), S(58)), fill="#d83b01")

    return img.resize((w, h), Image.LANCZOS)


def flatten_for_key(im: Image.Image, key: str = KEY, edge_color: str | None = None) -> Image.Image:
    """把抗锯齿边缘压成轮廓色，供 `-transparentcolor` 使用（否则会有一圈品红边）。

    edge_color 默认用配色里的描边色（`PALETTE['line']`）：
    这比「从像素里猜最深的颜色」更稳，也不会用到 Pillow 里已弃用的 getdata()。
    """
    if im.mode != "RGBA":
        im = im.convert("RGBA")
    ec = _rgb(edge_color or PALETTE["line"])
    alpha = im.getchannel("A")
    rgb = Image.new("RGB", im.size, _rgb(key))
    rgb.paste(im.convert("RGB"), (0, 0), im)

    out = rgb.load()
    av = alpha.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            a = av[x, y]
            if 0 < a < 255:            # 半透明 = 抗锯齿的那一圈 → 压成描边色
                out[x, y] = ec
    return rgb


def contact_sheet(out_path: Path, key: str = KEY) -> Path:
    """导出一张对照图：每个状态下「原始 RGBA（贴灰底）」和「压边后（贴色键）」各一份。

    这张图是给**人眼**看的：确认形象不难看、且压边之后没有色边。
    """
    cols = len(STATES)
    cell_w, cell_h = W + 16, H + 34
    sheet = Image.new("RGB", (cell_w * cols, cell_h * 2 + 8), (245, 245, 245))
    d = ImageDraw.Draw(sheet)
    for i, st in enumerate(STATES):
        raw = render_state(st)
        flat = flatten_for_key(raw, key)
        x = i * cell_w + 8
        # 第一行：灰底 + 原始 RGBA（看形状）
        gray = Image.new("RGBA", raw.size, (150, 150, 150, 255))
        sheet.paste(Image.alpha_composite(gray, raw).convert("RGB"), (x, 22))
        # 第二行：色键底 + 压边版（看有没有色边）
        sheet.paste(flat, (x, cell_h + 22))
        d.text((x, 6), st, fill=(30, 30, 30))
        d.text((x, cell_h + 6), st + " (flattened)", fill=(30, 30, 30))
    sheet.save(out_path)
    return out_path


def main() -> int:
    ap = argparse.ArgumentParser(description="导出桌宠形象对照图")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "_probe_shots" / "pet_states.png"))
    ap.add_argument("--single", help="只导出某个状态（原始 RGBA）")
    ap.add_argument("--bob", type=int, default=0)
    args = ap.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.single:
        render_state(args.single, bob=args.bob).save(out)
    else:
        contact_sheet(out)
    print(f"已导出：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
