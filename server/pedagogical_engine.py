"""Pedagogical Engine —— 教学策略引擎（V4 决策层）

三大核心：
1. 诊断引擎（Diagnosis）   ：run_diagnosis —— 结合学生档案与知识图谱输出诊断报告
2. 规划引擎（Planning）    ：plan_next_lesson —— 推荐下一个要学习的单元
3. 自适应策略（Adaptive）  ：adapt_teaching_strategy / should_advance / get_next_action

自适应策略规则（todo.txt）：
- 连续答错 2 题   → 切换教学方式（换类比、换例子）
- 连续答对 3 题   → 推进到下一个知识点
- 掌握度 < 0.5    → 重讲 + 更多练习题
- 掌握度 > 0.8    → 跳过基础练习，直接进入应用层
"""

from typing import Any, Dict, List, Optional

from learner_model import get_learner, get_mastery, get_or_create_learner, save_learner

MASTERY_RETEACH = 0.5     # 低于此值：重讲 + 更多练习
MASTERY_ADVANCE = 0.8     # 高于此值：跳过基础练习进入应用层
WEAK_MASTERY = 0.6        # 视为掌握的最低阈值

CONSECUTIVE_WRONG_TRIGGER = 2   # 连续答错 2 题 → 切换教学方式
CONSECUTIVE_CORRECT_TRIGGER = 3  # 连续答对 3 题 → 推进


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _streak(learner: Dict[str, Any]) -> Dict[str, int]:
    """读取/初始化答题连击记录（存于 learning_behavior.answer_streak）。"""
    behavior = learner.setdefault("learning_behavior", {})
    streak = behavior.setdefault("answer_streak", {"consecutive_correct": 0, "consecutive_wrong": 0})
    streak.setdefault("consecutive_correct", 0)
    streak.setdefault("consecutive_wrong", 0)
    return streak


def record_answer_result(learner_id: str, is_correct: bool, concept_id: Optional[str] = None) -> Dict[str, int]:
    """记录一次答题结果，更新连击数并持久化。

    Returns:
        {"consecutive_correct": n, "consecutive_wrong": m}
    """
    learner = get_or_create_learner(learner_id)
    streak = _streak(learner)
    if is_correct:
        streak["consecutive_correct"] = streak.get("consecutive_correct", 0) + 1
        streak["consecutive_wrong"] = 0
    else:
        streak["consecutive_wrong"] = streak.get("consecutive_wrong", 0) + 1
        streak["consecutive_correct"] = 0
    if concept_id:
        learner["learning_behavior"]["last_concept"] = concept_id
    save_learner(learner_id, learner)
    return dict(streak)


def _context_streak(context: Dict[str, Any]) -> Dict[str, int]:
    """从上下文（优先）或学生档案读取连击状态。"""
    ctx = context or {}
    if "consecutive_correct" in ctx or "consecutive_wrong" in ctx:
        return {
            "consecutive_correct": int(ctx.get("consecutive_correct", 0)),
            "consecutive_wrong": int(ctx.get("consecutive_wrong", 0)),
        }
    learner = get_learner(context.get("learner_id"), create=False) if context else None
    if learner:
        return _streak(learner)
    return {"consecutive_correct": 0, "consecutive_wrong": 0}


