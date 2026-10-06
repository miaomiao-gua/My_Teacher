"""归档工具：让模型在**聊天过程中**把这段对话放进正确文件夹。

为什么做成工具而不是纯后台判断：模型本来就在读这段对话，
它比事后的一次小调用更清楚「这是数学 9709 的三角函数」。
用户只管聊天，归档由它顺手做掉 —— 不用右键、不用拖拽。
"""

from __future__ import annotations

from typing import Any, Dict

from ..filing import apply_filing, existing_folders
from ..registry import ToolContext, ToolResult, tool


@tool(
    "file_conversation",
    "把**当前这段对话**归到自己该在的文件夹（左栏的 学科 → 教材 → 对话 三级）。"
    "当你从聊天里看出这是哪门课、哪本教材时**顺手调用一次**，学生就不用自己建文件夹了。"
    "已经归好档、或者学生自己改过位置的对话，不要再调用。",
    {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "学科，如 数学 / 物理 / 语文 / 化学（不确定就写 未分类）"},
            "textbook": {"type": "string", "description": "教材或考试名，如 9709 / 人教版必修一；不确定写 未指定教材，**不要编**"},
            "reason": {"type": "string", "description": "一句话理由（会记在会话里，方便以后核对）"},
        },
        "required": ["subject"],
    },
)
async def file_conversation(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    session = ctx.session
    subject = str(args.get("subject") or "").strip()
    textbook = str(args.get("textbook") or "").strip()
    reason = str(args.get("reason") or "").strip()
    if not subject:
        return ToolResult.fail("至少要给 subject（学科），如 数学")

    already = (getattr(session, "subject", "") or "").strip()
    by = (getattr(session, "meta", None) or {}).get("filed_by")
    if by == "user" and already:
        return ToolResult(
            summary=(f"这段对话学生自己放在「{already} / {session.textbook}」，"
                     "尊重他的选择，就不改了。"),
            data={"subject": already, "textbook": session.textbook, "changed": False,
                  "skipped": "user_filed"},
        )

    changed = apply_filing(session, subject, textbook, by="model", reason=reason)
    where = f"{session.subject} / {session.textbook}"
    if not changed:
        return ToolResult(
            summary=f"已经在「{where}」了，不用重复归档。",
            data={"subject": session.subject, "textbook": session.textbook, "changed": False},
        )

    folders = [f"{f['subject']} / {f['textbook'] or '未指定教材'}" for f in existing_folders()][:10]
    return ToolResult(
        summary=(f"已把这段对话归档到「{where}」（左栏里就能看到）。"
                 f"不用跟学生特意说这件事，继续讲课即可。"
                 + (f"当前文件夹：{'、'.join(folders)}" if folders else "")),
        data={"subject": session.subject, "textbook": session.textbook, "changed": True,
              "reason": reason},
    )
