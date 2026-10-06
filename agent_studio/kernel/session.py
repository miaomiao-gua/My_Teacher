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

from .config import FOLDERS_PATH, SESSIONS_DIR, WORKSPACES_DIR

# 历史预算：中文 1 字 ≈ 1 token 量级，留足给 system prompt 与工具 schema
HISTORY_CHAR_BUDGET = 24000
MAX_KEEP_MESSAGES = 60


# ---------------------------------------------------------------------------
# 文件夹登记表
# ---------------------------------------------------------------------------
# 为什么需要单独存一份：树里的「学科 / 教材」是从**会话字段聚合**出来的 ——
# 没有会话就没有文件夹。于是「在空白处新建一个空文件夹」这种最基本的操作
# 根本显示不出来（建完就消失），这跟资源管理器的直觉不符。
# 所以额外登记「存在哪些文件夹」：文件夹可以先空着，之后再往里放对话；
# 把里面的对话删光，文件夹本身还在。
def load_folders() -> List[Dict[str, str]]:
    try:
        data = json.loads(FOLDERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, str]] = []
    for item in data:
        if isinstance(item, dict):
            out.append({
                "subject": str(item.get("subject") or "").strip(),
                "textbook": str(item.get("textbook") or "").strip(),
            })
    return out


def save_folders(items: List[Dict[str, str]]) -> None:
    """原子写：先写临时文件再替换，避免中断留下半个 JSON。"""
    # 去重（同一 subject+textbook 只留一条）
    seen = set()
    uniq: List[Dict[str, str]] = []
    for item in items:
        key = (item.get("subject", ""), item.get("textbook", ""))
        if key in seen:
            continue
        seen.add(key)
        uniq.append({"subject": key[0], "textbook": key[1]})
    FOLDERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = FOLDERS_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(uniq, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(FOLDERS_PATH)
    except Exception:
        pass


def ensure_folder(subject: str, textbook: str = "") -> List[Dict[str, str]]:
    """登记一个文件夹（已存在则原样返回）。空白处右键「新建学科/教材」走这里。"""
    items = load_folders()
    key = ((subject or "").strip(), (textbook or "").strip())
    if key not in [(i["subject"], i["textbook"]) for i in items]:
        items.append({"subject": key[0], "textbook": key[1]})
        save_folders(items)
    return items


def rename_folder(subject: str, textbook: str, new_subject: Optional[str] = None,
                  new_textbook: Optional[str] = None) -> Dict[str, Any]:
    """重命名（或移动）一个文件夹，并把里面所有对话的归属一起改掉。

    返回 {"sessions": 受影响会话数, "folders": [...]}。
    为什么必须同时改会话：树是按会话字段聚合的，只改登记表的话，
    下次刷新树会按旧字段把旧文件夹又「长」出来。
    """
    old_subject = (subject or "").strip()
    old_textbook = (textbook or "").strip()
    target_subject = (new_subject if new_subject is not None else old_subject).strip()
    target_textbook = (new_textbook if new_textbook is not None else old_textbook).strip()

    moved = 0
    for path in SESSIONS_DIR.glob("*.json") if SESSIONS_DIR.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (data.get("subject") or "").strip() != old_subject:
            continue
        if (data.get("textbook") or "").strip() != old_textbook:
            continue
        data["subject"] = target_subject
        data["textbook"] = target_textbook
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            moved += 1
        except Exception:
            pass

    items = [i for i in load_folders()
             if not ((i["subject"], i["textbook"]) == (old_subject, old_textbook))]
    items.append({"subject": target_subject, "textbook": target_textbook})
    save_folders(items)
    return {"sessions": moved, "folders": load_folders()}


def delete_folder(subject: str, textbook: str, delete_sessions: bool = True) -> Dict[str, Any]:
    """删除文件夹。默认连同里面的对话一起删（与资源管理器一致）。"""
    subj = (subject or "").strip()
    book = (textbook or "").strip()
    removed = 0
    if delete_sessions:
        for path in list(SESSIONS_DIR.glob("*.json")) if SESSIONS_DIR.exists() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if (data.get("subject") or "").strip() == subj and (data.get("textbook") or "").strip() == book:
                try:
                    path.unlink()
                    removed += 1
                except Exception:
                    pass
    save_folders([i for i in load_folders()
                  if not ((i["subject"], i["textbook"]) == (subj, book))])
    return {"sessions": removed, "folders": load_folders()}


def sessions_in_folder(subject: str, textbook: str) -> List[str]:
    """某个文件夹下的会话 id 列表。"""
    subj = (subject or "").strip()
    book = (textbook or "").strip()
    ids: List[str] = []
    for path in SESSIONS_DIR.glob("*.json") if SESSIONS_DIR.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (data.get("subject") or "").strip() == subj and (data.get("textbook") or "").strip() == book:
            ids.append(str(data.get("id") or path.stem))
    return ids


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

    # 层级归属：学科（数学/语文…）→ 教材（如「人教版必修一」）→ 对话
    # 为什么放在会话上而不是单独建索引：会话本身就是一个文件，
    # 归属信息跟着它走，删会话就自然消失，不会留下孤儿目录。
    subject: str = ""
    textbook: str = ""
    title: str = ""

    # 运行时字段，不落盘
    cancel: asyncio.Event = field(default_factory=asyncio.Event, repr=False, compare=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    # ---- 标题 ----
    def auto_title(self, max_len: int = 22) -> str:
        """没起标题时，用第一条用户消息当标题（左侧列表要能一眼认出对话）。"""
        if self.title:
            return self.title
        for msg in self.messages:
            if msg.get("role") == "user" and (msg.get("content") or "").strip():
                text = re.sub(r"\s+", " ", str(msg["content"]).strip())
                return text[:max_len] + ("…" if len(text) > max_len else "")
        return "新对话"

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
            "subject": self.subject,
            "textbook": self.textbook,
            "title": self.title,
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
            subject=data.get("subject") or "",
            textbook=data.get("textbook") or "",
            title=data.get("title") or "",
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
            "subject": self.subject,
            "textbook": self.textbook,
            "title": self.auto_title(),
            "message_count": len(self.messages),
            "artifacts": self.artifacts,
            "created_at": self.created_at,
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

    # ---- 列表与层级（左侧历史栏用） ----
    def list_all(self, limit: int = 400) -> List[Dict[str, Any]]:
        """所有会话的概要，按最近活跃排序。"""
        out: List[Dict[str, Any]] = []
        if SESSIONS_DIR.exists():
            for path in SESSIONS_DIR.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    out.append(Session.from_dict(data).public())
                except Exception:
                    continue
        out.sort(key=lambda s: float(s.get("updated_at") or 0), reverse=True)
        return out[:limit]

    def tree(self) -> List[Dict[str, Any]]:
        """按「学科 → 教材 → 对话」组织成树。

        两个来源合并：
          1. **文件夹登记表**（可能有空文件夹 —— 用户右键新建了但还没放对话）
          2. **会话自身的 subject/textbook 字段**（层级不另建索引，删对话就自然消失）
        「未分类 / 未指定教材」兜底，保证没有任何对话会从界面上消失。
        """
        buckets: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

        # 先铺登记的文件夹（哪怕一个对话都没有，也要在树上占个位）
        for pair in load_folders():
            subject = pair.get("subject") or "未分类"
            textbook = pair.get("textbook") or "未指定教材"
            buckets.setdefault(subject, {}).setdefault(textbook, [])

        for sess in self.list_all():
            subject = (sess.get("subject") or "").strip() or "未分类"
            textbook = (sess.get("textbook") or "").strip() or "未指定教材"
            buckets.setdefault(subject, {}).setdefault(textbook, []).append(sess)

        def sort_key(name: str) -> tuple:
            return (name in ("未分类", "未指定教材"), name)

        subjects: List[Dict[str, Any]] = []
        for subject in sorted(buckets, key=sort_key):
            textbooks = []
            for textbook in sorted(buckets[subject], key=sort_key):
                sessions = buckets[subject][textbook]
                textbooks.append({
                    "name": textbook,
                    "count": len(sessions),
                    "sessions": sessions,
                    "updated_at": sessions[0].get("updated_at") if sessions else 0,
                })
            subjects.append({
                "name": subject,
                "count": sum(t["count"] for t in textbooks),
                "textbooks": textbooks,
            })
        return subjects


STORE = SessionStore()
