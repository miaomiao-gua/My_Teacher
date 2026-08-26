"""Evaluator Agent —— 评估 Agent。

计算学习增益、学习效率与 ROI，输出阶段学习效果报告。
"""

from typing import Any, Dict, Optional

from .base_agent import BaseAgent
from learner_model import compute_learning_gain, get_average_mastery, get_learner


class EvaluatorAgent(BaseAgent):
    name = "evaluator"
    description = "评估 Agent（学习增益 / 效率 / ROI）"

    def _compute(self) -> Dict[str, Any]:
        learner = get_learner(self.learner_id, create=False)
        if not learner:
            return {"report": "暂无学习数据，先完成几次测验或学习后再评估。"}

        history = learner.get("assessment_history", []) or []
        scores = [float(h.get("score", 0) or 0) for h in history]
        if len(scores) < 2:
            avg = round(sum(scores) / len(scores), 1) if scores else 0.0
            return {
                "report": f"目前有 {len(scores)} 次测验记录，平均分 {avg}。"
                f"再完成几次测验后，我就能为你生成增益与效率报告。",
                "average_score": avg,
            }

        pre = scores[0]
        post = scores[-1]
        gain = compute_learning_gain(self.learner_id, pre, post)
        # 用「平均专注时长 x 测验次数」近似时间投入
        behavior = learner.get("learning_behavior", {}) or {}
        time_spent = float(behavior.get("avg_focus_duration", 20) or 20) * len(scores) / 60.0
        efficiency = round(gain / time_spent, 3) if time_spent > 0 else 0.0

        mastery = get_average_mastery(self.learner_id)
        report = (
            f"学习效果评估：\n"
            f"- 首测 {pre:.0f} 分 → 最近一次 {post:.0f} 分\n"
            f"- 学习增益：{gain:+.1f} 分\n"
            f"- 学习效率：{efficiency:.2f} 分/小时\n"
            f"- 知识点平均掌握度：{mastery:.0%}"
        )
        return {
            "report": report,
            "pre_score": pre,
            "post_score": post,
            "gain": round(gain, 1),
            "efficiency": efficiency,
            "average_mastery": mastery,
            "assessment_count": len(scores),
        }

    def run(self, user_input: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.learner_id:
            return self._fail("需要先绑定学生身份（learner_id）才能评估学习效果。")
        result = self._compute()
        return self.done(result.pop("report", ""), data=result)
