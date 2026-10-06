"""看一眼屏幕：学生按快捷键 → 截屏 → 交给能看图的模型 → 用一句话答在气泡里。

为什么要单独一个模块（而不是塞进 runtime）：
- 这条链路**不需要工具、不需要多轮**，就是"一张图 + 一句要求 → 一段回答"，
  塞进 agent 主循环反而慢（要跑工具、要裁历史）。这里一次性问完就返回。
- 主链路（云端 DeepSeek-V3）**看不见图**，所以看图要单独选一条**能看图**的链路；
  默认走本机 Ollama（免费、不联网），也可以让学生指到云端的视觉模型。

三个坑：
1. 图要**压**：2560×1600 的原图直接发给模型，token 会爆、还慢。统一缩到长边 ≤ 1400 并转 JPEG。
2. 提示词要**限定输出**：屏幕内容往往很杂，不约束的话模型会把整个屏幕念一遍。
   默认要求它"只说最相关的一件事，≤3 句"。
3. 模型不支持图片时，服务商给的报错往往很含糊 —— 这里要把"可能是模型不支持看图"
   一起写进错误里，不然学生只会看到一句 400。
"""

from __future__ import annotations

import base64
import io
import time
from typing import Any, Dict, Optional, Tuple

MAX_SIDE = 1400            # 长边上限（压完再发）
JPEG_QUALITY = 82

DEFAULT_PROMPT = (
    "这是我屏幕上的画面。请用**中文**、**不超过三句话**，告诉我：如果我在学习，"
    "最该看的一处是什么、给了什么条件或要求。"
    "不要逐字念屏幕，不要罗列所有内容；看不清就说看不清。"
)


def settings() -> Dict[str, Any]:
    """从设置中心读"看图"这条链路的配置。"""
    from .settings_api import read_state

    try:
        v = read_state().get("values", {})
    except Exception:
        v = {}
    return {
        "provider": str(v.get("vision_provider") or "local"),   # off | local | cloud
        "model": str(v.get("vision_model") or "qwen2.5vl:7b"),
        "prompt": str(v.get("vision_prompt") or DEFAULT_PROMPT),
        "space": str(v.get("vision_space") or ""),
    }


