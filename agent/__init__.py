"""My_Teacher Agent 内核。

一个与 Web 框架无关的 Agent 运行时：

    LLM（原生 function calling） ⇄ 工具注册表 ⇄ 事件流 → 前端

设计约定
--------
1. **内核不认识 Flask，也不认识 FastAPI。** 传输层只是薄壳（`agent/server.py`），
   换成 WebSocket、CLI 或桌面端都不需要改内核。
2. **一次 run = 一串有序事件**（见 `events.py`）。前端只消费事件，不猜内核行为。
3. **每个能力都是一个注册的工具**（见 `registry.py`）。内置工具与插件工具同表，
   模型看不出区别——这是「复杂功能调试成熟后固化为插件」的基础。
4. **工具的返回值分两层**：`summary` 回灌给模型（必须短），
   `data` / `ui` / `artifacts` 推给前端（可以很丰富）。多轮工具循环的上下文成本靠这一条控制。
"""

__version__ = "0.1.0"

from .events import Event
from .registry import REGISTRY, Tool, ToolContext, ToolResult, tool
from .runtime import AgentRuntime
from .session import Session, SessionStore

__all__ = [
    "__version__",
    "Event",
    "REGISTRY",
    "Tool",
    "ToolContext",
    "ToolResult",
    "tool",
    "AgentRuntime",
    "Session",
    "SessionStore",
]
