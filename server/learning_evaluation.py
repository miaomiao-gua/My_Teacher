"""Learning Evaluation —— 学习效果评估闭环（V4 指标层）

核心指标：
- 学习增益（gain）        = 后测 - 前测
- 学习效率（efficiency）  = 增益 / 时间
- 学习 ROI                = 增益 / (时间 * 努力程度)

并基于 learner_model 提供进度曲线、增益报告与学习洞察的生成。
"""

from typing import Any, Dict, List, Optional

from learner_model import get_average_mastery, get_learner


def compute_learning_gain(pre_score: float, post_score: float) -> float:
    """计算学习增益 = 后测 - 前测（分数制 0~100）。"""
    return float(post_score) - float(pre_score)


def compute_learning_efficiency(gain: float, time_spent: float) -> float:
    """计算学习效率 = 增益 / 时间（小时）。"""
    return gain / time_spent if time_spent > 0 else 0.0


def compute_learning_roi(gain: float, time_spent: float, effort: float) -> float:
    """计算学习 ROI = 增益 / (时间 * 努力程度)。"""
    return gain / (time_spent * effort) if time_spent * effort > 0 else 0.0


# ---------------------------------------------------------------------------
# 进度曲线
# ---------------------------------------------------------------------------
def get_progress_curve(learner_id: str) -> Dict[str, Any]:
    """学习进度曲线：从测验历史提取分数序列 + 知识点平均掌握度变化。"""
    learner = get_learner(learner_id, create=False)
    if not learner:
        return {"learner_id": learner_id, "score_curve": [], "mastery_curve": [], "assessment_count": 0}

    history = learner.get("assessment_history", []) or []
    score_curve = [
        {
            "timestamp": h.get("timestamp", ""),
            "score": round(float(h.get("score", 0) or 0), 1),
            "correct": h.get("correct"),
            "total": h.get("total"),
        }
        for h in history
    ]
    # 知识点平均掌握度：取 knowledge_state 中已有概念的平均
    mastery_curve: List[Dict[str, Any]] = []
    ks = learner.get("knowledge_state", {}) or {}
    if ks:
        mastery_curve = [
            {"timestamp": learner.get("updated_at", ""), "average_mastery": round(sum(ks.values()) / len(ks), 4)}
        ]
    return {
        "learner_id": learner_id,
        "score_curve": score_curve,
        "mastery_curve": mastery_curve,
        "assessment_count": len(score_curve),
        "average_mastery": round(sum(ks.values()) / len(ks), 4) if ks else 0.0,
    }


# ---------------------------------------------------------------------------
# 增益报告
# ---------------------------------------------------------------------------
def get_gain_report(learner_id: str, time_spent_hours: Optional[float] = None, effort: float = 1.0) -> Dict[str, Any]:
    """学习增益报告：前测/后测/增益/效率/ROI + 知识点掌握度变化。"""
    curve = get_progress_curve(learner_id)
    scores = [p["score"] for p in curve["score_curve"]]

    if len(scores) < 2:
        return {
            "learner_id": learner_id,
            "pre_score": scores[0] if scores else None,
            "post_score": scores[-1] if scores else None,
            "gain": 0.0,
            "efficiency": 0.0,
            "roi": 0.0,
            "message": "测验次数不足，至少需要 2 次测验才能生成增益报告。",
            "average_mastery": curve["average_mastery"],
        }

    pre = scores[0]
    post = scores[-1]
    gain = compute_learning_gain(pre, post)
    if time_spent_hours is None:
        # 无显式时间时，按测验次数 × 单次 0.5 小时估算
        time_spent_hours = len(scores) * 0.5
    efficiency = compute_learning_efficiency(gain, time_spent_hours)
    roi = compute_learning_roi(gain, time_spent_hours, effort)

    # 知识点掌握度变化（按掌握度从低到高排列薄弱项）
    learner = get_learner(learner_id, create=False)
    ks = (learner or {}).get("knowledge_state", {}) or {}
    mastery_change = [{"concept_id": cid, "mastery": round(float(m or 0), 4)} for cid, m in ks.items()]

    return {
        "learner_id": learner_id,
        "pre_score": pre,
        "post_score": post,
        "gain": round(gain, 1),
        "efficiency": round(efficiency, 3),
        "roi": round(roi, 3),
        "time_spent_hours": round(time_spent_hours, 2),
        "assessment_count": len(scores),
        "average_mastery": curve["average_mastery"],
        "mastery_state": sorted(mastery_change, key=lambda m: m["mastery"])[:10],
        "message": f"首测 {pre:.0f} 分 → 最近 {post:.0f} 分，学习增益 {gain:+.1f} 分。",
    }


