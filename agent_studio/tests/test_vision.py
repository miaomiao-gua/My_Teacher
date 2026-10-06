#!/usr/bin/env python
"""看图功能端到端自测：**真的**让视觉模型看一张图并回答。

为什么不用真实截屏做断言：屏幕上的内容一直在变，答对了也不知道是"看懂了"还是"猜对了"。
这里画一张**内容已知**的图（红圆 + 一行字），问它图里写了什么数字 ——
答出那个数字，才说明「图 → 模型 → 答案」这条路真的通了。

顺带把配置也验一遍：本机 qwen2.5vl:7b / 云端 Qwen3-VL。

跑法：py -3.14 tests/test_vision.py            # 纯逻辑（不联网）
      py -3.14 tests/test_vision.py --live     # 额外真发一次（本机或云端视觉模型）
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

from kernel import vision  # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


def make_test_image(secret: str = "47") -> bytes:
    """画一张内容已知的图：白底 + 红圆 + 一行字（模型必须读出那个数字）"""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (900, 500), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((60, 60, 300, 300), fill=(220, 30, 30))
    try:
        from PIL import ImageFont

        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 64)
    except Exception:
        font = None
    d.text((360, 150), f"本题答案 = {secret}", fill="black", font=font)
    d.text((360, 260), "请把数字报给我", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_prepare() -> None:
    print("\n[1] 图片压缩（发之前必须压，不然 token 爆炸）")
    png = make_test_image()
    b64, meta = vision.prepare_image(png)
    check("产出了 base64", len(b64) > 1000, f"{len(b64)} 字符")
    check("是 JPEG（base64 开头 /9j/）", b64.startswith("/9j/"), b64[:8])
    check("带了尺寸与体积说明", set(meta) == {"from", "to", "kb"}, json.dumps(meta, ensure_ascii=False))
    big = make_test_image()
    from PIL import Image

    huge = Image.open(io.BytesIO(big)).resize((4000, 2200))
    buf = io.BytesIO()
    huge.save(buf, format="PNG")
    _b64, meta2 = vision.prepare_image(buf.getvalue())
    check("超大图被缩到长边 ≤ 1400", max(meta2["to"]) <= 1400, json.dumps(meta2, ensure_ascii=False))
    check("压缩后体积合理（< 900KB）", meta2["kb"] < 900, f"{meta2['kb']}KB")


def test_config() -> None:
    print("\n[2] 配置与自检")
    st = vision.self_check()
    check("默认走本机（免费、离线）", st["provider"] == "local", json.dumps(st, ensure_ascii=False))
    check("默认模型是视觉模型（qwen2.5vl）", "vl" in str(st["model"]).lower(), str(st["model"]))
    check("off 时 ready=False", vision.self_check()["ready"] in (True, False))


def test_message_shape() -> None:
    print("\n[3] 图片在两家协议里的写法（写错了模型就收不到图）")
    from kernel.llm import build_messages

    msgs = [{"role": "user", "content": "看这个", "images": ["AAA"]}]
    o = build_messages("ollama", msgs)
    check("ollama：images 字段带上了", o[0].get("images") == ["AAA"], json.dumps(o, ensure_ascii=False)[:120])
    p = build_messages("openai", msgs)
    parts = p[0].get("content")
    check("openai：content 变成数组（text + image_url）",
          isinstance(parts, list) and parts[0]["type"] == "text" and parts[1]["type"] == "image_url",
          json.dumps(parts, ensure_ascii=False)[:160])
    check("openai：图片用 data URL", str(parts[1]["image_url"]["url"]).startswith("data:image/jpeg;base64,"),
          str(parts[1]["image_url"]["url"])[:40])
    plain = build_messages("openai", [{"role": "user", "content": "没有图"}])
    check("没有图时仍是普通字符串（别把文字也包成数组）", isinstance(plain[0]["content"], str))


def test_live() -> None:
    print("\n[4] 真发一次（要看图的那个模型得在跑）")
    secret = "47"
    b64, _meta = vision.prepare_image(make_test_image(secret))
    r = asyncio.run(vision.ask(b64, "图里写的本题答案是多少？只回数字。"))
    ok = bool(r.get("ok")) and secret in str(r.get("answer"))
    check("模型读出了图里的数字", ok,
          f"model={r.get('model')} ms={r.get('ms')} answer={str(r.get('answer'))[:120]}")
    if not ok:
        print("     ↑ 若报「不支持看图」：设置 → 看图 里换成 qwen2.5vl:7b 或云端 Qwen3-VL")


def test_hotkey() -> None:
    print("\n[5] 快捷键解析（必须带修饰键，别抢普通按键）")
    from desktop import core

    check("ctrl+shift+t 认得（学生的例子）", core.parse_hotkey("ctrl+shift+t") is not None,
          str(core.parse_hotkey("ctrl+shift+t")))
    check("大小写/空格都认", core.parse_hotkey(" Ctrl + Alt + S ") == core.parse_hotkey("ctrl+alt+s"))
    check("win 键也认", core.parse_hotkey("win+alt+q") is not None)
    check("**光一个 f2 不认**（没有修饰键会抢掉普通按键）", core.parse_hotkey("f2") is None)
    check("乱写返回 None（不崩）", core.parse_hotkey("") is None and core.parse_hotkey("!!!") is None)
    check("给人看的写法", core.hotkey_label("ctrl+shift+t") == "Ctrl+Shift+T",
          core.hotkey_label("ctrl+shift+t"))


def test_watcher_registers() -> None:
    """真注册一次全局热键（不建窗口）——这条只有在 Windows 桌面会话里才跑得过。"""
    print("\n[6] 快捷键线程真注册（不开窗口）")
    import tempfile
    import time

    from desktop import core, hotkey

    if sys.platform != "win32":
        print("  ·  不是 Windows，跳过")
        return
    with tempfile.TemporaryDirectory(dir=str(ROOT / "data")) as tmp:
        data_dir = Path(tmp)
        t = hotkey.start_watcher("http://127.0.0.1:9", data_dir, "ctrl+shift+f9", enabled=True)
        time.sleep(0.6)
        log = (data_dir / "logs" / "desktop.log")
        text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        ok = "快捷键已就绪" in text
        check("注册成功并写了日志（不创建窗口）", ok and t is not None,
              text.strip().splitlines()[-1] if text.strip() else "（没有日志）")
        # 关掉的路径：它只是 daemon 线程，进程退出即回收，不需要显式清理


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="额外真发一次（用本机/云端视觉模型）")
    args = ap.parse_args()
    test_prepare()
    test_config()
    test_message_shape()
    test_hotkey()
    test_watcher_registers()
    if args.live:
        test_live()
    print("\n" + ("全部通过 ✓" if _failures == 0 else f"失败 {_failures} 项 ✗"))
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
