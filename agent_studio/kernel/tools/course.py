"""备课与课程管理工具 —— 让新项目自己会备课、管课程、管进度。

为什么这组工具是关键
--------------------
没有它，模型只能「就着记忆讲课」——大纲自己编、知识点自己排，讲三节课就开始飘。
有了它，「教学」这件事才有骨架：**先备一门课（一次重活，1~3 分钟）→ 之后的每一课
都锚定在教案上**。这与人类老师的流程一致，也是老项目验证过的做法。

实现方式是**复用老项目的数据层**（`course/` 目录下原样拷贝的模块），
但数据落在本项目自己的 `data/lessons/` 下 —— 新项目自己拥有课程，不再只是读老项目的。
"""

from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from ..config import ROOT, load_app_config
from ..registry import ToolContext, ToolResult, tool

# 课程数据层：老项目移植过来的模块 + 本项目的 storage（目录约定一致，课程可互搬）
COURSE_DIR = ROOT / "course"
if str(COURSE_DIR) not in sys.path:
    sys.path.insert(0, str(COURSE_DIR))

import storage  # noqa: E402
from lesson_prep import prepare_lesson  # noqa: E402

MAX_UNITS_IN_SUMMARY = 10


def _topic_brief(topic: str, level: str, focus: str) -> str:
    brief = topic
    if level:
        brief += f"（学生水平：{level}）"
    if focus:
        brief += f"（侧重：{focus}）"
    return brief


def _outline_summary(created: Dict[str, Any], plan: Dict[str, Any]) -> str:
    """给模型看的课程摘要（要短，但必须够它接着上课用）。"""
    lines = [
        f"备课完成，《{created['topic']}》已建好（课程目录 `{created['folder']}`，共 {created['unit_count']} 课）。"
    ]
    if plan.get("course_target"):
        lines.append(f"课程目标：{plan['course_target']}")
    if plan.get("acceptance_criteria"):
        lines.append(f"验收标准：{plan['acceptance_criteria']}")
    lines.append("单元目录：")
    for unit in created["units"][:MAX_UNITS_IN_SUMMARY]:
        kps = "、".join(str(k) for k in (unit.get("key_points") or [])[:4])
        line = f"  {unit['index']}. {unit['title']}"
        if unit.get("summary"):
            line += f" —— {unit['summary'][:60]}"
        if kps:
            line += f"（要点：{kps}）"
        lines.append(line)
    if created["unit_count"] > MAX_UNITS_IN_SUMMARY:
        lines.append(f"  …（还有 {created['unit_count'] - MAX_UNITS_IN_SUMMARY} 课，需要时说「讲第 N 课」）")
    lines.append(
        "接下来：学生说「讲第 N 课」时，先调用 `lesson_context`(part=unit, unit_index=N) 取该课教案再讲；"
        "讲完用 `record_result` 记录学生答题对错；需要演示就用 graph_calculator / draw_board / render_ui。"
    )
    return "\n".join(lines)


