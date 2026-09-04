"""Planner Agent —— 课程规划 Agent。

根据学生当前掌握状态 + 知识图谱，推荐下一个要学习的知识点/单元。
复用 lesson_prep.generate_knowledge_graph 生成图谱，调用 knowledge_graph.generate_learning_path。
"""

from typing import Any, Dict, Optional

from .base_agent import BaseAgent
from knowledge_graph import generate_learning_path, get_nodes
from learner_model import get_average_mastery, get_learner


class PlannerAgent(BaseAgent):
    name = "planner"
    description = "课程规划 Agent（下一步学什么、学习路径推荐）"

    def _next_target(self) -> Optional[str]:
        """从当前课程图谱中挑一个尚未掌握的目标节点。"""
        nodes = get_nodes(self.lesson_folder)
        if not nodes:
            return None
        learner = get_learner(self.learner_id, create=False)
        ks = (learner or {}).get("knowledge_state", {}) or {}
        # 优先选掌握度最低且没有前置未掌握依赖的节点
        for node in nodes:
            cid = node.get("id")
            if not cid or cid in ks:
                continue
            prereqs_ok = all(ks.get(p, 0) >= 0.6 for p in node.get("prerequisites", []))
            if prereqs_ok:
                return cid
        # 全部已学或依赖不满足：选掌握度最低的节点
        weakest = min(nodes, key=lambda n: ks.get(n.get("id"), 0))
        return weakest.get("id")

    def run(self, user_input: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.learner_id:
            return self._fail("需要先绑定学生身份（learner_id）才能规划课程。")

        target_id = (context or {}).get("target_node_id") or self._next_target()
        if not target_id:
            return self._fail("当前课程还没有知识图谱，先完成一次备课再规划吧。")

        plan = generate_learning_path(self.learner_id, target_id, self.lesson_folder)
        if not plan.get("path"):
            reply = (
                f"你的掌握度很扎实（平均 {get_average_mastery(self.learner_id):.0%}），"
                f"前置知识已具备，可以直接学习「{plan.get('target_name', target_id)}」。"
            )
            return self.done(reply, data={"plan": plan})

        lines = [
            f"根据你的当前状态，推荐学习路径（目标：{plan.get('target_name', target_id)}）："
        ]
        for i, item in enumerate(plan["path"], 1):
            lines.append(
                f"{i}. {item.get('name', item['concept_id'])}"
                f"（当前掌握度 {item['current_mastery']:.0%}，"
                f"预计 {item.get('estimated_time', 15)} 分钟）"
            )
        reply = "\n".join(lines)
        return self.done(reply, data={"plan": plan})
