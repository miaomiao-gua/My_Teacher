"""桌宠事件总线 + 提醒。

桌宠要「知道老师在干什么」，但它不该去解析聊天流（那是浏览器的事）。
所以主循环把事件**顺手抄一份**到这条总线上（见 server.py 里那一处 mirror），
桌宠订阅它就行 —— 一个生产者、一个消费者，谁都不用改内核逻辑。

提醒有两个来源，但只有一份数据：
- 模型调 `set_reminder`（「我 30 分钟后提醒你复习」）
- 学生自己在桌宠右键菜单里点「20 分钟后提醒我」
两者都写进 `data/reminders.json`，由服务端一个小循环到点后发到总线。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_BACKLOG = 20          # 只为「提醒」保留一点积压：桌宠晚启动也能补上


def _now() -> float:
    return time.time()


class PetBus:
    """进程内的极简广播：每个订阅者一个队列，满了就丢最旧的（桌宠卡住也不拖累服务）。"""

    def __init__(self) -> None:
        self._subs: List[asyncio.Queue] = []
        self._reminder_backlog: List[Dict[str, Any]] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subs.remove(q)
        except ValueError:
            pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, event: Dict[str, Any]) -> None:
        ev = dict(event or {})
        ev.setdefault("ts", _now())
        if ev.get("type") == "reminder":
            self._reminder_backlog.append(ev)
            self._reminder_backlog = self._reminder_backlog[-MAX_BACKLOG:]
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:                 # 订阅者太慢 → 丢最旧的，别堵住
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except Exception:
                    pass

    def recent_reminders(self, within_seconds: float = 3600, limit: int = 3) -> List[Dict[str, Any]]:
        """最近发生的提醒（桌宠刚启动时补报一下，别让提醒白定了）"""
        cut = _now() - within_seconds
        return [e for e in self._reminder_backlog if float(e.get("ts") or 0) >= cut][-limit:]


BUS = PetBus()


def bubble_text_for(event: Dict[str, Any], max_len: int = 90) -> str:
    """把一个事件翻成桌宠该说的**一句话**（纯函数，好测）。

    放在 kernel 里是为了「一份文案，两个桌宠共用」：
    桌面版（tkinter）和网页版（/agent/pet）都读事件里的 `text`，不会各写一套口径。
    说得短是有意的：气泡只放一句，细节去主界面看。
    """
    t = str(event.get("type") or "")
    d = event.get("data") or {}
    if t == "run.start":
        return "老师在想了…"
    if t == "run.end":
        if d.get("reason") == "error":
            return "这次没跑起来，去主界面看看？"
        n = d.get("steps")
        return f"讲完了（用了 {n} 步）" if n else "讲完了"
    if t == "tool.result":
        name = str(d.get("name") or "")
        if name in ("graph_calculator", "geometry_board", "data_lab", "solve_equation") and d.get("ok"):
            return "画好了，去工作台看"
        if name == "prepare_course" and d.get("ok"):
            return "课备好了！"
        if name == "show_artifact" and d.get("ok"):
            return "界面做好啦"
        if name == "file_conversation" and d.get("ok"):
            return ""
        if not d.get("ok"):
            return f"「{name}」没成功，我再试试"
        return ""
    if t == "filed":
        return str(d.get("text") or "")
    if t == "quiz":
        pct = d.get("percent")
        if pct is None:
            return "交卷啦，我看看"
        try:
            good = float(pct) >= 60
        except Exception:
            good = True
        return f"交卷：{pct} 分" + ("，不错！" if good else "，我们再过一遍")
    if t == "reminder":
        return str(d.get("text") or "该复习啦")
    if t == "notice":
        return str(d.get("text") or "")[:max_len]
    if t == "alert":
        return str(d.get("text") or "")[:max_len]
    return ""


def publish_with_text(event: Dict[str, Any]) -> Dict[str, Any]:
    """给事件补上 `text` 再广播（消费方只管显示，不用各自翻译）"""
    ev = dict(event or {})
    if "text" not in ev:
        ev["text"] = bubble_text_for(ev)
    BUS.publish(ev)
    return ev


# ---------------------------------------------------------------------------
# 提醒存储
# ---------------------------------------------------------------------------

def _path() -> Path:
    from runtime_paths import data_root
    return data_root() / "reminders.json"


def load_reminders() -> List[Dict[str, Any]]:
    p = _path()
    try:
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)]
    except Exception:
        pass
    return []


def save_reminders(items: List[Dict[str, Any]]) -> None:
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(items[-50:], ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


def add_reminder(minutes: float, text: str, source: str = "pet") -> Dict[str, Any]:
    """加一条提醒，返回这条记录（分钟数会被夹在 1 分钟 ~ 24 小时之间）"""
    try:
        m = float(minutes)
    except Exception:
        m = 20.0
    m = max(1.0, min(24 * 60.0, m))
    item = {
        "id": uuid.uuid4().hex[:8],
        "due": _now() + m * 60.0,
        "text": (text or "该复习啦").strip()[:120],
        "source": source,
        "done": False,
    }
    items = load_reminders()
    items.append(item)
    save_reminders(items)
    return item


def due_reminders(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """到点且还没发过的提醒（并把它们标记成已发，避免重复弹）"""
    now = _now() if now is None else now
    items = load_reminders()
    hit = [x for x in items if not x.get("done") and float(x.get("due") or 0) <= now]
    if hit:
        for x in items:
            if not x.get("done") and float(x.get("due") or 0) <= now:
                x["done"] = True
                x["fired_at"] = now
        save_reminders(items)
    return hit


def pending_reminders(now: Optional[float] = None) -> List[Dict[str, Any]]:
    now = _now() if now is None else now
    return [x for x in load_reminders() if not x.get("done") and float(x.get("due") or 0) > now]


async def reminder_loop(interval: float = 10.0) -> None:
    """服务端的小循环：到点就把提醒发到总线上（桌宠订阅它）"""
    while True:
        try:
            for item in due_reminders():
                BUS.publish({"type": "reminder", "data": {"text": item.get("text"),
                                                          "id": item.get("id")}})
        except Exception:
            pass
        await asyncio.sleep(interval)
