"""Agent 事件协议 —— 内核与前端之间唯一的通信契约。

一次 run（用户一次提问 → 模型可能调用多轮工具 → 最终回答）产生一串**有序**事件。
传输方式可以是 SSE（`event: <type>` + `data: <json>`）或 WebSocket，序列化结果完全一致，
所以前端逻辑不依赖传输层。

事件分三类
----------
- **对话类**：`text`（流式正文）、`thinking`（模型的推理过程，可折叠展示）
- **工具类**：`tool.call` → `tool.result`，中间可以有 `ui` / `artifact` / `stage`
- **生命周期**：`run.start` / `run.end` / `error`

关键约定：`ui` 事件在**工具执行的那一刻**就推出去，不等模型把话说完——
学生先看到界面出现，再听老师讲解，体感差别很大。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# 事件类型常量（前端按这些字符串分发）
# ---------------------------------------------------------------------------
RUN_START = "run.start"          # {run_id, session_id, model}
TEXT = "text"                    # {delta}                正文增量
THINKING = "thinking"            # {delta}                推理增量（可折叠）
TOOL_CALL = "tool.call"          # {id, name, args, step} 模型决定调用工具
TOOL_RESULT = "tool.result"      # {id, name, ok, summary, ms, error?}
UI = "ui"                        # {spec}                 生成式 UI（前端渲染到主舞台）
ARTIFACT = "artifact"            # {path, name, kind, url, bytes}
STAGE = "stage"                  # {emotion?, motion?, speak?} 角色表现（Live2D / TTS）
ERROR = "error"                  # {message, detail?}
NOTICE = "notice"                # {message, level}      给学生的提示（换链路/降级/被拦下）
RUN_END = "run.end"              # {run_id, reason, steps, ms}


@dataclass
class Event:
    """一个事件。`type` 用上面的常量，`data` 必须是可 JSON 序列化的 dict。"""

    type: str
    data: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "ts": self.ts, **self.data}

    def to_sse(self) -> str:
        """SSE 帧。data 里带 type 字段，前端只解析 data 也能工作。"""
        payload = json.dumps(self.to_dict(), ensure_ascii=False, default=str)
        return f"event: {self.type}\ndata: {payload}\n\n"

    def __str__(self) -> str:  # 调试用
        return f"<Event {self.type} {json.dumps(self.data, ensure_ascii=False, default=str)[:200]}>"


def new_run_id() -> str:
    return "run_" + uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# 便捷构造器：内核里到处 `Event(...)` 太啰嗦，这里给出语义化入口
# ---------------------------------------------------------------------------

def run_start(run_id: str, session_id: str, model: str, provider: str) -> Event:
    return Event(RUN_START, {"run_id": run_id, "session_id": session_id, "model": model, "provider": provider})


def run_end(run_id: str, reason: str, steps: int, ms: int) -> Event:
    return Event(RUN_END, {"run_id": run_id, "reason": reason, "steps": steps, "ms": ms})


def text(delta: str) -> Event:
    return Event(TEXT, {"delta": delta})


def thinking(delta: str) -> Event:
    return Event(THINKING, {"delta": delta})


def tool_call(call_id: str, name: str, args: Dict[str, Any], step: int) -> Event:
    return Event(TOOL_CALL, {"id": call_id, "name": name, "args": args, "step": step})


def tool_result(call_id: str, name: str, ok: bool, summary: str, ms: int, error: Optional[str] = None) -> Event:
    data: Dict[str, Any] = {"id": call_id, "name": name, "ok": ok, "summary": summary, "ms": ms}
    if error:
        data["error"] = error
    return Event(TOOL_RESULT, data)


def ui(spec: Dict[str, Any]) -> Event:
    return Event(UI, {"spec": spec})


def artifact(path: str, name: str, kind: str, url: str, size: int = 0) -> Event:
    return Event(ARTIFACT, {"path": path, "name": name, "kind": kind, "url": url, "bytes": size})


def stage(emotion: Optional[str] = None, motion: Optional[str] = None, speak: Optional[str] = None) -> Event:
    data: Dict[str, Any] = {}
    if emotion:
        data["emotion"] = emotion
    if motion:
        data["motion"] = motion
    if speak:
        data["speak"] = speak
    return Event(STAGE, data)


def notice(message: str, level: str = "info") -> Event:
    return Event(NOTICE, {"message": message, "level": level})


def error(message: str, detail: Optional[str] = None) -> Event:
    return Event(ERROR, {"message": message, "detail": detail} if detail else {"message": message})
