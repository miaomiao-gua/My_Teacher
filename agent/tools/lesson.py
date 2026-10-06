"""课程与学生状态工具 —— 把 v4 的「学生数字孪生 / 知识图谱 / 教学策略引擎」交给模型调用。

为什么这样做
------------
老项目把这些能力做成了**关键词路由**：用户说「我该学什么」→ 路由到 Planner Agent。
问题是：模型自己最清楚此刻需要什么上下文，而关键词匹配既漏（换个说法就不认）又误
（「我该学什么」出现在别的语境里也乱触发）。

工具化之后：模型想了解这个学生 → 调 `learner_status`；想知道下一步讲什么 → 调 `plan_next`；
想记录学生答对答错 → 调 `record_result`（顺手拿到教学策略建议）。
**上下文按需取，而不是每次把所有资料塞进提示词**——这是多轮工具循环省上下文的关键。

这些工具全部复用 `server/` 里现成的模块（它们是纯函数，没有 Flask 依赖），
所以行为与老界面完全一致，不会出现「两个系统两套成绩」。
"""

from __future__ import annotations

import json
import importlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import DATA_DIR, SERVER_DIR
from ..registry import ToolContext, ToolResult, tool

LESSONS_DIR = DATA_DIR / "lessons"
MAX_OUTLINE_CHARS = 3600
MAX_UNIT_CHARS = 3200


@lru_cache(maxsize=32)
def _srv(name: str):
    """导入 server/ 下的老模块（纯函数模块，安全复用）。"""
    import sys

    if str(SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(SERVER_DIR))
    return importlib.import_module(name)


def _learner_id(ctx: ToolContext) -> str:
    """学习者标识：优先用登录用户名（与老界面一致），没有就用匿名档。"""
    return (getattr(ctx.session, "username", None) or "anonymous").strip() or "anonymous"


def _lesson(ctx: ToolContext) -> Optional[str]:
    folder = getattr(ctx.session, "lesson_folder", None)
    if not folder:
        return None
    return folder if (LESSONS_DIR / folder).exists() else None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _units_of(data: Any) -> List[Dict[str, Any]]:
    """从 syllabus.json 里取出单元列表（兼容几种历史结构）。"""
    if isinstance(data, dict):
        for key in ("units", "lessons", "chapters"):
            value = data.get(key)
            if isinstance(value, list):
                return [u for u in value if isinstance(u, dict)]
        plan = data.get("plan")
        if isinstance(plan, dict):
            return _units_of(plan)
    if isinstance(data, list):
        return [u for u in data if isinstance(u, dict)]
    return []


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n…（内容过长已截断）"


