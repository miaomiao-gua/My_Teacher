"""会话状态 —— 对话历史、工作目录、取消信号。

三个容易踩的坑，这里一次解决：
1. **历史无限增长**：老项目靠「最近 10 轮」硬截断，丢了教案上下文。
   这里改成按**字符预算**裁剪，并且始终保留 system 与最近若干条工具结果
   （工具结果丢了模型会重复调同一个工具）。
2. **取消**：学生中途插话或关页面时，正在跑的工具必须能被叫停，
   所以取消信号放进会话，内核每一步都检查。
3. **生成的文件放哪**：每个会话一个独立工作目录，互不污染，
   路径统一走 `safe_relpath` 防目录穿越。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import SESSIONS_DIR, WORKSPACES_DIR

# 历史预算：中文 1 字 ≈ 1 token 量级，留足给 system prompt 与工具 schema
HISTORY_CHAR_BUDGET = 24000
MAX_KEEP_MESSAGES = 60


def safe_relpath(path: str) -> str:
    """把模型给的路径清洗成工作目录内的相对路径（防 `../` 穿越与绝对路径）。"""
    raw = (path or "").replace("\\", "/").strip().lstrip("/")
    parts = [p for p in re.split(r"/+", raw) if p and p not in (".", "..")]
    if not parts:
        parts = ["untitled.txt"]
    clean = "/".join(parts)
    return clean[:180]


def _now() -> float:
    return time.time()


@dataclass
class Session:
    id: str
    username: Optional[str] = None
    lesson_folder: Optional[str] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=_now)
    updated_at: float = field(default_factory=_now)

    # 运行时字段，不落盘
    cancel: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    # ---- 工作目录 ----
    @property
    def workspace(self) -> Path:
        path = WORKSPACES_DIR / safe_relpath(self.id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def file_url(self, relpath: str) -> str:
        """生成给前端访问的 URL（由 server 层挂载的静态路由提供）。"""
        return f"/agent/files/{self.id}/{safe_relpath(relpath)}"

    # ---- 消息 ----
    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text, "ts": _now()})

    def add_assistant(self, content: str, tool_calls: Optional[List[Dict[str, Any]]] = None) -> None:
        msg: Dict[str, Any] = {"role": "assistant", "content": content or "", "ts": _now()}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.messages.append(msg)

    def add_tool(self, call_id: str, name: str, content: str) -> None:
        self.messages.append(
            {"role": "tool", "tool_call_id": call_id, "name": name, "content": content, "ts": _now()}
        )

    def history(self, system_prompt: str) -> List[Dict[str, Any]]:
        """构造发给模型的消息列表（system + 裁剪后的历史）。"""
        history = self._trimmed()
        return [{"role": "system", "content": system_prompt}] + history

    def _trimmed(self) -> List[Dict[str, Any]]:
        msgs = self.messages[-MAX_KEEP_MESSAGES:]
        # 从后往前累加字符数，超预算就丢最早的；但保证不把 assistant(tool_calls) 与 tool 结果拆散
        total = 0
        kept: List[Dict[str, Any]] = []
        for msg in reversed(msgs):
            size = len(json.dumps(msg, ensure_ascii=False, default=str))
            if total + size > HISTORY_CHAR_BUDGET and kept:
                break
            kept.append(msg)
            total += size
        kept.reverse()
        # 开头若是 tool 结果（其对应的 assistant 调用已被裁掉），丢弃它，避免协议不合法
        while kept and kept[0].get("role") == "tool":
            kept.pop(0)
        return [self._to_llm(m) for m in kept]

    @staticmethod
    def _to_llm(msg: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"role": msg.get("role"), "content": msg.get("content") or ""}
        if msg.get("tool_calls"):
            out["tool_calls"] = msg["tool_calls"]
        if msg.get("role") == "tool":
            out["tool_call_id"] = msg.get("tool_call_id")
            out["name"] = msg.get("name")
        return out

    # ---- 持久化 ----
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "lesson_folder": self.lesson_folder,
            "messages": self.messages,
            "artifacts": self.artifacts,
            "meta": self.meta,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def save(self) -> None:
        self.updated_at = _now()
        path = SESSIONS_DIR / f"{safe_relpath(self.id)}.json"
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        return cls(
            id=data.get("id") or uuid.uuid4().hex[:12],
            username=data.get("username"),
            lesson_folder=data.get("lesson_folder"),
            messages=data.get("messages") or [],
            artifacts=data.get("artifacts") or [],
            meta=data.get("meta") or {},
            created_at=data.get("created_at") or _now(),
            updated_at=data.get("updated_at") or _now(),
        )

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "lesson_folder": self.lesson_folder,
            "message_count": len(self.messages),
            "artifacts": self.artifacts,
            "updated_at": self.updated_at,
        }


class SessionStore:
    """会话仓库：内存缓存 + 磁盘持久化（进程重启不掉历史）。"""

    def __init__(self) -> None:
        self._cache: Dict[str, Session] = {}

    def get(self, session_id: str) -> Optional[Session]:
        sid = safe_relpath(session_id)
        if sid in self._cache:
            return self._cache[sid]
        path = SESSIONS_DIR / f"{sid}.json"
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sess = Session.from_dict(data)
                self._cache[sid] = sess
                return sess
            except Exception:
                return None
        return None

    def get_or_create(
        self, session_id: Optional[str], username: Optional[str] = None, lesson_folder: Optional[str] = None
    ) -> Session:
        sid = safe_relpath(session_id) if session_id else uuid.uuid4().hex[:12]
        sess = self.get(sid)
        if sess is None:
            sess = Session(id=sid, username=username, lesson_folder=lesson_folder)
            self._cache[sid] = sess
            sess.save()
        else:
            if username:
                sess.username = username
            if lesson_folder:
                sess.lesson_folder = lesson_folder
        return sess

    def drop(self, session_id: str) -> None:
        sid = safe_relpath(session_id)
        self._cache.pop(sid, None)
        path = SESSIONS_DIR / f"{sid}.json"
        if path.exists():
            path.unlink()


STORE = SessionStore()
