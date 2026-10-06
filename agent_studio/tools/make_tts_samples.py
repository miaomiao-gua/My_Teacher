#!/usr/bin/env python
"""把同一句话用**几个嗓子**各合成一遍，存成音频文件，让人亲耳挑。

为什么不只在界面里放「试听」：界面一次只能听一个，来回切很烦；
挑嗓子这件事**必须并排听**才做得出来（快、慢、男声、女声、自然度）。
生成的 wav/mp3 直接双击就能放。

用法：py -3.14 tools/make_tts_samples.py
     （不传参数 = Windows 本机嗓子 + 硅基流动常用的几个云端音色；
       想只试某一个：--only anna）
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import tts  # noqa: E402

OUT = ROOT / "data" / "tts_samples"
TEXT = "你好呀，我是艾琳老师。这道题我们分三步看：先看条件，再找等量关系，最后代进公式算一遍。"

# 硅基流动 CosyVoice2 的常见音色（名字后面是给学生的说明）
CLOUD_VOICES = [
    ("alex", "男声，稳"),
    ("anna", "女声，温柔"),
    ("bella", "女声，年轻"),
    ("charles", "男声，偏低"),
]


async def make_cloud(voice: str, label: str, key: str, base: str, model: str) -> Path | None:
    import httpx

    payload = {"model": model, "input": TEXT, "voice": f"{model}:{voice}", "response_format": "mp3"}
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(base.rstrip("/") + "/audio/speech", json=payload,
                                  headers={"Authorization": f"Bearer {key}"})
        if r.status_code != 200 or len(r.content) < 500:
            print(f"  ✗ 云端 {voice}: HTTP {r.status_code} {r.text[:120]}")
            return None
        path = OUT / f"云端_{voice}_{label}.mp3"
        path.write_bytes(r.content)
        print(f"  ✓ {path.name}  ({len(r.content) // 1024} KB)")
        return path
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 云端 {voice}: {type(exc).__name__}: {exc}")
        return None


def make_windows(idx: int, name: str) -> Path | None:
    try:
        cfg = {"provider": "windows", "windows_voice": idx, "speed": 1.0, "volume": 100}
        data = tts._windows_wav(TEXT, cfg)
        path = OUT / f"本机_{idx}_{name}.wav"
        path.write_bytes(data)
        print(f"  ✓ {path.name}  ({len(data) // 1024} KB)")
        return path
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 本机 {name}: {type(exc).__name__}: {exc}")
        return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只合成名字里带这个字的")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    print(f"输出目录：{OUT}\n同一句话：{TEXT[:24]}…\n")

    print("【Windows 本机嗓子】离线、免费、不用 key：")
    for i, v in enumerate(tts.windows_voices()):
        name = str(v["name"]).split(" - ")[0].replace(" ", "")
        if args.only and args.only.lower() not in name.lower():
            continue
        make_windows(i, name)

    from kernel.config import load_app_config

    cfg = load_app_config()
    key = str(cfg.get("chat_api_key") or cfg.get("cloud_api_key") or "")
    base = str(cfg.get("chat_base_url") or "https://api.siliconflow.cn/v1")
    model = "FunAudioLLM/CosyVoice2-0.5B"
    if key:
        print(f"\n【云端音色】走你的 key（{key[:6]}…），更自然但会花钱、要联网：")
        for voice, label in CLOUD_VOICES:
            if args.only and args.only.lower() not in voice and args.only.lower() not in label:
                continue
            await make_cloud(voice, label, key, base, model)
    else:
        print("\n（没配云端 key，跳过云端音色）")

    files = sorted(OUT.glob("*"))
    print(f"\n共 {len(files)} 个文件，双击就能听：")
    for f in files:
        print("  " + str(f))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