# ---------------------------------------------------------------------------
# 诊断引擎
# ---------------------------------------------------------------------------
def run_diagnosis(learner_id: str, course_id: Optional[str] = None) -> Dict[str, Any]:
    """诊断引擎：输出学生知识漏洞、薄弱点分析与学习建议。"""
    learner = get_learner(learner_id, create=False)
    if not learner:
        return {"learner_id": learner_id, "weak_points": [], "suggestion": "暂无学习数据，先学习或测验吧。"}

    from knowledge_graph import get_nodes
    ks = learner.get("knowledge_state", {}) or {}
    nodes = get_nodes(course_id)
    name_map = {n.get("id"): n.get("name", n.get("id")) for n in nodes}

    weak_points = [
        {"concept_id": cid, "name": name_map.get(cid, cid), "mastery": round(float(m or 0), 4)}
        for cid, m in ks.items() if float(m or 0) < WEAK_MASTERY
    ]
    weak_points.sort(key=lambda p: p["mastery"])

    errors = learner.get("error_memory", []) or []
    error_counter: Dict[str, int] = {}
    for e in errors:
        cid = e.get("concept", "")
        if cid:
            error_counter[cid] = error_counter.get(cid, 0) + 1
    error_summary = [
        {"concept_id": cid, "name": name_map.get(cid, cid), "count": cnt}
        for cid, cnt in sorted(error_counter.items(), key=lambda kv: -kv[1])
    ]

    suggestion = (
        f"发现 {len(weak_points)} 个薄弱知识点，其中「{weak_points[0]['name']}」掌握度最低（{weak_points[0]['mastery']:.0%}），"
        f"建议优先复习并配合 {len(error_summary) or '适量'} 类高频错题专项练习。"
        if weak_points
        else "未发现明显薄弱点，可以推进新内容。"
    )
    return {
        "learner_id": learner_id,
        "course_id": course_id,
        "weak_points": weak_points,
        "error_summary": error_summary,
        "suggestion": suggestion,
    }


# ---------------------------------------------------------------------------
# 规划引擎
# ---------------------------------------------------------------------------
def plan_next_lesson(learner_id: str, course_id: Optional[str] = None) -> Dict[str, Any]:
    """规划引擎：根据学生当前状态推荐下一个单元（知识点）。"""
    from knowledge_graph import get_nodes
    learner = get_learner(learner_id, create=False)
    ks = (learner or {}).get("knowledge_state", {}) or {}
    nodes = get_nodes(course_id)
    if not nodes:
        return {"recommended": None, "reason": "课程尚无知识图谱，请先备课。"}

    # 推荐规则：选择「前置已具备」且「自身未掌握」的节点中难度最低者
    candidates = []
    for node in nodes:
        cid = node.get("id")
        mastery = float(ks.get(cid, 0) or 0)
        prereqs = node.get("prerequisites", []) or []
        prereqs_ready = all(float(ks.get(p, 0) or 0) >= WEAK_MASTERY for p in prereqs)
        if prereqs_ready and mastery < MASTERY_ADVANCE:
            candidates.append({"node": node, "mastery": mastery})
    if not candidates:
        return {
            "recommended": None,
            "reason": "所有知识点前置都已具备且掌握度达标，可以进入综合应用/总复习。",
        }
    best = min(candidates, key=lambda c: c["mastery"])
    node = best["node"]
    return {
        "recommended": {
            "concept_id": node.get("id"),
            "name": node.get("name"),
            "difficulty": node.get("difficulty"),
            "estimated_time": node.get("estimated_time"),
            "current_mastery": round(best["mastery"], 4),
        },
        "reason": (
            f"「{node.get('name')}」的前置已掌握，而当前掌握度仅 {best['mastery']:.0%}，"
            f"建议作为下一课。"
        ),
    }


