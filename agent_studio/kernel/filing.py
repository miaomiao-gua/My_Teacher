"""对话自动归档：**用纯对话，让 AI 自己判断该放进哪个文件夹**。

左栏是「学科 → 教材 → 对话」三级。以前要用户右键「移动到 → 数学P1 → 9709」，
太重了 —— 学生只想聊天，归档应该是 AI 的事。

两条硬规则（都来自「别帮倒忙」）：
1. **用户自己归过档的，绝不自动改**：`meta["filed_by"] == "user"` 之后自动归档就不碰它。
2. **猜不准就别动**：模型没给出把握或者解析失败 → 保持原样（宁可留在「未分类」，
   也不要塞进错误的教材里让人找不到）。置信度低于阈值时也不动。

两条路径，互为兜底：
- 主线：模型在教学过程中直接调用 `file_conversation` 工具（零额外开销、最准）；
- 兜底：这一轮跑完后，如果还是「未分类」，后台用一次很短的模型调用判断一下（见 `maybe_autofile`）。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

MAX_SUBJECT = 24
MAX_TEXTBOOK = 40
MIN_CONFIDENCE = 0.5
RETRY_AFTER = 600.0          # 同一个会话两次自动判断之间至少隔 10 分钟

FILING_SYSTEM = """你在给一个 AI 教学助手整理对话归档。
看下面的对话片段，判断它属于哪个「学科」和哪本「教材」，只输出 JSON，不要解释。"""

FILING_USER = """已有文件夹（学科 / 教材），能对上就直接用这些名字：
{folder_list}

对话片段：
{conversation}

请判断这段对话应该归到哪个学科和哪本教材，输出严格的 JSON：
{{"subject": "学科名（如 数学、物理、语文）", "textbook": "教材/考试名（如 9709、人教版必修一；实在判断不出就写 未指定教材）", "confidence": 0.0 到 1.0 的小数, "reason": "一句话理由"}}
要求：
- 学科必须给（判断不出就写「未分类」）
- 教材判断不出就写「未指定教材」，**不要瞎编教材名**
- 这是给学生的对话，用中文"""


def _clean_name(text: Any, limit: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip().strip("「」\"'")
    s = s.replace("/", "／").replace("\\", "＼")          # 避免和路径分隔符混淆
    return s[:limit]


def conversation_excerpt(session: Any, max_messages: int = 6, max_chars: int = 1200) -> str:
    """取对话开头几轮做判断依据。

    **取开头而不是结尾**：学科/教材在第一两句就定了，后面全是细节，
    结尾还可能跑题（问了个别的科目的问题）。
    """
    lines: List[str] = []
    for msg in (getattr(session, "messages", None) or [])[:max_messages]:
        role = "学生" if msg.get("role") == "user" else "老师"
        text = re.sub(r"\s+", " ", str(msg.get("content") or "")).strip()
        if text:
            lines.append(f"{role}：{text[:300]}")
    out = "\n".join(lines)
    return out[:max_chars]


def existing_folders() -> List[Dict[str, str]]:
    try:
        from .session import load_folders
        return load_folders()
    except Exception:
        return []


def needs_filing(session: Any) -> bool:
    """这个会话值不值得让 AI 判断一下归属"""
    if session is None:
        return False
    if (getattr(session, "subject", "") or "").strip():
        return False                                   # 已经有学科了（人工或模型给过）
    meta = getattr(session, "meta", None) or {}
    if meta.get("filed_by"):
        return False                                   # 用户定过的，别动
    if len([m for m in (getattr(session, "messages", None) or []) if m.get("role") == "user"]) < 1:
        return False                                   # 还没说话，没什么可判断的
    last = float(meta.get("filing_tried_at") or 0)
    if last and time.time() - last < RETRY_AFTER:
        return False                                   # 刚试过，别反复问模型
    return True


def parse_filing(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出里抠出 JSON（模型常带 markdown 围栏或前后废话）"""
    raw = (text or "").strip()
    if not raw:
        return None
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        try:
            from .llm import _safe_json
            data = _safe_json(m.group(0))
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    subject = _clean_name(data.get("subject"), MAX_SUBJECT)
    textbook = _clean_name(data.get("textbook"), MAX_TEXTBOOK) or "未指定教材"
    try:
        conf = float(data.get("confidence", 0.6))
    except Exception:
        conf = 0.6
    if not subject or subject in ("未分类", "未知"):
        return None
    return {
        "subject": subject,
        "textbook": textbook,
        "confidence": max(0.0, min(1.0, conf)),
        "reason": _clean_name(data.get("reason"), 80),
    }


