"""Examiner Agent —— 出题 Agent。

复用现有测验模块能力：根据学生薄弱知识点，推荐出题范围并生成针对性练习题。
本 Agent 返回出题建议（结构 + 文本），实际出题仍走现有 /api/exam/generate。
"""

from typing import Any, Dict, Optional

from .base_agent import BaseAgent
from knowledge_graph import get_nodes
from learner_model import get_learner

WEAK_THRESHOLD = 0.5


class ExaminerAgent(BaseAgent):
    name = "examiner"
    description = "出题 Agent（针对薄弱点生成测验建议）"

    def run(self, user_input: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        learner = get_learner(self.learner_id, create=False)
        nodes = get_nodes(self.lesson_folder)

        # 收集薄弱知识点作为出题重点
        focus: List[str] = []
        if learner:
            ks = learner.get("knowledge_state", {}) or {}
            focus = [
                cid for cid, m in ks.items() if float(m or 0) < WEAK_THRESHOLD
            ]
        # 没有薄弱点/无档案时，用课程前几个知识点兜底
        if not focus and nodes:
            focus = [n.get("id") for n in nodes[:3]]

        names = {n.get("id"): n.get("name", n.get("id")) for n in nodes}
        focus_names = [names.get(cid, cid) for cid in focus]

        if not focus_names:
            return self._fail("暂无可出题的知识点，先备课或学习几课再说吧。")

        reply = (
            "好的，我可以针对这些薄弱知识点出题：\n"
            + "\n".join(f"- {n}" for n in focus_names)
            + "\n\n你可以在上方测验入口发起随堂测验，我会优先考察这些内容。"
        )
        return self.done(reply, data={"focus_concepts": focus, "focus_names": focus_names})
