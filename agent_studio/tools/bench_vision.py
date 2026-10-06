#!/usr/bin/env python
"""比一比几种"看图"配置的速度与质量，好挑一个默认值。

为什么必须实测：本机 qwen2.5vl:7b 读一张 1400px 的截图要 40 秒 ——
"按快捷键马上出答案"的体验就废了。云端小模型（Qwen3-VL-8B）多半 3~6 秒，
但要花钱、要联网。到底用哪个，只能量。

跑法：py -3.14 tools/bench_vision.py
"""

from __future__ import annotations

import asyncio
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import vision  # noqa: E402

SECRET = "47"


def image(max_side: int) -> str:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1600, 900), "white")     # 接近真实截图的尺寸
    d = ImageDraw.Draw(img)
    d.ellipse((80, 80, 420, 420), fill=(220, 30, 30))
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 90)
        small = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 44)
    except Exception:
        font = small = None
    d.text((520, 200), f"本题答案 = {SECRET}", fill="black", font=font)
    d.text((520, 380), "工具栏里有：移动 选区 套索 魔棒", fill="black", font=small)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64, meta = vision.prepare_image(buf.getvalue(), max_side=max_side)
    return b64, meta


async def one(label: str, provider: str, model: str, max_side: int) -> None:
    from kernel.config import LLMConfig, load_app_config

    app = load_app_config()
    b64, meta = image(max_side)
    if provider == "ollama":
        cfg = LLMConfig(provider="ollama", model=model,
                        base_url=(app.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/"),
                        temperature=0.2, num_predict=200, think=False)
    else:
        cfg = LLMConfig(provider="openai", model=model,
                        base_url=(app.get("chat_base_url") or "https://api.siliconflow.cn/v1").rstrip("/"),
                        api_key=str(app.get("chat_api_key") or ""), temperature=0.2, num_predict=200)
    t0 = time.time()
    try:
        from kernel.llm import build_llm

        llm = build_llm(cfg)
        got = []
        async for ch in llm.stream([
            {"role": "system", "content": "你能看到用户屏幕。只回答要点，不超过两句。"},
            {"role": "user", "content": "图里写的本题答案是多少？工具栏里有哪些工具？",
             "images": [b64]},
        ]):
            if ch.kind == "text" and ch.text:
                got.append(ch.text)
        answer = "".join(got).strip().replace("\n", " ")
        ok = SECRET in answer
        print(f"  {label:34} {time.time() - t0:6.1f}s  {'✓' if ok else '✗'}  {answer[:90]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  {label:34} {time.time() - t0:6.1f}s  ✗  {type(exc).__name__}: {str(exc)[:90]}")


async def main() -> int:
    print("同一句话、同一张图（1600×900 白底 + 红圆 + 两行字）\n")
    await one("本机 qwen2.5vl:7b @1400", "ollama", "qwen2.5vl:7b", 1400)
    await one("本机 qwen2.5vl:7b @1024", "ollama", "qwen2.5vl:7b", 1024)
    await one("云端 Qwen3-VL-8B-Instruct @1400", "openai", "Qwen/Qwen3-VL-8B-Instruct", 1400)
    await one("云端 Qwen3-VL-30B-A3B @1400", "openai", "Qwen/Qwen3-VL-30B-A3B-Instruct", 1400)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