def apply_filing(session: Any, subject: str, textbook: str = "", *,
                 by: str = "model", reason: str = "") -> bool:
    """真正落盘：写 subject/textbook + 登记文件夹（空文件夹也能留在左栏）"""
    subject = _clean_name(subject, MAX_SUBJECT)
    if not subject or session is None:
        return False
    if (getattr(session, "meta", None) or {}).get("filed_by") == "user" and by != "user":
        return False                                   # 用户定过的，模型不能覆盖
    textbook = _clean_name(textbook, MAX_TEXTBOOK) or "未指定教材"
    changed = (session.subject != subject) or (session.textbook != textbook)
    session.subject = subject
    session.textbook = textbook
    session.meta = dict(getattr(session, "meta", None) or {})
    session.meta["filed_by"] = by
    session.meta["filed_at"] = time.time()
    if reason:
        session.meta["filed_reason"] = reason
    try:
        from .session import ensure_folder
        ensure_folder(subject, textbook)               # 让这个文件夹出现在左栏
    except Exception:
        pass
    try:
        session.save()
    except Exception:
        return False
    return changed


async def classify_session(session: Any, *, timeout: float = 30.0) -> Optional[Dict[str, Any]]:
    """一次很短的模型调用，判断归属。失败一律返回 None（绝不影响上课）。"""
    excerpt = conversation_excerpt(session)
    if not excerpt:
        return None
    folders = existing_folders()
    folder_list = "\n".join(
        f"- {f.get('subject')} / {f.get('textbook') or '未指定教材'}" for f in folders[:30]
    ) or "（还没有文件夹）"

    try:
        import asyncio

        from .config import llm_configs
        from .llm import build_llm

        messages = [
            {"role": "system", "content": FILING_SYSTEM},
            {"role": "user", "content": FILING_USER.format(folder_list=folder_list,
                                                           conversation=excerpt)},
        ]
        last_err: Optional[Exception] = None
        for cfg in llm_configs():
            try:
                client = build_llm(cfg)
                buf: List[str] = []

                async def _run() -> None:
                    async for chunk in client.stream(messages):
                        if chunk.kind == "text" and chunk.text:
                            buf.append(chunk.text)

                await asyncio.wait_for(_run(), timeout=timeout)
                parsed = parse_filing("".join(buf))
                if parsed:
                    return parsed
            except Exception as exc:                     # noqa: BLE001
                last_err = exc
                continue
        if last_err:
            return None
        return None
    except Exception:
        return None


async def maybe_autofile(session: Any) -> Optional[Dict[str, Any]]:
    """后台兜底：这一轮讲完了，如果还没归档，就让 AI 判断一下。

    设计上**绝不抛异常**：归档失败顶多留在「未分类」，不能影响正常教学。
    """
    if not needs_filing(session):
        return None
    session.meta = dict(getattr(session, "meta", None) or {})
    session.meta["filing_tried_at"] = time.time()        # 先记上，避免并发重复调用
    info = await classify_session(session)
    if not info or info.get("confidence", 0) < MIN_CONFIDENCE:
        try:
            session.save()
        except Exception:
            pass
        return None
    if apply_filing(session, info["subject"], info.get("textbook", ""),
                    by="model", reason=info.get("reason", "")):
        info["applied"] = True
        return info
    return None
