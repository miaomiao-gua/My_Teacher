"""Coach Agent —— 教练 Agent。

关注学习动力、情绪与行为管理：结合学生档案中的学习行为与近期表现，
给出激励性、可执行的学习建议。
"""

from typing import Any, Dict, Optional

from .base_agent import BaseAgent
from learner_model import get_learner


class CoachAgent(BaseAgent):
    name = "coach"
    description = "教练 Agent（学习动力 / 情绪疏导 / 行为建议）"

    def run(self, user_input: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        learner = get_learner(self.learner_id, create=False)

        hints: list[str] = []
        if learner:
            behavior = learner.get("learning_behavior", {}) or {}
            optimal = behavior.get("optimal_time", "")
            if optimal:
                hints.append(f"你的最佳学习时段是「{optimal}」，建议把最难的内容安排在这个时段。")
            focus = behavior.get("avg_focus_duration", 20)
            if focus and focus <= 15:
                hints.append("你的平均专注时长较短，可以试试 20 分钟专注 + 5 分钟休息的番茄钟节奏。")
            errors = behavior.get("error_types", []) or []
            if errors:
                hints.append(f"近期高频出错的知识点：{'、'.join(errors[:3])}，犯错不可怕，错题就是提分地图。")

        if not hints:
            hints.append("保持节奏比冲刺更重要：每天固定学一小段，积累下来就很可观。")

        reply = (
            "别着急，学习是一场长跑。\n"
            + "\n".join(hints)
            + "\n\n需要的话，随时叫我帮你拆分任务、安排复习。今天也一起加油！"
        )
        return self.done(reply, data={"hints": hints})
