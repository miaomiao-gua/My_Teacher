"""语音合成（TTS）：让桌宠能把气泡念出来。

三种来源，学生自己在设置里选：
- `off`     —— 不出声
- `windows` —— **用 Windows 自带的语音**（离线、不用 key）。本机有中文嗓子
                「Microsoft Huihui」，中文念得还行；这是默认推荐。
- `openai`  —— 任何 OpenAI 兼容的云端 TTS（POST {base_url}/audio/speech）。
                硅基流动、OpenAI、自建服务都能用，只要填 base_url / key / model / voice。

为什么返回音频字节而不是"在服务器上放声音"：服务端和浏览器常常是同一台机器，
但**声音该由浏览器播**（这样静音、音量、蓝牙耳机都归学生自己的设备管），
而且 exe 形态下服务器没有音频设备。所以统一返回 wav/mp3 给前端播。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROVIDERS = ("off", "windows", "openai")

# 常见的 OpenAI 兼容音色：只作为下拉候选，学生也能自己填一个
KNOWN_VOICES: List[Dict[str, str]] = [
    {"id": "alloy", "name": "alloy"},
    {"id": "echo", "name": "echo"},
    {"id": "fable", "name": "fable"},
    {"id": "nova", "name": "nova"},
    {"id": "onyx", "name": "onyx"},
    {"id": "shimmer", "name": "shimmer"},
    {"id": "FunAudioLLM/CosyVoice2-0.5B:alex", "name": "硅基流动 · alex"},
    {"id": "FunAudioLLM/CosyVoice2-0.5B:anna", "name": "硅基流动 · anna"},
    {"id": "FunAudioLLM/CosyVoice2-0.5B:bella", "name": "硅基流动 · bella"},
    {"id": "FunAudioLLM/CosyVoice2-0.5B:benjamin", "name": "硅基流动 · benjamin"},
    {"id": "FunAudioLLM/CosyVoice2-0.5B:charles", "name": "硅基流动 · charles"},
    {"id": "FunAudioLLM/CosyVoice2-0.5B:claire", "name": "硅基流动 · claire"},
    {"id": "FunAudioLLM/CosyVoice2-0.5B:david", "name": "硅基流动 · david"},
]


def _values() -> Dict[str, Any]:
    """从设置中心读当前 TTS 配置（内含已解密的 key，只在本进程内用）。"""
    from .settings_api import read_state

    try:
        return read_state().get("values", {}) | {
            "tts_cloud_api_key": _secret_or_shared(),
        }
    except Exception:
        return {}


def _secret_or_shared() -> str:
    """TTS 没单独填 key 时，直接复用云端大模型那把 key（多半是同一个服务商）。"""
    from .config import load_app_config

    cfg = load_app_config()
    return str(cfg.get("tts_cloud_api_key") or cfg.get("chat_api_key") or "")


def settings() -> Dict[str, Any]:
    v = _values()
    return {
        "provider": str(v.get("tts_provider") or "off"),
        "windows_voice": int(v.get("tts_windows_voice") or 0),
        "base_url": str(v.get("tts_cloud_base_url") or "https://api.siliconflow.cn/v1"),
        "model": str(v.get("tts_cloud_model") or "FunAudioLLM/CosyVoice2-0.5B"),
        "voice": str(v.get("tts_cloud_voice") or "FunAudioLLM/CosyVoice2-0.5B:alex"),
        "api_key": _secret_or_shared(),
        "speed": float(v.get("tts_speed") or 1.0),
        "volume": int(v.get("tts_volume") or 100),
        "auto_speak": bool(v.get("tts_auto_speak")),
    }


def windows_voices() -> List[Dict[str, Any]]:
    """系统里装了哪些嗓子（装过中文语音包的机器上会看到 Huihui）"""
    out: List[Dict[str, Any]] = []
    try:
        import win32com.client as win32

        voice = win32.Dispatch("SAPI.SpVoice")
        items = voice.GetVoices()
        for i in range(items.Count):
            try:
                desc = str(items.Item(i).GetDescription())
            except Exception:
                desc = f"voice-{i}"
            out.append({"id": i, "name": desc})
    except Exception as exc:  # noqa: BLE001
        return [{"id": 0, "name": f"（读不到系统语音：{exc}）"}]
    return out


def voices() -> Dict[str, Any]:
    return {"windows": windows_voices(), "cloud": KNOWN_VOICES}


def _windows_wav(text: str, cfg: Dict[str, Any]) -> bytes:
    """用 Windows SAPI 合成一段 wav（离线，不用任何 key）"""
    import win32com.client as win32

    voice = win32.Dispatch("SAPI.SpVoice")
    try:
        items = voice.GetVoices()
        idx = int(cfg.get("windows_voice") or 0)
        if 0 <= idx < items.Count:
            voice.Voice = items.Item(idx)
    except Exception:
        pass
    try:
        voice.Rate = max(-10, min(10, int(round((float(cfg.get("speed") or 1.0) - 1.0) * 10))))
    except Exception:
        pass
    try:
        voice.Volume = max(0, min(100, int(cfg.get("volume") or 100)))
    except Exception:
        pass

    path = Path(tempfile.mkdtemp(prefix="tts_")) / "say.wav"
    stream = win32.Dispatch("SAPI.SpFileStream")
    # 3 = SSFMCreateForWrite：这个数字是 SAPI 的常量，网上很多例子写错成别的值就写不出文件
    stream.Open(str(path), 3, False)
    voice.AudioOutputStream = stream
    try:
        voice.Speak(text[:600])
    finally:
        try:
            stream.Close()
        except Exception:
            pass
    data = path.read_bytes()
    if len(data) < 100:
        raise RuntimeError("系统语音没生成音频（可能没装中文语音包）")
    return data


async def _cloud_audio(text: str, cfg: Dict[str, Any]) -> bytes:
    import httpx

    base = str(cfg.get("base_url") or "").rstrip("/")
    if not base:
        raise RuntimeError("没填云端语音的接口地址")
    if not cfg.get("api_key"):
        raise RuntimeError("没填 API Key（TTS 会复用云端大模型的 key，也可以在语音那栏单独填）")
    payload: Dict[str, Any] = {
        "model": cfg.get("model") or "tts-1",
        "input": text[:1000],
        "voice": cfg.get("voice") or "alloy",
        "response_format": "mp3",
    }
    try:
        speed = float(cfg.get("speed") or 1.0)
        if 0.25 <= speed <= 4.0:
            payload["speed"] = speed
    except Exception:
        pass
    async with httpx.AsyncClient(timeout=90.0) as client:
        r = await client.post(base + "/audio/speech", json=payload,
                              headers={"Authorization": f"Bearer {cfg['api_key']}"})
    if r.status_code != 200:
        raise RuntimeError(f"云端语音返回 HTTP {r.status_code}：{r.text[:200]}")
    if len(r.content) < 200:
        raise RuntimeError("云端语音返回的内容不像音频")
    return r.content


async def synthesize(text: str) -> Tuple[bytes, str]:
    """合成一句话，返回 (音频字节, mime)。provider=off 或没文字时抛 RuntimeError。"""
    text = (text or "").strip()
    if not text:
        raise RuntimeError("没有要念的文字")
    cfg = settings()
    provider = str(cfg.get("provider") or "off")
    if provider not in PROVIDERS:
        provider = "off"
    if provider == "off":
        raise RuntimeError("语音没开（设置 → 语音，选「Windows 本机语音」或云端）")
    if provider == "windows":
        return _windows_wav(text, cfg), "audio/wav"
    return await _cloud_audio(text, cfg), "audio/mpeg"


def self_check() -> Dict[str, Any]:
    """设置面板上那一行「当前语音」用的小结"""
    cfg = settings()
    info: Dict[str, Any] = {"provider": cfg["provider"], "ready": False, "note": ""}
    if cfg["provider"] == "windows":
        names = [v["name"] for v in windows_voices()]
        info["ready"] = True
        info["note"] = "本机语音：" + (names[int(cfg["windows_voice"]) % len(names)] if names else "?")
    elif cfg["provider"] == "openai":
        info["ready"] = bool(cfg["api_key"])
        info["note"] = (f"云端：{cfg['base_url']} / {cfg['model']} / {cfg['voice']}"
                        + ("" if cfg["api_key"] else "（缺 API Key）"))
    else:
        info["note"] = "未开启"
    return info
