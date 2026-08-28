"""Learner Model —— 学生数字孪生（V4 核心数据层）

负责学生档案的创建、读取、更新与持久化。
每个学生一个 JSON 文件，存放在 project/lessons/learners/learner_<id>.json。

核心数据结构（LEARNER_SCHEMA）：
- knowledge_state:  知识点ID -> 掌握度 (0.0 ~ 1.0)
- cognitive_features: 认知特征（抽象推理 / 记忆保持 / 数学推理 / 动手实践）
- learning_behavior: 学习行为（最佳时段 / 平均专注时长 / 易错类型 / 遗忘率）
- error_memory:      错误记录列表
- assessment_history: 测验记录列表
- learning_goals:    学习目标（目标知识点 / 目标分数 / 截止时间）
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 学生档案存储目录：data/lessons/learners/
LEARNERS_DIR = Path(__file__).resolve().parent.parent / "data" / "lessons" / "learners"

# 写文件加锁，避免并发读写损坏档案
_LEARNER_LOCK = threading.Lock()

# 默认掌握度（新知识点）
DEFAULT_MASTERY = 0.0


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def default_learner(learner_id: str) -> Dict[str, Any]:
    """创建一份空的（默认）学生档案。"""
    return {
        "learner_id": learner_id,
        "knowledge_state": {},
        "cognitive_features": {
            "abstract_reasoning": "中",
            "memory_retention": "中",
            "math_reasoning": "中",
            "hands_on_practice": "中",
        },
        "learning_behavior": {
            "optimal_time": "上午",
            "avg_focus_duration": 20,
            "error_types": [],
            "forgetting_rate": "中",
        },
        "error_memory": [],
        "assessment_history": [],
        "learning_goals": {
            "target_concepts": [],
            "target_score": 0.0,
            "deadline": "",
        },
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }


def learner_path(learner_id: str) -> Path:
    """返回学生档案文件路径。"""
    return LEARNERS_DIR / f"learner_{learner_id}.json"


def ensure_learners_dir() -> Path:
    LEARNERS_DIR.mkdir(parents=True, exist_ok=True)
    return LEARNERS_DIR


def list_learners() -> List[str]:
    """列出所有已创建的学生 ID。"""
    if not LEARNERS_DIR.exists():
        return []
    return sorted(p.stem.replace("learner_", "") for p in LEARNERS_DIR.glob("learner_*.json"))


def get_learner(learner_id: str, create: bool = True) -> Optional[Dict[str, Any]]:
    """加载学生档案；不存在时按 create 决定是否自动创建。"""
    if not learner_id:
        return None
    path = learner_path(learner_id)
    if not path.exists():
        if not create:
            return None
        data = default_learner(learner_id)
        save_learner(learner_id, data)
        return data
    try:
        with _LEARNER_LOCK:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        # 档案损坏时回退为空档案，避免拖垮整条链路
        data = default_learner(learner_id)
        save_learner(learner_id, data)
        return data


def save_learner(learner_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """保存学生档案（原子写：先写临时文件再替换）。"""
    ensure_learners_dir()
    data.setdefault("learner_id", learner_id)
    data["updated_at"] = _now_iso()
    path = learner_path(learner_id)
    tmp = path.with_suffix(".json.tmp")
    with _LEARNER_LOCK:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    return data


def get_or_create_learner(learner_id: str) -> Dict[str, Any]:
    return get_learner(learner_id, create=True) or default_learner(learner_id)


def update_knowledge_state(learner_id: str, concept_id: str, score: float) -> Dict[str, Any]:
    """更新单个知识点掌握度（0.0 ~ 1.0）。

    采用加权平均：新证据占 60%，旧掌握度占 40%，
    使掌握度既响应最近表现、又不剧烈抖动。
    """
    score = max(0.0, min(1.0, float(score)))
    learner = get_learner(learner_id, create=True)
    ks = learner.setdefault("knowledge_state", {})
    old = ks.get(concept_id, DEFAULT_MASTERY)
    if old is None:
        old = DEFAULT_MASTERY
    # 首次记录直接采用；后续按 60/40 平滑
    new_val = score if old == 0.0 else round(old * 0.4 + score * 0.6, 4)
    ks[concept_id] = new_val
    return save_learner(learner_id, learner)


def batch_update_knowledge_state(learner_id: str, updates: Dict[str, float]) -> Dict[str, Any]:
    """批量更新多个知识点掌握度。"""
    for cid, sc in (updates or {}).items():
        update_knowledge_state(learner_id, cid, sc)
    return get_learner(learner_id, create=True)


def add_error_record(learner_id: str, concept_id: str, error_description: str) -> Dict[str, Any]:
    """记录一次错误（错题本 -> 错误记忆）。"""
    learner = get_learner(learner_id, create=True)
    learner.setdefault("error_memory", []).append({
        "timestamp": _now_iso(),
        "concept": concept_id,
        "error": error_description,
    })
    # 错误记忆最多保留 500 条
    learner["error_memory"] = learner["error_memory"][-500:]
    # 同步累计易错类型
    behavior = learner.setdefault("learning_behavior", {})
    error_types = behavior.setdefault("error_types", [])
    if concept_id and concept_id not in error_types:
        error_types.append(concept_id)
    return save_learner(learner_id, learner)


def add_assessment_record(learner_id: str, assessment_data: Dict[str, Any]) -> Dict[str, Any]:
    """记录一次测验结果。

    assessment_data 建议字段：{ "score": 0~100, "concepts": [知识点ID], "total": n, "correct": n }
    """
    learner = get_learner(learner_id, create=True)
    record = {
        "timestamp": _now_iso(),
        "score": float(assessment_data.get("score", 0) or 0),
        "concepts": list(assessment_data.get("concepts") or []),
        "total": assessment_data.get("total"),
        "correct": assessment_data.get("correct"),
    }
    learner.setdefault("assessment_history", []).append(record)
    learner["assessment_history"] = learner["assessment_history"][-200:]  # 只保留最近 200 次
    # 先持久化测验记录（否则下方 update_knowledge_state 会读取旧档案导致记录丢失）
    save_learner(learner_id, learner)
    # 测验后自动回写知识点掌握度（score/100 映射到 0~1）
    if record["concepts"] and record["total"]:
        mastery = record["correct"] / record["total"] if record["total"] else 0.0
        for cid in record["concepts"]:
            update_knowledge_state(learner_id, cid, mastery)
    return learner


def compute_learning_gain(learner_id: str, pre_test: float, post_test: float) -> float:
    """计算学习增益 = 后测 - 前测（按 0~100 分制）。"""
    return float(post_test) - float(pre_test)


def get_mastery(learner_id: str, concept_id: str) -> float:
    """读取单个知识点掌握度。"""
    learner = get_learner(learner_id, create=False)
    if not learner:
        return DEFAULT_MASTERY
    return float(learner.get("knowledge_state", {}).get(concept_id, DEFAULT_MASTERY) or 0.0)


def get_average_mastery(learner_id: str) -> float:
    """当前全部知识点平均掌握度。"""
    learner = get_learner(learner_id, create=False)
    if not learner:
        return 0.0
    ks = learner.get("knowledge_state", {}) or {}
    return round(sum(ks.values()) / len(ks), 4) if ks else 0.0


if __name__ == "__main__":
    # 冒烟测试
    import sys
    tid = "test_smoke"
    get_learner(tid, create=True)
    update_knowledge_state(tid, "KG001", 0.82)
    update_knowledge_state(tid, "KG002", 0.45)
    add_error_record(tid, "KG001", "混淆了加速度与速度的概念")
    add_assessment_record(tid, {"score": 75, "concepts": ["KG001"], "total": 4, "correct": 3})
    data = get_learner(tid)
    assert data["knowledge_state"]["KG002"] == 0.45, data
    assert data["knowledge_state"]["KG001"] > 0.75, data  # 0.82 与测验 0.75 平滑后的结果
    assert data["error_memory"][-1]["concept"] == "KG001"
    assert compute_learning_gain(tid, 50, 82) == 32
    print("Learner Model 冒烟测试通过:", data["learner_id"], data["knowledge_state"])
    # 清理测试档案
    import os
    p = learner_path(tid)
    if p.exists():
        os.remove(p)
