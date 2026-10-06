#!/usr/bin/env python
"""试一下云端 TTS 能不能用（用学生已经配好的那把 key）。

为什么要单独测：云端音色通常比 Windows 自带的自然得多，但**能不能用取决于服务商**
（要支持 OpenAI 兼容的 /audio/speech）。这里真发一次请求，把结果说清楚：
能用 → 界面上可以一键切过去；不能用（404/401/400）→ 就把服务端的原话报出来，
别让学生对着"语音没出来"猜。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import tts  # noqa: E402


async def try_one(base: str, model: str, voice: str, key: str) -> None:
    import httpx

    payload = {"model": model, "input": "你好，我是艾琳老师。", "voice": voice, "response_format": "mp3"}
    print(f"\n试：{base}  模型={model}  音色={voice}")
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(base.rstrip("/") + "/audio/speech", json=payload,
                                  headers={"Authorization": f"Bearer {key}"})
        if r.status_code == 200 and len(r.content) > 500:
            print(f"  ✅ 能用！返回 {len(r.content)} 字节 {r.headers.get('content-type')}")
        else:
            print(f"  ❌ HTTP {r.status_code}：{r.text[:300]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ❌ 连不上：{type(exc).__name__}: {exc}")


async def main() -> int:
    from kernel.config import load_app_config

    cfg = load_app_config()
    key = str(cfg.get("chat_api_key") or cfg.get("cloud_api_key") or "")
    base = str(cfg.get("chat_base_url") or "https://api.siliconflow.cn/v1")
    if not key:
        print("没有可用的 key，跳过")
        return 0
    print(f"用你的 key（{key[:6]}…）试云端语音，base={base}")

    # 硅基流动的语音模型 + 它文档里的音色名
    await try_one(base, "FunAudioLLM/CosyVoice2-0.5B", "FunAudioLLM/CosyVoice2-0.5B:alex", key)
    # 也试一下 OpenAI 那套名字（有些网关两种都收）
    await try_one(base, "tts-1", "alloy", key)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