# ---------------------------------------------------------------------------
@tool(
    "lesson_context",
    "读取当前课程的教案资料：课程大纲与目标、某一单元的详细内容、学生的学习画像、学习进度。"
    "讲解前想确认「这门课到底要教什么、这个单元有哪些核心公式」时调用它，"
    "不要凭记忆编课程内容。",
    {
        "type": "object",
        "properties": {
            "part": {
                "type": "string",
                "description": "取哪部分：outline=全课程大纲与单元要点（默认）；unit=某个单元的详细资料；"
                               "student=学生的跨章学习画像；progress=学习进度",
            },
            "unit_index": {"type": "integer", "description": "part=unit 时指定第几个单元（从 1 开始）"},
        },
        "required": [],
    },
)
async def lesson_context(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    folder = _lesson(ctx)
    if not folder:
        return ToolResult(
            summary="当前会话还没有绑定课程（学生是自由提问模式），没有教案资料可用。"
                    "请基于公认知识作答，并明确标注哪些是补充常识。",
            data={"lesson": None},
        )
    part = (str(args.get("part") or "outline")).strip().lower()
    base = LESSONS_DIR / folder

    if part == "unit":
        index = int(args.get("unit_index") or 1)
        try:
            text = _srv("file_utils").load_unit_context(folder, index)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"读取单元资料失败：{exc}")
        if not text or not text.strip():
            return ToolResult.fail(
                f"第 {index} 单元没有可用的详细资料（可能教材还没提取）。"
                "可以改用 part=outline 看大纲，或直接讲解该单元的核心概念。"
            )
        return ToolResult(
            summary=f"【第 {index} 单元资料】\n" + _truncate(text.strip(), MAX_UNIT_CHARS),
            data={"lesson": folder, "part": "unit", "unit_index": index},
        )

    if part == "student":
        profile = _read_json(base / "student_profile.json") or {}
        if not profile:
            return ToolResult(summary="还没有这个学生的跨章画像（一般在学完一章后生成）。", data={"profile": {}})
        lines = []
        for key, value in profile.items():
            if isinstance(value, (str, int, float)) and str(value).strip():
                lines.append(f"- {key}: {value}")
            elif isinstance(value, list) and value:
                lines.append(f"- {key}: " + "；".join(str(v) for v in value[:8]))
        return ToolResult(
            summary="【学生跨章画像】\n" + _truncate("\n".join(lines), 2000),
            data={"profile": profile},
        )

    if part == "progress":
        progress = _read_json(base / "progress.json") or {}
        return ToolResult(
            summary="【学习进度】\n" + _truncate(json.dumps(progress, ensure_ascii=False, indent=1), 1500),
            data={"progress": progress},
        )

    # 默认：全课程大纲
    data = _read_json(base / "syllabus.json") or _read_json(base / "plan.json") or {}
    units = _units_of(data)
    if not units and not data:
        return ToolResult.fail("这门课还没有教案（可能还在备课中）。请先跟学生确认主题，用已有知识讲解。")

    head: List[str] = []
    if isinstance(data, dict):
        for key, label in (("topic", "主题"), ("course_target", "课程目标"),
                           ("acceptance_criteria", "验收标准")):
            value = data.get(key)
            if value:
                head.append(f"{label}：{value}")
    body: List[str] = []
    for i, unit in enumerate(units[:20], 1):
        title = unit.get("title") or unit.get("name") or f"第 {i} 单元"
        summary = unit.get("summary") or unit.get("target") or ""
        kps = unit.get("key_points") or []
        formulas = unit.get("core_formulas") or []
        line = f"{i}. {title}"
        if summary:
            line += f" —— {summary}"
        if kps:
            line += "\n   要点：" + "、".join(str(k) for k in kps[:6])
        if formulas:
            names = [f.get("name") if isinstance(f, dict) else str(f) for f in formulas[:4]]
            line += "\n   公式/概念：" + "、".join(str(n) for n in names if n)
        body.append(line)

    text = "\n".join(head) + "\n\n【单元目录】\n" + "\n".join(body)
    return ToolResult(
        summary=f"当前课程《{folder}》的大纲：\n" + _truncate(text, MAX_OUTLINE_CHARS),
        data={"lesson": folder, "part": "outline", "unit_count": len(units)},
    )


