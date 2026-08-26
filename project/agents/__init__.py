"""多 Agent 系统入口 —— 意图路由分发器。

根据用户输入的关键词匹配意图，分发到对应 Agent；
讲解/答疑类请求（Tutor）交回主聊天流程处理，保证现有体验不变。

协作流程：
    用户消息
      ↓
    route_message()（关键词意图识别）
      ↓
    ├── 教学提问 → Tutor Agent（交回主聊天）
    ├── 课程规划 → Planner Agent
    ├── 诊断请求 → Diagnostician Agent
    ├── 测验请求 → Examiner Agent
    ├── 评估请求 → Evaluator Agent
    └── 情感/动力 → Coach Agent
      ↓
    返回结果 + 更新学生状态（由调用方在 app.py 完成）
"""

from typing import Any, Dict, Optional

from .base_agent import BaseAgent
from .coach_agent import CoachAgent
from .diagnostician_agent import DiagnosticianAgent
from .evaluator_agent import EvaluatorAgent
from .examiner_agent import ExaminerAgent
from .planner_agent import PlannerAgent
from .tutor_agent import TutorAgent

# 意图规则：keyword 列表按优先级从高到低匹配
# 每个规则 (agent_name, [关键词...])；命中任意关键词即分发。
INTENT_RULES: list[tuple[str, list[str]]] = [
    # 诊断类优先（"分析我的薄弱点"含"分析"也含"薄弱点"）
    ("diagnostician", ["薄弱", "诊断", "知识漏洞", "弱点", "学习差距", "哪里不会", "不会什么"]),
    ("coach", ["没动力", "学不下去", "坚持不下去", "想放弃", "沮丧", "焦虑", "不想学", "好累", "学不进去"]),
    ("examiner", ["出题", "考我", "测验", "做几道题", "练习题", "测试我", "抽测"]),
    ("evaluator", ["评估", "学习效果", "学习效率", "增益", "进步多少", "效率报告", "效果报告", "学得怎么样"]),
    ("planner", ["接下来学什么", "接下来该学", "接下来学", "下一步学", "学习计划", "课程规划", "先学什么", "该怎么学", "学习顺序", "规划一下", "下一步", "之后学"]),
]

AGENT_CLASSES: Dict[str, type] = {
    "tutor": TutorAgent,
    "planner": PlannerAgent,
    "diagnostician": DiagnosticianAgent,
    "examiner": ExaminerAgent,
    "evaluator": EvaluatorAgent,
    "coach": CoachAgent,
}


def detect_intent(user_input: str) -> Optional[str]:
    """关键词意图识别，返回 agent_name；无法确定时返回 None（默认 Tutor）。"""
    if not user_input:
        return None
    text = user_input.strip().lower()
    for agent_name, keywords in INTENT_RULES:
        if any(kw in text for kw in keywords):
            return agent_name
    return None


def build_agent(
    agent_name: str,
    lesson_folder: Optional[str] = None,
    learner_id: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[BaseAgent]:
    cls = AGENT_CLASSES.get(agent_name)
    if not cls:
        return None
    return cls(lesson_folder=lesson_folder, learner_id=learner_id, config=config or {})


def route_message(
    user_input: str,
    lesson_folder: Optional[str] = None,
    learner_id: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """路由入口：识别意图并执行对应 Agent。

    返回统一结构 { "agent", "reply", "fallback_to_chat", "data" }。
    无法识别意图或命中 Tutor 时返回 fallback_to_chat=True，交回主聊天流程。
    """
    agent_name = detect_intent(user_input)
    if not agent_name or agent_name == "tutor":
        return {
            "agent": agent_name or "tutor",
            "reply": "",
            "fallback_to_chat": True,
            "data": {},
        }

    agent = build_agent(agent_name, lesson_folder=lesson_folder, learner_id=learner_id, config=config)
    if not agent:
        return {"agent": agent_name, "reply": "", "fallback_to_chat": True, "data": {}}

    try:
        result = agent.run(user_input, context={"lesson_folder": lesson_folder, "learner_id": learner_id})
    except Exception as exc:
        print(f"[agents] {agent_name} 执行异常，回退主聊天: {exc}", flush=True)
        return {"agent": agent_name, "reply": "", "fallback_to_chat": True, "data": {}}
    result["agent"] = agent_name
    result.setdefault("fallback_to_chat", False)
    result.setdefault("data", {})
    return result
