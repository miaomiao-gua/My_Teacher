"""Tutor Agent —— 教学讲解 Agent。

按 todo.txt 要求复用现有 /api/chat 流程：本 Agent 不做独立响应，
返回 fallback_to_chat=True，由主聊天流程（含课程上下文、工具调用、口型同步等）接管。
"""

from typing import Any, Dict, Optional

from .base_agent import BaseAgent


class TutorAgent(BaseAgent):
    name = "tutor"
    description = "教学讲解 Agent（讲解概念、答疑、举例）"

    def run(self, user_input: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # 讲解/答疑类请求直接交给主聊天流程，保证课程上下文与现有交互体验完整
        return self._fallback()