# ---------------------------------------------------------------------------
@tool(
    "learner_status",
    "查看这个学生的知识掌握情况：平均掌握度、最薄弱的几个知识点、高频错题类型、学习增益。"
    "想知道「他哪里不行、我该不该重讲」时调用它，而不是凭感觉猜。",
    {"type": "object", "properties": {}, "required": []},
)
async def learner_status(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    learner_id = _learner_id(ctx)
    folder = _lesson(ctx)
    try:
        lm = _srv("learner_model")
        learner = lm.get_learner(learner_id, create=False) or {}
        if not learner:
            return ToolResult(
                summary=f"学生 `{learner_id}` 还没有学习记录（第一次上课）。可以先做几道摸底题建立档案。",
                data={"learner_id": learner_id, "empty": True},
            )
        avg = lm.get_average_mastery(learner_id)
        ks = learner.get("knowledge_state") or {}
        weak = sorted(((cid, float(m or 0)) for cid, m in ks.items()), key=lambda kv: kv[1])[:5]
        errors = learner.get("error_memory") or []
        assessments = learner.get("assessment_history") or []
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"读取学生档案失败：{exc}")

    # 把知识点 ID 换成学生的语言（有知识图谱时用名字）
    names: Dict[str, str] = {}
    if folder:
        try:
            names = {n.get("id"): n.get("name") for n in _srv("knowledge_graph").get_nodes(folder)}
        except Exception:
            names = {}

    lines = [f"学生：`{learner_id}`", f"平均掌握度：{avg:.0%}", f"已记录知识点：{len(ks)} 个",
             f"测验记录：{len(assessments)} 次"]
    if weak:
        lines.append("最薄弱：" + "、".join(f"{names.get(cid, cid)}（{m:.0%}）" for cid, m in weak))
    if errors:
        recent = errors[-5:]
        lines.append("最近的错题：")
        for e in recent:
            cid = e.get("concept") or ""
            lines.append(f"  - {names.get(cid, cid) or '（未标注知识点）'}：{str(e.get('error') or '')[:80]}")
    insight = ""
    try:
        data = _srv("learning_evaluation").generate_insights(learner_id)
        insight = str((data or {}).get("summary") or (data or {}).get("insight") or "")
    except Exception:
        insight = ""
    if insight:
        lines.append("学习洞察：" + insight[:300])

    return ToolResult(
        summary="\n".join(lines),
        data={"learner_id": learner_id, "average_mastery": avg, "weak": weak, "errors": errors[-5:]},
    )


# ---------------------------------------------------------------------------
@tool(
    "diagnose",
    "深度诊断这个学生的知识漏洞：列出未掌握的知识点、错误集中在哪、给出补救建议。"
    "学生问「我哪里薄弱」「为什么我总是错」时用它。",
    {"type": "object", "properties": {}, "required": []},
)
async def diagnose(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    learner_id = _learner_id(ctx)
    folder = _lesson(ctx)
    try:
        result = _srv("pedagogical_engine").run_diagnosis(learner_id, folder) or {}
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"诊断失败：{exc}")

    weak = result.get("weak_points") or []
    errors = result.get("error_summary") or []
    lines = [str(result.get("suggestion") or "")]
    if weak:
        lines.append("薄弱知识点：")
        for w in weak[:6]:
            lines.append(f"  - {w.get('name') or w.get('concept_id')}：掌握度 {float(w.get('mastery') or 0):.0%}")
    if errors:
        lines.append("错得最多：" + "、".join(f"{e.get('name') or e.get('concept_id')}（{e.get('count')} 次）" for e in errors[:5]))
    if not weak and not errors:
        lines.append("暂时没有足够数据做诊断，可以先用几道小题摸个底。")
    return ToolResult(summary="\n".join(x for x in lines if x), data=result)


# ---------------------------------------------------------------------------
@tool(
    "plan_next",
    "根据学生的掌握情况推荐「下一步该学什么」，并给出理由与预计用时。"
    "学生问「接下来学什么」「我不想学这个了」时用它做依据。",
    {"type": "object", "properties": {}, "required": []},
)
async def plan_next(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    learner_id = _learner_id(ctx)
    folder = _lesson(ctx)
    try:
        ped = _srv("pedagogical_engine")
        plan = ped.plan_next_lesson(learner_id, folder) or {}
        action = ped.get_next_action(learner_id, {}) or {}
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"规划失败：{exc}")

    rec = plan.get("recommended") or {}
    if not rec:
        summary = f"暂无推荐：{plan.get('reason') or '课程还没有知识图谱，需要先备课。'}"
    else:
        summary = (
            f"推荐下一个知识点：**{rec.get('name')}**（当前掌握度 "
            f"{float(rec.get('current_mastery') or 0):.0%}，难度 {rec.get('difficulty')}，"
            f"预计 {rec.get('estimated_time')} 分钟）。\n理由：{plan.get('reason')}"
        )
    if action.get("message"):
        summary += f"\n教学动作建议：{action.get('message')}"
    return ToolResult(summary=summary, data={"plan": plan, "action": action})