def prepare_image(data: bytes, max_side: int = MAX_SIDE) -> Tuple[str, Dict[str, Any]]:
    """把截屏压成适合发给模型的 JPEG，返回 (base64, 说明)。

    说明里带上原始尺寸和压缩后大小 —— 出问题时一眼能看出是"图太大"还是"模型不认图"。
    """
    from PIL import Image

    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")
    w0, h0 = img.size
    scale = min(1.0, float(max_side) / max(w0, h0))
    if scale < 1.0:
        img = img.resize((max(1, int(w0 * scale)), max(1, int(h0 * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    raw = buf.getvalue()
    return base64.b64encode(raw).decode("ascii"), {
        "from": [w0, h0], "to": list(img.size), "kb": round(len(raw) / 1024, 1),
    }


def _config() -> Any:
    """按设置拼一条 LLM 链路（复用云端那把 key，视觉模型多半在同一个服务商）"""
    from .config import LLMConfig, load_app_config

    cfg = settings()
    app = load_app_config()
    provider = cfg["provider"]
    if provider == "local":
        return LLMConfig(
            provider="ollama",
            model=cfg["model"] or "qwen2.5vl:7b",
            base_url=(app.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/"),
            temperature=0.2,
            num_predict=600,
            think=False,
        )
    key = str(app.get("chat_api_key") or app.get("cloud_api_key") or "")
    return LLMConfig(
        provider="openai",
        model=cfg["model"] or "Qwen/Qwen2.5-VL-32B-Instruct",
        base_url=(app.get("chat_base_url") or "https://api.siliconflow.cn/v1").rstrip("/"),
        api_key=key,
        temperature=0.2,
        num_predict=600,
    )


async def ask(image_b64: str, question: str = "", session: Any = None,
              context_turns: int = 6) -> Dict[str, Any]:
    """把一张图交给模型看，返回 {ok, answer, model, ms, note}。

    `question` 是学生打字问的那句（比如「套索工具咋用，在哪里」）；
    `session` 有的话会带上**最近几轮对话**当上下文 —— 学生说的"根据上下文"就是这个：
    他可能先说了"我在用 PS"，再问"套索工具在哪"，没有上下文模型就不知道"那个"指什么。

    不抛异常：调用方是桌宠，出错就让它把错误显示在气泡里（学生看得见才知道怎么办）。
    """
    from .llm import build_llm
    from .prompts import BASE_PERSONA

    cfg = settings()
    if cfg["provider"] == "off":
        return {"ok": False, "answer": "看图功能关着（设置 → 看图 → 选本机或云端）",
                "note": "vision off"}

    llm_cfg = _config()
    text = (question or "").strip() or cfg["prompt"]
    system = (BASE_PERSONA + "\n\n现在学生按下快捷键、或打字问了你一句，"
              "你会**同时看到他的屏幕截图**。要求：\n"
              "1. 结合截图回答他的问题（例如屏幕上是 Photoshop，他问「套索工具在哪」，"
              "就要指出界面上**具体哪个位置**、怎么用）；\n"
              "2. 用中文，**不超过 4 句话**，说重点，别把整个屏幕念一遍；\n"
              "3. 不要说「我看不到屏幕」这类话——图已经在你手上了；看不清就直说看不清。")
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system}]
    # 上下文：只带最近的**纯文本**对话（历史里的图不再重复发，省 token）
    if session is not None and context_turns > 0:
        try:
            recent = [m for m in (session.messages or [])
                      if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()]
            for m in recent[-context_turns:]:
                messages.append({"role": m["role"], "content": str(m["content"])[:600]})
        except Exception:
            pass
    messages.append({"role": "user", "content": text, "images": [image_b64]})

    t0 = time.time()
    try:
        llm = build_llm(llm_cfg)
        parts = []
        async for chunk in llm.stream(messages):
            if chunk.kind == "text" and chunk.text:
                parts.append(chunk.text)
        answer = "".join(parts).strip()
        if not answer:
            return {"ok": False, "answer": "模型没说话（可能这个模型不认图片）",
                    "model": llm_cfg.label, "ms": int((time.time() - t0) * 1000)}
        out = {"ok": True, "answer": answer, "model": llm_cfg.label,
               "ms": int((time.time() - t0) * 1000)}
        if session is not None:
            # 记进会话历史：这样"看屏幕"也能接着聊（网页版里能看到这段对话）
            try:
                session.messages.append({
                    "role": "user",
                    "content": ("（我让你看屏幕：" + question.strip() + "）") if question.strip()
                               else "（我按了快捷键，让你看一眼屏幕）",
                    "ts": time.time(),
                })
                session.messages.append({"role": "assistant", "content": answer, "ts": time.time()})
                if hasattr(session, "save"):
                    session.save()
            except Exception:
                pass
        return out
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        hint = ""
        low = msg.lower()
        if any(k in low for k in ("image", "vision", "multimodal", "400", "invalid")):
            hint = "（这个模型可能不支持看图：设置 → 看图，换成视觉模型，比如本机 qwen3.5:9b 或云端 Qwen2.5-VL）"
        return {"ok": False, "answer": f"看图失败了：{msg[:160]}{hint}",
                "model": llm_cfg.label, "ms": int((time.time() - t0) * 1000), "note": msg}


def grab_screen(region: Optional[Tuple[int, int, int, int]] = None) -> bytes:
    """截屏（PNG 字节）。`region` 为空就截整个屏幕。"""
    from PIL import ImageGrab

    img = ImageGrab.grab(bbox=region, all_screens=True) if region else ImageGrab.grab(all_screens=True)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def self_check() -> Dict[str, Any]:
    cfg = settings()
    return {
        "provider": cfg["provider"],
        "model": cfg["model"],
        "ready": cfg["provider"] in ("local", "cloud"),
        "note": ("关着" if cfg["provider"] == "off"
                 else f"{cfg['provider']}：{cfg['model']}"),
    }
