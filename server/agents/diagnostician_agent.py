"""Diagnostician Agent —— 诊断 Agent。

基于 learner_model（掌握度 / 错题 / 测验记录）+ knowledge_graph（知识点关系）
输出学生知识漏洞与薄弱点分析报告。
"""

from typing import Any, Dict, List, Optional

from .base_agent import BaseAgent
from knowledge_graph import get_nodes
from learner_model import get_learner

WEAK_THRESHOLD = 0.5


class DiagnosticianAgent(BaseAgent):
    name = "diagnostician"
    description = "诊断 Agent（分析薄弱点、知识漏洞）"

    def _build_report(self) -> Dict[str, Any]:
        learner = get_learner(self.learner_id, create=False)
        if not learner:
            return {"weak_points": [], "error_summary": [], "suggestion": "暂无学习数据"}

        ks = learner.get("knowledge_state", {}) or {}
        nodes = get_nodes(self.lesson_folder)
        name_map = {n.get("id"): n.get("name", n.get("id")) for n in nodes}

        # 1) 薄弱知识点：掌握度 < 阈值
        weak_points: List[Dict[str, Any]] = []
        for cid, mastery in ks.items():
            if float(mastery or 0) < WEAK_THRESHOLD:
                weak_points.append({
                    "concept_id": cid,
                    "name": name_map.get(cid, cid),
                    "mastery": round(float(mastery or 0), 4),
                })
        weak_points.sort(key=lambda p: p["mastery"])

        # 2) 错题汇总：按知识点聚合
        errors = learner.get("error_memory", []) or []
        error_counter: Dict[str, int] = {}
        for e in errors:
            cid = e.get("concept", "")
            if cid:
                error_counter[cid] = error_counter.get(cid, 0) + 1
        error_summary = [
            {
                "concept_id": cid,
                "name": name_map.get(cid, cid),
                "count": cnt,
            }
            for cid, cnt in sorted(error_counter.items(), key=lambda kv: -kv[1])
        ]

        # 3) 测验趋势
        history = learner.get("assessment_history", []) or []
        trend = [round(float(h.get("score", 0) or 0), 1) for h in history[-10:]]

        suggestion = (
            f"共发现 {len(weak_points)} 个薄弱知识点、{len(error_summary)} 类高频错误。"
            f"建议优先复习掌握度最低的知识点，并针对高频错题做专项练习。"
            if weak_points or error_summary
            else "未发现明显薄弱点，可以推进新内容。"
        )
        return {
            "weak_points": weak_points,
            "error_summary": error_summary,
            "score_trend": trend,
            "suggestion": suggestion,
        }

    def run(self, user_input: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.learner_id:
            return self._fail("需要先绑定学生身份（learner_id）才能进行诊断。")

        report = self._build_report()
        if not report["weak_points"] and not report["error_summary"]:
            return self.done(report["suggestion"], data=report)

        lines = ["我来帮你分析薄弱点："]
        if report["weak_points"]:
            lines.append("\n**薄弱知识点**：")
            for p in report["weak_points"][:8]:
                lines.append(f"- {p['name']}（掌握度 {p['mastery']:.0%}）")
        if report["error_summary"]:
            lines.append("\n**高频错题**：")
            for e in report["error_summary"][:5]:
                lines.append(f"- {e['name']}（出错 {e['count']} 次）")
        lines.append(f"\n💡 {report['suggestion']}")
        return self.done("\n".join(lines), data=report)