@tool(
    "prepare_course",
    "给一个主题做**整门课**的备课：生成课程目标、单元大纲、每课的知识点/核心公式/易错对比/"
    "通关问题，并保存成一门课程（还会生成知识图谱）。需要 1~3 分钟。"
    "学生第一次学某个主题、或说「帮我做一门课/系统讲讲 X」时用它——不要自己凭空编大纲。",
    {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "课程主题，如「Python 列表与字典」「酸碱盐」「Alevel M1 力学」"},
            "level": {"type": "string", "description": "学生水平或年级（可选），如「初中」「大一无基础」"},
            "focus": {"type": "string", "description": "学生的具体诉求或侧重点（可选），如「只想学会做题」「偏实验现象」"},
        },
        "required": ["topic"],
    },
    timeout=600,              # 备课是重活，给它 10 分钟（全局超时是 60 秒）
    produces=["ui"],
)
async def prepare_course(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    topic = str(args.get("topic") or "").strip()
    if not topic:
        return ToolResult.fail("请给出课程主题（topic），例如「酸碱盐」。")
    level = str(args.get("level") or "").strip()
    focus = str(args.get("focus") or "").strip()

    await ctx.push(_progress_notice("正在备课：生成课程目标与单元大纲…（约 1~3 分钟，请稍候）"))

    cfg = load_app_config()
    try:
        plan = await asyncio.to_thread(prepare_lesson, _topic_brief(topic, level, focus), cfg)
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(
            f"备课失败：{type(exc).__name__}: {exc}。请检查模型链路（/agent/health），稍后可重试。"
        )
    if not isinstance(plan, dict) or not plan.get("units"):
        return ToolResult.fail(
            "备课没有生成有效大纲（模型可能没连上或返回为空）。可以稍后重试；"
            "如果学生只是想先听个大概，也可以直接讲解并明确说明「这是概述，不是完整教案」。"
        )

    degraded = bool(plan.get("_prepared_fallback"))
    try:
        created = await asyncio.to_thread(storage.create_course, plan, ctx.username)
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"课程保存失败：{type(exc).__name__}: {exc}")

    # 绑定到当前会话：之后的讲课/出题/记画像都针对这门课
    ctx.session.lesson_folder = created["folder"]
    ctx.session.save()

    units_ui = [
        {"title": f"第 {u['index']} 课 · {u['title']}", "desc": u.get("summary") or ""}
        for u in created["units"][:12]
    ]
    spec = {
        "title": f"备课完成 · 《{created['topic']}》",
        "items": [
            {"type": "keyvalue", "pairs": [
                {"key": "课程目录", "value": created["folder"]},
                {"key": "单元数", "value": str(created["unit_count"])},
                {"key": "课程目标", "value": plan.get("course_target") or "（未生成）"},
                {"key": "知识图谱", "value": "已生成" if created["graph_ok"] else "未生成（不影响上课）"},
            ]},
            {"type": "steps", "current": 1, "steps": units_ui},
            {"type": "callout", "tone": "warning" if degraded else "success",
             "title": "⚠️ 这是降级生成的简版大纲" if degraded else "可以开始上课了",
             "content": (f"原因：{plan.get('_prepared_reason') or '模型未返回完整结果'}。建议稍后重新备课。"
                         if degraded else
                         "说「讲第 1 课」我就按教案讲；讲的过程中会用图表、画板或虚拟实验帮你看见知识点。")},
        ],
    }
    summary = _outline_summary(created, plan)
    if degraded:
        summary = "⚠️ 本次是**降级模板**（模型未返回完整教案）：" + summary
    return ToolResult(
        summary=summary,
        ui=spec,
        data={"folder": created["folder"], "unit_count": created["unit_count"], "degraded": degraded},
        stage={"emotion": "joy"},
    )