# ---------------------------------------------------------------------------
# 学习洞察
# ---------------------------------------------------------------------------
def generate_insights(learner_id: str) -> Dict[str, Any]:
    """AI 学习洞察：基于学习行为 + 测验数据给出可执行建议（不依赖 LLM 的规则洞察）。"""
    learner = get_learner(learner_id, create=False)
    if not learner:
        return {"learner_id": learner_id, "insights": [], "summary": "暂无学习数据。"}

    insights: List[str] = []
    behavior = learner.get("learning_behavior", {}) or {}

    # 1) 答题连击洞察
    streak = behavior.get("answer_streak", {}) or {}
    if streak.get("consecutive_wrong", 0) >= 2:
        insights.append(f"近期连续答错 {streak['consecutive_wrong']} 题，建议暂停推进，回顾错题并换一种方式重新理解。")
    elif streak.get("consecutive_correct", 0) >= 3:
        insights.append(f"已连续答对 {streak['consecutive_correct']} 题，状态很好，适合挑战下一个知识点。")

    # 2) 最佳学习时段
    optimal = behavior.get("optimal_time", "")
    if optimal:
        insights.append(f"你的最佳学习时段是「{optimal}」，把最难的内容安排在这个时段效率更高。")

    # 3) 高频错误
    error_counter: Dict[str, int] = {}
    for e in learner.get("error_memory", []) or []:
        cid = e.get("concept", "")
        if cid:
            error_counter[cid] = error_counter.get(cid, 0) + 1
    if error_counter:
        top = sorted(error_counter.items(), key=lambda kv: -kv[1])[0]
        insights.append(f"「{top[0]}」是高频错点（{top[1]} 次），建议本周内安排一次专项复习。")

    # 4) 遗忘率 / 专注度
    forgetting = behavior.get("forgetting_rate", "")
    if forgetting == "快":
        insights.append("你的遗忘速度偏快，建议采用间隔复习（学完 1 天、3 天、7 天各复习一次）。")
    focus = behavior.get("avg_focus_duration", 0)
    if focus and focus <= 15:
        insights.append(f"平均专注时长约 {focus} 分钟，可尝试番茄工作法提升效率。")

    if not insights:
        insights.append("保持当前学习节奏，定期回顾错题本，稳步推进即可。")

    return {"learner_id": learner_id, "insights": insights, "summary": f"共生成 {len(insights)} 条学习洞察。"}


if __name__ == "__main__":
    # 冒烟测试
    from learner_model import add_assessment_record
    tid = "test_eval"
    add_assessment_record(tid, {"score": 50, "concepts": ["KG001"], "total": 4, "correct": 2})
    add_assessment_record(tid, {"score": 82, "concepts": ["KG001"], "total": 5, "correct": 4})
    assert compute_learning_gain(50, 82) == 32
    assert compute_learning_efficiency(32, 4) == 8
    assert compute_learning_roi(32, 4, 2) == 4
    report = get_gain_report(tid)
    assert report["gain"] == 32.0, report
    curve = get_progress_curve(tid)
    assert len(curve["score_curve"]) == 2
    insights = generate_insights(tid)
    assert len(insights["insights"]) >= 1
    print("Learning Evaluation 冒烟测试通过")
    from learner_model import learner_path
    p = learner_path(tid)
    if p.exists():
        p.unlink()