# ---------------------------------------------------------------------------
# 自适应策略
# ---------------------------------------------------------------------------
def adapt_teaching_strategy(learner_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """根据学生最近答题表现与掌握度，返回教学策略建议。

    context 建议字段：
      learner_id / concept_id / consecutive_correct / consecutive_wrong /
      recent_results: [bool]（最近答题对错）
    """
    concept_id = (context or {}).get("concept_id")
    mastery = get_mastery(learner_id, concept_id) if concept_id else None
    ctx = dict(context or {})
    ctx.setdefault("learner_id", learner_id)
    streak = _context_streak(ctx)
    cc = streak["consecutive_correct"]
    cw = streak["consecutive_wrong"]

    # 规则 1：连续答错 ≥ 2 → 切换教学方式
    if cw >= CONSECUTIVE_WRONG_TRIGGER:
        return {
            "strategy": "switch_method",
            "action": "reteach_with_analogy",
            "message": "连续答错，建议切换教学方式：换一种类比/换一组例子重新讲解，再给 1~2 道同型巩固题。",
            "data": {"consecutive_wrong": cw},
        }

    # 规则 2：连续答对 ≥ 3 → 推进到下一个知识点
    if cc >= CONSECUTIVE_CORRECT_TRIGGER:
        return {
            "strategy": "advance",
            "action": "advance_to_next_concept",
            "message": f"已连续答对 {cc} 题，掌握扎实，可以推进到下一个知识点。",
            "data": {"consecutive_correct": cc},
        }

    # 规则 3：掌握度 > 0.8 → 跳过基础练习进入应用层
    if mastery is not None and mastery > MASTERY_ADVANCE:
        return {
            "strategy": "advance_application",
            "action": "skip_basic_to_application",
            "message": f"掌握度已达 {mastery:.0%}，跳过基础练习，直接进入应用/综合题。",
            "data": {"mastery": mastery},
        }

    # 规则 4：掌握度 < 0.5 → 重讲 + 更多练习
    if mastery is not None and mastery < MASTERY_RETEACH:
        return {
            "strategy": "reteach",
            "action": "reteach_with_practice",
            "message": f"掌握度仅 {mastery:.0%}，建议重讲核心概念并增加练习量。",
            "data": {"mastery": mastery},
        }

    # 默认：继续巩固
    return {
        "strategy": "consolidate",
        "action": "practice_more",
        "message": "当前状态平稳，继续做几道练习题巩固。",
        "data": {"consecutive_correct": cc, "consecutive_wrong": cw},
    }


def should_advance(learner_id: str, concept_id: str) -> bool:
    """判断是否应该推进到下一个概念。"""
    mastery = get_mastery(learner_id, concept_id)
    if mastery > MASTERY_ADVANCE:
        return True
    learner = get_learner(learner_id, create=False)
    if learner:
        streak = _streak(learner)
        if streak["consecutive_correct"] >= CONSECUTIVE_CORRECT_TRIGGER:
            return True
    return False


def get_next_action(learner_id: str, current_state: Dict[str, Any]) -> Dict[str, Any]:
    """获取下一步教学动作：诊断 → 策略 → 动作 的复合输出。"""
    concept_id = (current_state or {}).get("concept_id")
    strategy = adapt_teaching_strategy(learner_id, current_state)
    diagnosis = run_diagnosis(learner_id, current_state.get("course_id")) if concept_id is None else None

    next_action = {
        "strategy": strategy["strategy"],
        "action": strategy["action"],
        "message": strategy["message"],
    }
    if diagnosis and diagnosis.get("weak_points"):
        next_action["weak_points"] = diagnosis["weak_points"]
    return next_action


if __name__ == "__main__":
    # 冒烟测试
    tid = "test_ped"
    # 场景 1：连续答错 2 题 → 切换教学方式
    record_answer_result(tid, False, "KG001")
    record_answer_result(tid, False, "KG001")
    s1 = adapt_teaching_strategy(tid, {"concept_id": "KG001"})
    assert s1["strategy"] == "switch_method", s1
    # 场景 2：连续答对 3 题 → 推进
    record_answer_result(tid, True, "KG001")
    record_answer_result(tid, True, "KG001")
    record_answer_result(tid, True, "KG001")
    s2 = adapt_teaching_strategy(tid, {"concept_id": "KG001"})
    assert s2["strategy"] == "advance", s2
    # 场景 3：掌握度 > 0.8 → 跳过基础练习
    from learner_model import update_knowledge_state
    update_knowledge_state(tid, "KG002", 0.9)
    s3 = adapt_teaching_strategy(tid, {"concept_id": "KG002", "consecutive_correct": 0, "consecutive_wrong": 0})
    assert s3["strategy"] == "advance_application", s3
    # 场景 4：掌握度 < 0.5 → 重讲 + 更多练习
    update_knowledge_state(tid, "KG003", 0.3)
    s4 = adapt_teaching_strategy(tid, {"concept_id": "KG003", "consecutive_correct": 0, "consecutive_wrong": 0})
    assert s4["strategy"] == "reteach", s4
    print("Pedagogical Engine 冒烟测试通过")
    # 清理
    import os
    from learner_model import learner_path
    p = learner_path(tid)
    if p.exists():
        p.unlink()