@tool(
    "list_courses",
    "列出已有的课程（主题、单元数、创建时间）。学生问「我有哪些课」「之前学过什么」或要换课时用它。",
    {"type": "object", "properties": {}, "required": []},
    produces=["ui"],
)
async def list_courses(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    courses = await asyncio.to_thread(storage.list_courses)
    current = getattr(ctx.session, "lesson_folder", None)
    if not courses:
        return ToolResult(
            summary="还没有任何课程。学生想系统学点什么时，用 `prepare_course` 建一门。",
            data={"courses": []},
        )
    rows = [
        [
            ("★ " if c["folder"] == current else "") + c["topic"],
            str(c["unit_count"]),
            (c.get("created_at") or "")[:10],
            "有" if c.get("has_graph") else "无",
        ]
        for c in courses[:20]
    ]
    spec = {
        "title": "我的课程",
        "items": [
            {"type": "table", "columns": ["课程", "单元数", "创建时间", "知识图谱"], "rows": rows,
             "types": ["text", "num", "text", "badge"]},
            {"type": "callout", "tone": "info", "title": "怎么继续",
             "content": "说「打开《课程名》」或「讲第 N 课」即可。★ 是当前正在上的课。"},
        ],
    }
    lines = [f"共 {len(courses)} 门课程：" + "；".join(
        f"{c['topic']}（{c['unit_count']} 课，{c['folder']}）" for c in courses[:8]
    )]
    if current:
        lines.append(f"当前课程：{current}")
    return ToolResult(summary="\n".join(lines), ui=spec, data={"courses": courses})


@tool(
    "open_course",
    "打开一门已存在的课程，设为当前课程（之后的讲课、出题、记录画像都针对它）。"
    "可以用课程目录名，也可以用主题关键词（如「酸碱」）。",
    {
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "课程目录名（不确定就用 topic）"},
            "topic": {"type": "string", "description": "主题关键词，模糊匹配课名"},
        },
        "required": [],
    },
    produces=["ui"],
)
async def open_course(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    folder = str(args.get("folder") or "").strip()
    topic = str(args.get("topic") or "").strip()
    if not folder and not topic:
        return ToolResult.fail("请给出课程目录名（folder）或主题关键词（topic）。")

    courses = await asyncio.to_thread(storage.list_courses)
    if not courses:
        return ToolResult.fail("还没有任何课程。要系统学一个主题，先用 `prepare_course` 建一门。")

    target = None
    if folder:
        target = next((c for c in courses if c["folder"] == folder), None)
    if target is None and topic:
        target = next((c for c in courses if topic in (c["topic"] or "")), None)
        if target is None:
            target = next((c for c in courses if topic in c["folder"]), None)
    if target is None:
        names = "、".join(c["topic"] for c in courses[:8])
        return ToolResult.fail(f"没找到匹配的课程（关键词：{folder or topic}）。现有课程：{names}")

    ctx.session.lesson_folder = target["folder"]
    ctx.session.save()

    data = await asyncio.to_thread(storage.load_course, target["folder"])
    syllabus = data.get("syllabus") or {}
    units = [u for u in (syllabus.get("units") or []) if isinstance(u, dict)]
    progress = data.get("progress") or {}
    current_unit = int(progress.get("current_unit") or 1)

    steps = [
        {"title": f"第 {i} 课 · {u.get('title') or ''}", "desc": (u.get("summary") or "")[:60]}
        for i, u in enumerate(units[:12], 1)
    ]
    spec = {
        "title": f"打开课程 · 《{target['topic']}》",
        "items": [
            {"type": "keyvalue", "pairs": [
                {"key": "进度", "value": f"当前第 {current_unit} 课 / 共 {target['unit_count']} 课"},
                {"key": "已完成", "value": str(len(progress.get("completed_units") or [])) + " 课"},
                {"key": "课程目标", "value": (syllabus.get("course_target") or data.get("config", {}).get("course_target") or "（未记录）")},
            ]},
            {"type": "steps", "current": current_unit, "steps": steps},
        ],
    }
    return ToolResult(
        summary=(
            f"已打开《{target['topic']}》（目录 `{target['folder']}`，共 {target['unit_count']} 课，"
            f"当前第 {current_unit} 课）。\n"
            + "\n".join(f"  {i}. {u.get('title') or ''}" for i, u in enumerate(units[:12], 1))
            + "\n讲某一课前先调用 `lesson_context`(part=unit, unit_index=N) 取该课教案。"
        ),
        ui=spec,
        data={"folder": target["folder"], "current_unit": current_unit},
    )


@tool(
    "course_progress",
    "查看或更新课程进度。不传参数 = 查看；unit_index + completed=true = 标记这一课学完（会自动推进到下一课）；"
    "quiz_score = 记一次测验成绩。",
    {
        "type": "object",
        "properties": {
            "unit_index": {"type": "integer", "description": "第几课（从 1 开始）"},
            "completed": {"type": "boolean", "description": "是否标记为已完成"},
            "quiz_score": {"type": "number", "description": "这次测验的分数（0~100）"},
        },
        "required": [],
    },
    produces=["ui"],
)
async def course_progress(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    folder = getattr(ctx.session, "lesson_folder", None)
    if not folder:
        return ToolResult.fail("当前还没有绑定课程。用 `list_courses` 看看有哪些课，或 `prepare_course` 新建一门。")

    unit_index = args.get("unit_index")
    completed = bool(args.get("completed"))
    quiz_score = args.get("quiz_score")
    try:
        if unit_index is not None or completed or quiz_score is not None:
            progress = await asyncio.to_thread(
                storage.set_progress, folder,
                int(unit_index) if unit_index is not None else None,
                completed,
                float(quiz_score) if quiz_score is not None else None,
            )
            action = "已更新进度"
        else:
            data = await asyncio.to_thread(storage.load_course, folder)
            progress = data.get("progress") or {}
            action = "当前进度"
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"读取/更新进度失败：{type(exc).__name__}: {exc}")

    scores = progress.get("quiz_scores") or []
    spec = {
        "title": "学习进度",
        "items": [
            {"type": "keyvalue", "pairs": [
                {"key": "课程", "value": folder},
                {"key": "当前课", "value": f"第 {progress.get('current_unit') or 1} 课"},
                {"key": "已完成", "value": f"{len(progress.get('completed_units') or [])} 课"},
                {"key": "测验次数", "value": str(len(scores))},
            ]},
        ],
    }
    if scores:
        spec["items"].append({
            "type": "chart", "kind": "line", "title": "历次测验成绩",
            "series": [{"label": "分数", "data": [s.get("score") for s in scores[-12:]]}],
            "data": [{"label": f"第{s.get('unit') or '?'}课"} for s in scores[-12:]],
        })
    return ToolResult(
        summary=(
            f"{action}：课程 `{folder}`，当前第 {progress.get('current_unit') or 1} 课，"
            f"已完成 {len(progress.get('completed_units') or [])} 课"
            + (f"，最近一次测验 {scores[-1].get('score')} 分" if scores else "")
        ),
        ui=spec,
        data={"progress": progress},
    )


def _progress_notice(message: str):
    """发一个给学生看的提示事件（备课要等一两分钟，不能让人干瞪眼）。"""
    from .. import events

    return events.notice(message, level="info")


# ---------------------------------------------------------------------------
# 对话与文件夹：让模型知道自己「在哪儿」，也看得见学生的全部历史对话
# ---------------------------------------------------------------------------
@tool(
    "list_conversations",
    "查看历史对话与文件夹结构（学科 → 教材 → 对话），并标出**当前所在的对话**。"
    "学生问「我们在哪个文件夹」「我有哪些对话」「之前问过什么」时用它；"
    "也可以用 keyword 找以前某个话题的对话。",
    {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "只看某个学科（可选）"},
            "keyword": {"type": "string", "description": "按关键词筛选对话标题（可选）"},
        },
        "required": [],
    },
    produces=["ui"],
)
async def list_conversations(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    from ..session import STORE

    subject_filter = str(args.get("subject") or "").strip()
    keyword = str(args.get("keyword") or "").strip().lower()

    sess = ctx.session
    cur_subject = (getattr(sess, "subject", "") or "").strip() or "未分类"
    cur_textbook = (getattr(sess, "textbook", "") or "").strip() or "未指定教材"
    cur_title = sess.auto_title() if hasattr(sess, "auto_title") else (getattr(sess, "title", "") or "")

    tree = STORE.tree()
    rows: List[List[str]] = []
    matched: List[str] = []
    for subj in tree:
        if subject_filter and subj["name"] != subject_filter:
            continue
        for book in subj.get("textbooks") or []:
            for item in book.get("sessions") or []:
                title = item.get("title") or "新对话"
                if keyword and keyword not in title.lower():
                    continue
                is_current = item.get("id") == sess.id
                rows.append([
                    subj["name"], book["name"],
                    ("▶ " if is_current else "") + title[:40],
                    time.strftime("%m-%d %H:%M", time.localtime(float(item.get("updated_at") or 0))),
                ])
                if is_current:
                    matched.append(title)

    here = f"「{cur_subject} / {cur_textbook}」下的对话「{cur_title}」"
    if not rows:
        return ToolResult(
            summary=f"你现在在 {here}。"
                    + (f"没有找到匹配的对话（筛选：学科={subject_filter or '不限'}，关键词={keyword or '无'}）。"
                       if (subject_filter or keyword) else "学生目前只有这一个对话。"),
            data={"current": {"subject": cur_subject, "textbook": cur_textbook, "title": cur_title},
                  "conversations": []},
        )

    spec = {
        "title": "历史对话与文件夹",
        "items": [
            {"type": "callout", "tone": "info", "title": "你现在在这里",
             "content": f"**{cur_subject} / {cur_textbook}** → 对话「{cur_title}」（表里带 ▶ 的那条）"},
            {"type": "table", "columns": ["学科", "教材", "对话", "最近活动"], "rows": rows[:40],
             "types": ["text", "text", "text", "text"]},
        ],
    }
    listing = "\n".join(f"  - {r[0]} / {r[1]} → {r[2]}（{r[3]}）" for r in rows[:30])
    total = len(STORE.list_all())
    return ToolResult(
        summary=(
            f"你现在在 {here}。学生一共有 {total} 个对话"
            + (f"，其中匹配的有 {len(rows)} 个" if (subject_filter or keyword) else "")
            + f"：\n{listing}\n"
            "（如果学生问「我们在哪个文件夹」，照实回答上面这一句即可，不要说不知道。）"
        ),
        ui=spec,
        data={"current": {"subject": cur_subject, "textbook": cur_textbook, "title": cur_title},
              "conversations": rows, "total": total},
    )


# ---------------------------------------------------------------------------
# 出题：讲完一课要能考学生，否则「学会了没有」只能靠感觉
# ---------------------------------------------------------------------------
_LETTERS = "ABCDEFGH"

# 判断题各种写法 → 对应「第一个选项正确 / 第二个选项正确」
_TRUE_WORDS = re.compile(r"^(对|正确|是|真|T|True|true|√|yes|Y)$")
_FALSE_WORDS = re.compile(r"^(错|错误|否|假|F|False|false|×|x|X|no|N)$")


def _mark_correct(options: List[Dict[str, Any]], answer: Any) -> bool:
    """在选项里标出正确项。成功返回 True。

    模型给的「答案」格式五花八门，**漏掉任何一种那道题就判不了分**，
    而现象是「学生点了没反应」，从界面上很难看出来（实测 3 道题里就有 1 道没标上）。
    这里把见过的写法都覆盖了：
        字母：B / B. / （B） / (b) / B、     序号：2 / "2."
        原文：乙 / "B. 乙"（带前缀也行）      布尔：True/False（判断题）
        选项自带 correct 标记                 判断题词：正确/错误/对/错/T/F/√/×
    """
    if not options:
        return False
    if isinstance(answer, bool):
        pos = 0 if answer else 1
        if pos < len(options):
            options[pos]["correct"] = True
            return True
        return False

    ans = re.sub(r"\s+", " ", str(answer or "")).strip()
    # 去掉「答案：」「正确答案:」「Answer:」这类前缀
    ans = re.sub(r"^(正确答案|参考答案|答案|Answer|answer|Correct)\s*[:：]?\s*", "", ans).strip()
    if not ans:
        return False

    # 1) 字母形式（含括号、点、顿号等包装）
    letter = re.sub(r"^[\(（\[【\s]*([A-Ha-h])[\)）\]】\.、,，:：\s]*$", r"\1", ans)
    if len(letter) == 1 and letter.upper() in _LETTERS:
        pos = _LETTERS.index(letter.upper())
        if pos < len(options):
            options[pos]["correct"] = True
            return True

    # 2) 纯数字序号（1 基）
    digits = re.sub(r"[^\d]", "", ans)
    if digits and len(digits) <= 2 and not re.search(r"[\u4e00-\u9fa5a-zA-Z]", ans):
        pos = int(digits) - 1
        if 0 <= pos < len(options):
            options[pos]["correct"] = True
            return True

    def strip_prefix(label: str) -> str:
        """去掉选项的「A. 」「(B)」这类前缀，便于原文比对。"""
        return re.sub(r"^[\(（\[【\s]*[A-Ha-h][\)）\]】\.、,，:：\s]+", "", label).strip()

    # 3) 原文匹配（先精确，再容忍前缀 / 包含关系）
    for i, opt in enumerate(options):
        label = opt["label"]
        if ans == label or ans == strip_prefix(label):
            options[i]["correct"] = True
            return True
    for i, opt in enumerate(options):
        label = opt["label"]
        bare = strip_prefix(label)
        if ans in label or (bare and (bare in ans or ans in bare)):
            options[i]["correct"] = True
            return True

    # 4) 判断题词
    if _TRUE_WORDS.match(ans) and len(options) >= 1:
        options[0]["correct"] = True
        return True
    if _FALSE_WORDS.match(ans) and len(options) >= 2:
        options[1]["correct"] = True
        return True
    return False


def _answer_of(question: Dict[str, Any]) -> Any:
    """从题目里取「正确答案」。模型/不同提示词模板用过好几种字段名。"""
    for key in ("answer", "correct", "correct_answer", "answer_key", "right_answer", "key", "答案"):
        value = question.get(key)
        if value not in (None, "", []):
            return value
    return None


def _split_inline_options(text: str) -> List[str]:
    """把「A. 甲 B. 乙 C. 丙」这种一整坨文本拆成选项数组。

    模型偶尔不按数组给选项，而是把四个选项写成一个字符串 —— 不拆的话
    整道题就变成「只有一个选项」，判分也就无从谈起。
    """
    parts = re.split(r"(?:^|\s)([A-H])[\.、\)）:：]\s*", text)
    out: List[str] = []
    for i in range(1, len(parts) - 1, 2):
        label = parts[i + 1].strip()
        if label:
            out.append(f"{parts[i]}. {label}")
    return out


def _to_quiz_node(question: Dict[str, Any], number: int, concept: str, session_id: str) -> Dict[str, Any]:
    """把出题结果（题干 + 选项 + 答案）转成前端能就地判分的答题卡组件。

    为什么要转换：老项目的题目是「选项字符串数组 + answer」，而前端组件要的是
    「每个选项自带 correct 标记」——这样点选就能立刻判分，不用等模型。
    """
    raw_options = question.get("options") or question.get("choices") or []
    options: List[Dict[str, Any]] = []
    if isinstance(raw_options, dict):        # 形如 {"A": "甲", "B": "乙"}
        for letter, text in raw_options.items():
            options.append({"label": f"{letter}. {text}", "correct": False, "feedback": ""})
    else:
        for opt in raw_options:
            if isinstance(opt, dict):
                label = str(opt.get("label") or opt.get("text") or opt.get("content") or "")
                options.append({"label": label, "correct": bool(opt.get("correct")), "feedback": ""})
            else:
                options.append({"label": str(opt), "correct": False, "feedback": ""})
    # 一整坨文本 → 拆成真选项
    if len(options) == 1 and len(re.findall(r"(?:^|\s)[A-H][\.、\)）:：]", options[0]["label"])) >= 2:
        options = [{"label": t, "correct": False, "feedback": ""} for t in _split_inline_options(options[0]["label"])]

    if options and not any(o["correct"] for o in options):
        _mark_correct(options, _answer_of(question))

    right = next((o["label"] for o in options if o["correct"]), "")
    for opt in options:
        if not opt["correct"]:
            opt["feedback"] = f"再想想：正确答案是 {right}" if right else "再想想：见解析"

    return {
        "type": "quiz",
        "question": f"{number}. {question.get('question') or question.get('stem') or ''}",
        "options": options,
        "explanation": question.get("explanation") or question.get("analysis") or question.get("解析") or "",
        # 前端判分后会用这个信息回传成绩，自动记进学生画像（不需要模型参与）
        "record": {"concept": concept, "session": session_id},
    }


@tool(
    "generate_quiz",
    "给当前课程的第 N 课出随堂测验（选择题，含解析），并显示成**可点选、当场判分**的答题卡。"
    "学生说「考考我」「出几道题」「我掌握得怎么样」时用它。"
    "学生作答的成绩会自动记进学习画像，你不用再手动记录。",
    {
        "type": "object",
        "properties": {
            "unit_index": {"type": "integer", "description": "第几课（从 1 开始），不填就用当前进度那一课"},
            "count": {"type": "integer", "description": "出几道题，默认 5"},
        },
        "required": [],
    },
    timeout=420,              # 出题也要调一次大模型
    produces=["ui"],
)
async def generate_quiz(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    folder = getattr(ctx.session, "lesson_folder", None)
    if not folder:
        return ToolResult.fail(
            "当前还没有绑定课程。先用 `prepare_course` 建一门，或 `open_course` 打开已有的课。"
        )
    data = await asyncio.to_thread(storage.load_course, folder)
    syllabus = data.get("syllabus") or {}
    units = [u for u in (syllabus.get("units") or []) if isinstance(u, dict)]
    if not units:
        return ToolResult.fail(f"课程 `{folder}` 里没有单元，无法出题（可能备课不完整，建议重新备课）。")

    try:
        index = int(args.get("unit_index") or storage.next_unit_index(folder))
    except Exception:
        index = 1
    index = max(1, min(index, len(units)))
    unit = units[index - 1]
    unit_title = str(unit.get("title") or f"第 {index} 课")
    concept = (unit.get("key_points") or [unit_title])[0]
    count = max(1, min(int(args.get("count") or 5), 10))

    await ctx.push(_progress_notice(f"正在为《{unit_title}》出 {count} 道题…（约 30~90 秒）"))

    cfg = load_app_config()
    questions: List[Dict[str, Any]] = []
    try:
        from lesson_prep import _fallback_quiz, _is_meaningful_question, _normalize_quiz_questions
        from lesson_prep import generate_quiz_with_model

        raw = await asyncio.to_thread(generate_quiz_with_model, unit, "", cfg)
        if isinstance(raw, list):
            questions = [q for q in _normalize_quiz_questions(raw)
                         if isinstance(q, dict) and _is_meaningful_question(q)]
        if not questions:
            # 模型没出出来就退回「从教案要点生成」的兜底题，别让学生空手而归
            questions = [q for q in _fallback_quiz(unit) if isinstance(q, dict)]
    except Exception as exc:  # noqa: BLE001
        print(f"[course] 出题失败，改用兜底题：{exc}", flush=True)
        try:
            from lesson_prep import _fallback_quiz

            questions = [q for q in _fallback_quiz(unit) if isinstance(q, dict)]
        except Exception:
            questions = []

    if not questions:
        return ToolResult.fail("这次没能出出题来（模型可能没连上）。可以稍后重试，或我直接用教案里的通关问题口头提问。")

    questions = questions[:count]
    nodes = [_to_quiz_node(q, i, str(concept), ctx.session.id) for i, q in enumerate(questions, 1)]
    # 丢掉「标不出正确答案」的题：留在界面上学生点了没反应，比少一道题更糟
    judgeable = [n for n in nodes if any(o.get("correct") for o in n.get("options") or [])]
    dropped = [n for n in nodes if n not in judgeable]
    if dropped:
        # 把丢掉的原因写进日志：下次要调映射规则时不用再重新出题猜
        print("[course] 丢弃了标不出答案的题：", flush=True)
        for n in dropped:
            print(f"    {str(n.get('question'))[:80]} | 选项={[o.get('label') for o in n.get('options') or []]}", flush=True)
    if not judgeable:
        return ToolResult.fail(
            "这次出的题都没能标出正确答案（判不了分），已丢弃。建议重新出题；"
            "也可以我直接用教案里的通关问题口头提问。"
        )
    nodes = judgeable
    spec = {
        "title": f"随堂测验 · {unit_title}",
        "items": [
            {"type": "callout", "tone": "info", "title": "怎么用",
             "content": "点选项就会**当场判分**并给出解析；成绩会自动记进你的学习档案（不用告诉老师）。"},
            *nodes,
        ],
    }
    answer_key = "\n".join(
        f"  {i}. {q.get('question')} → 答案：{q.get('answer')}"
        + (f"（解析：{str(q.get('explanation') or '')[:60]}）" if q.get("explanation") else "")
        for i, q in enumerate(questions, 1)
    )
    return ToolResult(
        summary=(
            f"已出 {len(nodes)} 道题（第 {index} 课《{unit_title}》，考查点：{concept}）"
            + (f"；另有 {len(dropped)} 道因标不出正确答案已丢弃" if dropped else "")
            + "。答题卡已显示给学生，**点选即判分**，成绩自动记入画像。\n"
            f"你这边的答案与解析（用于讲评，别说成「答案我念给你听」）：\n{answer_key}\n"
            "学生做完后，针对错题讲评并追问；如果连续答错，就换一种类比重讲。"
        ),
        ui=spec,
        data={"folder": folder, "unit_index": index, "count": len(nodes), "dropped": len(dropped)},
    )
