"""提醒工具：让模型能说「我 30 分钟后提醒你复习」，桌宠到点会冒一句话。

为什么不塞进桌宠里做：定提醒的有两个人 —— 模型（讲课时顺口约好）和学生自己
（桌宠右键菜单）。两者写同一份数据（`data/reminders.json`），到点由服务端发到
事件总线上，桌宠只负责显示。这样即使桌宠关着定了提醒，下次打开也不会丢。
"""

from __future__ import annotations

import time
from typing import Any, Dict

from ..pet_bus import add_reminder, load_reminders, pending_reminders
from ..registry import ToolContext, ToolResult, tool


def _fmt_eta(due: float) -> str:
    left = max(0, int(due - time.time()))
    if left >= 3600:
        return f"{left // 3600} 小时 {left % 3600 // 60} 分钟后"
    if left >= 60:
        return f"{left // 60} 分钟后"
    return f"{left} 秒后"


@tool(
    "set_reminder",
    "给自己/学生定一条提醒（到点桌宠会冒一句话说出来）。"
    "适合：约好「10 分钟后做这道题」、提醒复习、提醒休息。"
    "**不要**用它代替正常回答；只是约时间。",
    {
        "type": "object",
        "properties": {
            "minutes": {"type": "number", "description": "多少分钟后提醒（1~1440）"},
            "text": {"type": "string", "description": "提醒内容，一句话，如「回来做第三题」"},
        },
        "required": ["minutes"],
    },
)
async def set_reminder(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    try:
        minutes = float(args.get("minutes") or 0)
    except Exception:
        return ToolResult.fail("minutes 必须是数字，例如 10")
    if not (1 <= minutes <= 24 * 60):
        return ToolResult.fail("minutes 要在 1~1440（24 小时）之间")
    text = str(args.get("text") or "").strip() or "该复习啦"
    item = add_reminder(minutes, text, source="model")
    return ToolResult(
        summary=(
            f"提醒定好了：{_fmt_eta(item['due'])}提醒「{item['text']}」。"
            "（桌宠到点会冒出来说；你正常继续讲课就行，不用再提这件事。）"
        ),
        data={"due": item["due"], "text": item["text"], "id": item["id"]},
    )


@tool(
    "list_reminders",
    "看看还有哪些提醒没到点（学生问「你刚才说要提醒我什么」时用）",
    {"type": "object", "properties": {}},
)
async def list_reminders(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    items = pending_reminders()
    if not items:
        done = [x for x in load_reminders() if x.get("done")]
        tail = f"（历史上已经提醒过 {len(done)} 次）" if done else ""
        return ToolResult(summary="现在没有待提醒的事。" + tail, data={"pending": []})
    lines = [f"- {_fmt_eta(float(x['due']))}：{x['text']}" for x in items[:8]]
    return ToolResult(
        summary="待提醒：" + "；".join(lines),
        data={"pending": [{"due": x["due"], "text": x["text"]} for x in items]},
    )