# ---------------------------------------------------------------------------
@tool(
    "record_result",
    "记录学生这道题答对还是答错（同时更新掌握度、错题记忆），并返回**教学策略建议**。"
    "每次判定学生答题对错后都要调用它——这是「记住这个学生」的唯一入口。",
    {
        "type": "object",
        "properties": {
            "concept": {"type": "string", "description": "这道题考的知识点名称，如「酸碱中和」「指示剂变色」"},
            "correct": {"type": "boolean", "description": "学生答对了就传 true，答错传 false"},
            "note": {"type": "string", "description": "答错时记录具体错在哪（一句话），答对可留空"},
        },
        "required": ["concept", "correct"],
    },
)
async def record_result(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    learner_id = _learner_id(ctx)
    folder = _lesson(ctx)
    concept = str(args.get("concept") or "").strip() or "未标注知识点"
    correct = bool(args.get("correct"))
    note = str(args.get("note") or "").strip()

    try:
        lm = _srv("learner_model")
        ped = _srv("pedagogical_engine")
        concept_id = concept
        if folder:
            # 尽量把「知识点名称」映射成课程知识图谱里的 ID，这样掌握度才能落到图上
            try:
                for node in _srv("knowledge_graph").get_nodes(folder):
                    if node.get("name") and node["name"] in concept or concept in str(node.get("name") or ""):
                        concept_id = node.get("id") or concept
                        break
                else:
                    # 图谱里没有 → 顺手加一个节点，避免数据散落
                    try:
                        _srv("knowledge_graph").save_knowledge_graph(folder, _merge_node(folder, concept_id, concept))
                    except Exception:
                        pass
            except Exception:
                pass
        lm.update_knowledge_state(learner_id, concept_id, 1.0 if correct else 0.25)
        if not correct and note:
            lm.add_error_record(learner_id, concept_id, note)
        streak = ped.record_answer_result(learner_id, correct, concept_id) or {}
        mastery = lm.get_mastery(learner_id, concept_id)
        advice = ped.adapt_teaching_strategy(
            learner_id,
            {
                "concept_id": concept_id,
                "recent_results": [correct],
                "consecutive_correct": streak.get("consecutive_correct", 0),
                "consecutive_wrong": streak.get("consecutive_wrong", 0),
            },
        ) or {}
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"记录失败：{exc}")

    summary = (
        f"已记录：`{concept}` {'答对' if correct else '答错'}；掌握度现在 {mastery:.0%}；"
        f"连续答对 {streak.get('consecutive_correct', 0)} 题 / 连续答错 {streak.get('consecutive_wrong', 0)} 题。\n"
        f"教学策略建议：{advice.get('message') or '继续当前节奏。'}"
    )
    return ToolResult(
        summary=summary,
        data={"concept": concept, "concept_id": concept_id, "mastery": mastery, "streak": streak, "advice": advice},
        stage={"emotion": "joy" if correct else "think"},
    )


def _merge_node(folder: str, node_id: str, name: str) -> Dict[str, Any]:
    """把一个新的知识点补进课程知识图谱（保持已有节点不动）。"""
    kg = _srv("knowledge_graph")
    graph = kg.load_knowledge_graph(folder) or {}
    nodes = graph.get("nodes") or []
    if not any(n.get("id") == node_id for n in nodes):
        nodes.append({"id": node_id, "name": name, "description": "", "prerequisites": [],
                      "concepts": [], "skills": [], "tags": [], "difficulty": 0.5,
                      "estimated_time": 10, "related_quiz_questions": []})
    graph["nodes"] = nodes
    return graph
