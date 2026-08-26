"""Knowledge Graph —— 知识图谱（V4 知识表示层）

负责知识点的表示、知识点间关系检索、知识漏洞检测与学习路径推荐。

知识图谱文件存储在 lessons/{课程文件夹}/knowledge_graph.json：
{
  "nodes": [ { "id", "name", "description", "prerequisites", "concepts", "skills", "tags", "difficulty", "estimated_time", "related_quiz_questions" } ],
  "edges": [ { "from", "to", "type", "strength" } ]   # type: prerequisite / related / sub_concept
}
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from learner_model import get_learner, get_mastery

# 默认掌握度阈值
MASTERY_OK = 0.6     # 达到此值视为已掌握
MASTERY_WEAK = 0.4   # 低于此值视为薄弱
_GAP_THRESHOLD = 0.6


def _course_dir(lesson_folder: str | None) -> Path:
    base = Path(__file__).resolve().parent / "lessons"
    return base / lesson_folder if lesson_folder else base


def knowledge_graph_path(lesson_folder: str | None) -> Path:
    return _course_dir(lesson_folder) / "knowledge_graph.json"


def load_knowledge_graph(lesson_folder: str | None) -> Dict[str, Any]:
    """从课程文件夹加载知识图谱；不存在时返回空图谱。"""
    path = knowledge_graph_path(lesson_folder)
    if not path.exists():
        return {"nodes": [], "edges": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"nodes": [], "edges": []}
    data.setdefault("nodes", [])
    data.setdefault("edges", [])
    return data


def save_knowledge_graph(lesson_folder: str | None, graph: Dict[str, Any]) -> Dict[str, Any]:
    """保存知识图谱到课程目录。"""
    path = knowledge_graph_path(lesson_folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
    return graph


def get_node(node_id: str, lesson_folder: str | None = None) -> Optional[Dict[str, Any]]:
    """获取单个知识点。"""
    graph = load_knowledge_graph(lesson_folder)
    for node in graph["nodes"]:
        if node.get("id") == node_id:
            return node
    return None


def get_nodes(lesson_folder: str | None = None) -> List[Dict[str, Any]]:
    return load_knowledge_graph(lesson_folder)["nodes"]


def get_prerequisites(node_id: str, lesson_folder: str | None = None, _visited: Optional[set] = None) -> List[str]:
    """获取某知识点的所有前置知识点（递归展开，含间接前置）。"""
    graph = load_knowledge_graph(lesson_folder)
    _visited = _visited or set()
    if node_id in _visited:
        return []
    _visited.add(node_id)

    results: List[str] = []
    for edge in graph["edges"]:
        if edge.get("to") == node_id and edge.get("type") == "prerequisite":
            pre = edge.get("from")
            if pre and pre not in _visited and pre != node_id:
                results.append(pre)
                results.extend(get_prerequisites(pre, lesson_folder, _visited))
    return results


def get_descendants(node_id: str, lesson_folder: str | None = None) -> List[str]:
    """获取依赖 node_id 的全部后继知识点（用于判断影响范围）。"""
    graph = load_knowledge_graph(lesson_folder)
    results: List[str] = []
    frontier = [node_id]
    while frontier:
        cur = frontier.pop()
        for edge in graph["edges"]:
            if edge.get("from") == cur and edge.get("type") == "prerequisite":
                nxt = edge.get("to")
                if nxt and nxt not in results and nxt != node_id:
                    results.append(nxt)
                    frontier.append(nxt)
    return results


def find_gaps(learner_knowledge: Dict[str, float], target_knowledge: List[str]) -> List[Dict[str, Any]]:
    """找出学生知识点与目标之间的差距。

    返回：[{ "concept_id", "current_mastery", "gap" }]，gap = 阈值 - 当前掌握度（>0 表示有差距）
    """
    gaps: List[Dict[str, Any]] = []
    for cid in target_knowledge or []:
        cur = float(learner_knowledge.get(cid, 0.0) or 0.0)
        gap = round(MASTERY_OK - cur, 4)
        if gap > 0:
            gaps.append({"concept_id": cid, "current_mastery": cur, "gap": gap})
    return sorted(gaps, key=lambda g: -g["gap"])


def generate_learning_path(learner_id: str, target_node_id: str, lesson_folder: str | None = None) -> Dict[str, Any]:
    """根据学生当前状态生成学习路径。

    逻辑：
    1. 递归收集 target 的全部前置知识点；
    2. 结合学生掌握度，把「薄弱的前置」排在前面；
    3. 最后才是目标知识点本身。
    """
    target_node = get_node(target_node_id, lesson_folder)
    if not target_node:
        return {"target": target_node_id, "path": [], "message": "目标知识点不存在"}

    prereqs = get_prerequisites(target_node_id, lesson_folder)
    nodes_map = {n.get("id"): n for n in get_nodes(lesson_folder)}

    # 合并目标本身，去重且保持前置优先
    all_needed: List[str] = []
    for cid in prereqs + [target_node_id]:
        if cid not in all_needed:
            all_needed.append(cid)

    # 已掌握的直接跳过；薄弱项按缺口从大到小排序
    pending: List[Dict[str, Any]] = []
    for cid in all_needed:
        mastery = get_mastery(learner_id, cid) if learner_id else 0.0
        if mastery >= MASTERY_OK:
            continue
        node = nodes_map.get(cid, {})
        pending.append({
            "concept_id": cid,
            "name": node.get("name", cid),
            "current_mastery": round(mastery, 4),
            "difficulty": node.get("difficulty", 0.5),
            "estimated_time": node.get("estimated_time", 15),
        })
    pending.sort(key=lambda p: (-p["current_mastery"], p["difficulty"]))

    return {
        "target": target_node_id,
        "target_name": target_node.get("name", target_node_id),
        "path": pending,
        "total_nodes": len(pending),
        "message": f"共需学习 {len(pending)} 个知识点" if pending else "已具备所需前置知识，可直接学习目标知识点",
    }


def compute_path_progress(learner_id: str, path: List[Dict[str, Any]]) -> float:
    """学习路径整体进度（0~1）：路径上各知识点掌握度的平均值。"""
    if not path:
        return 1.0
    total = 0.0
    for item in path:
        total += get_mastery(learner_id, item["concept_id"]) if learner_id else 0.0
    return round(total / len(path), 4)


if __name__ == "__main__":
    # 冒烟测试：构造内存图谱验证核心逻辑
    graph = {
        "nodes": [
            {"id": "KG001", "name": "Python 语法", "prerequisites": [], "difficulty": 0.2},
            {"id": "KG002", "name": "函数", "prerequisites": ["KG001"], "difficulty": 0.4},
            {"id": "KG003", "name": "面向对象", "prerequisites": ["KG002"], "difficulty": 0.6},
        ],
        "edges": [
            {"from": "KG001", "to": "KG002", "type": "prerequisite", "strength": 0.9},
            {"from": "KG002", "to": "KG003", "type": "prerequisite", "strength": 0.9},
        ],
    }
    save_knowledge_graph("_kg_smoke", graph)
    prereqs = get_prerequisites("KG003", "_kg_smoke")
    assert set(prereqs) == {"KG001", "KG002"}, prereqs
    gaps = find_gaps({"KG001": 0.8, "KG002": 0.3}, ["KG001", "KG002", "KG003"])
    assert [g["concept_id"] for g in gaps] == ["KG003", "KG002"], gaps
    from pathlib import Path as _P
    kg_path = knowledge_graph_path("_kg_smoke")
    if kg_path.exists():
        kg_path.unlink()
    if kg_path.parent.exists():
        kg_path.parent.rmdir()
    print("Knowledge Graph 冒烟测试通过")
