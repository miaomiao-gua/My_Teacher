import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


def _strip_thinking_residue(text: str) -> str:
    """剥离模型思考残留（qwen3 等即使 think=false 也可能输出 <|thinking|> / </think> / [thinking] 块）。"""
    if not text:
        return text
    for pattern in (
        r"<\|thinking\|>[\s\S]*?<\|/thinking\|>",
        r"<thinking>[\s\S]*?</thinking>",
        r"\[thinking\][\s\S]*?\[/thinking\]",
        r"\[think\][\s\S]*?\[/think\]",
        r"<\|thinking\|>[\s\S]*",
    ):
        text = re.sub(pattern, "", text)
    # 段级独白：独立成段的 [think]（行首或 \c 分段标记之后，到 \c / 空行 / 结尾为止）
    text = re.sub(r"(?:^|\n|\\c)\s*\[think\][\s\S]*?(?=\\c|\n\s*\n|\Z)", "\n", text)
    # 单独闭合/开始标签（含 [think] / [/think] 裸标记）
    text = re.sub(r"</?think>|<\|/?thinking\|>|\[/?think(?:ing)?\]", "", text, flags=re.IGNORECASE)
    return text.strip()


# qwen3 在 think=false 时仍会以"用户要求我…""我的任务是…"这类内心独白开头，
# 污染回复正文与出题表格。此正则只匹配"明确的任务复述/认知动词独白"句式，
# 不再包含"首先/重点/让我/作为老师/题目要求"等教学内容常用开场，避免误删正文。
# 实测 qwen3:4b 常见独白段：
#   "首先，用户说'你好'…作为艾琳老师，我需要以亲切的方式回应…"
#   "我的角色是给初中生讲…我应该避免使用太专业的术语…"
#   "作为AI，我需要确保回应符合…我可以从一个简单的例子开始…"
#   "用户说'你好'，我应该先回应问候…"
#   "可能的回应结果：1. 问候并表达热情…"
_THINK_LEAD_RE = re.compile(
    r"^\s*(?:"
    r"(?:(?:首先|好的|好|嗯|OK|明白了|收到|来了|开始|这样的话|那么)(?:[，,：:、\s]+)?)?"
    r"(?:"
    r"用户(?:要求|希望|提出|想要|需要|说)(?:我|我们|到)?|"  # 用户要求我…/用户说…
    r"(?:作为)(?:一个)?(?:AI|人工智能|老师|助教|虚拟老师|虚拟助教)(?:，|,|:)?\s*(?:我)?(?:需要|应该|要|将)|"  # 作为AI，我需要…
    r"(?:我的|本次|本|这)?(?:任务|工作|职责|角色)(?:要求|是|为|将|需要)|"  # 我的任务是…/我的角色是…
    r"我需要(?:来)?(?:先|现在)?(?:分析|思考|规划|设计|准备|回答|梳理|总结|确定|确保|回应|引导|处理)(?:一下|一遍|一个|这个|这道|如何|怎样)?|"  # 我需要先分析一下…/我需要处理…
    r"我应该(?:先|现在)?(?:回应|回答|引导|过渡|介绍|讲解|设计|考虑|保持|避免)(?:一下|一遍|一个|这个|如何|怎样|什么)?|"  # 我应该先回应…
    r"(?:我来|让我)(?:来)?(?:先|现在)?(?:想想|思考|规划|设计|准备|梳理|回应)(?:一下|一遍|一个|这个|这道|如何|怎样)?|"  # 我来/让我思考一下…
    r"(?:我可以|我打算|我准备)(?:从|用|以|先|来)?(?:一个|这个|以下|几种|这样|如下)?(?:例子|示例|方式|方法|角度|结构|流程|步骤|开场)?(?:开始|入手|来|进行|设计|安排|回答)?|"  # 我可以从一个简单的例子开始…
    r"可能的(?:回应|回答|方案|结果)(?:方式|列表|如下)?[：:]?"  # 可能的回应结果：
    r")"
    r")"
)

# 独白段一般都很短（一句半句），超过该长度视为实质教学内容，绝不剥离。
# qwen3 的独白段实测可达 100+ 字符（一句任务复述+一句策略），故从 90 放宽到 200。
_THINK_LEAD_MAX_LEN = 200


def _strip_thinking_lead(text: str) -> str:
    """剥离模型回答开头的思考/任务复述独白段（段级）。

    仅当"开头段落以强独白句式开头、且后面还有其他内容"时才剥离该段，
    逐段循环直到开头不再是独白；若整篇只有一段（独白与内容混排）则原样保留。
    段落超过 _THINK_LEAD_MAX_LEN 字符时视为教学内容，即使命中句式也不剥离，
    防止把"首先我们来看…""作为老师我建议…"这类正常讲课内容误删。
    """
    if not text:
        return text
    paragraphs = [p for p in str(text).split("\n\n") if p.strip()]
    while len(paragraphs) > 1:
        first = paragraphs[0].strip()
        if len(first) > _THINK_LEAD_MAX_LEN or not _THINK_LEAD_RE.search(first):
            break
        paragraphs = paragraphs[1:]
    return "\n\n".join(paragraphs).strip() or text


SILICONFLOW_URL = "https://api.siliconflow.cn/v1/chat/completions"
DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3"

# 单课资料正文最大长度（截断防 token 爆炸）
MAX_UNIT_CONTENT_CHARS = 8000

# 备课 system prompt（云端 / 本地 Ollama 共用）
#
# 设计原则：备课是"做教案"，讲课是"用教案"，二者完全解耦。
# - 备课阶段：模型按"两阶段思考链·阶段一"输出可执行的教案结构
#   （每个 unit 含 target 学习目标 + modules 讲解模块序列，每模块含 concept/example/interaction/action）。
# - 讲课阶段：app.py 的 /api/chat 注入这套教案作为强引导，模型按"两阶段思考链·阶段二"逐模块推进。
_LESSON_SYSTEM_PROMPT = (
    "你是一位顶级学科专家与课程设计师，同时承担「做教案」的职责。\n"
    "你的工作严格分为两个阶段，备课阶段你只需要做【阶段一】，不要做任何讲课。\n\n"
    "==================\n"
    "【阶段一：备课思考链（升级版 4 阶段）】\n"
    "==================\n"
    "目标：生成一份目标可量化、结构可拆解、每课可验收的完整教案。\n"
    "请严格按以下 4 个阶段生成，输出时把思考体现在 JSON 的结构里，不要输出思考过程。\n\n"
    "── 阶段一 · 定目标（课程级）──\n"
    "AI 内心活动：\n"
    "  \"这整门课学完，学生应该能做什么？我需要一个可量化的'终点线'。\"\n"
    "输出要求：\n"
    "  - course_target        ：一句话写清学完本课应掌握的核心知识/技能。\n"
    "  - acceptance_criteria  ：可验证的量化标准（如「完成一套10道A-Level M1选择题，正确率 ≥ 80%」或「能独立完成某类综合题」）。\n"
    "  - total_lessons        ：本课程计划课时数（整数）。\n\n"
    "── 阶段二 · 拆结构（单元级）──\n"
    "AI 内心活动：\n"
    "  \"这个目标要拆成几个独立的'单元'？每个单元讲什么？单元之间有先后依赖关系吗？\"\n"
    "输出要求：\n"
    "  - 每个单元聚焦 1 个核心概念（最多 3 个相关概念）。\n"
    "  - units[].prerequisites：标明依赖（如 [\"第2课\"]，没有则空数组 []）。\n\n"
    "── 阶段三 · 逐单元设计（每个单元独立思考 3 个维度）──\n"
    "维度 ① 核心公式 / 核心知识点（≤ 3 个）：\n"
    "  units[].core_formulas[]，每个元素：\n"
    "    { \"name\": \"公式/概念名\",\n"
    "      \"formula\": \"标准公式（如 s = vt；若为概念则写定义表达式）\",\n"
    "      \"variables\": \"变量说明（如 s: 位移, v: 速度, t: 时间）\" }\n"
    "  没有公式的概念课可用概念定义代替 \"formula\" 字段。\n"
    "维度 ② 单元通关问题（2~3 个，覆盖概念辨析/公式应用/简单计算）：\n"
    "  units[].gateway_questions[]：字符串数组，将作为单元测验/课堂提问素材库。\n"
    "维度 ③ 单元对最终目标的贡献：\n"
    "  units[].contribution_to_target：说明该单元对 course_target 的具体贡献；\n"
    "  若是前置基础，需写明它是后续单元的必要条件。\n\n"
    "── 阶段四 · 整合输出（最终教案结构）──\n"
    "AI 内心活动：\n"
    "  \"所有单元设计好了，现在把它们组装成完整教案。\"\n"
    "请按下方 schema 输出最终 JSON：\n"
    "{\n"
    '  "course_target": "课程级核心目标（一句话）",\n'
    '  "acceptance_criteria": "可量化的验收标准（含数字门槛或题型说明）",\n'
    '  "total_lessons": 12,\n'
    '  "topic": "主题",\n'
    '  "syllabus": "整体章节大纲（Markdown，### 第N课：标题 + 1-2句说明）",\n'
    '  "key_points": ["全局核心概念1", "全局核心概念2", ...],\n'
    '  "units": [\n'
    '    {\n'
    '      "title": "第 N 课：xxx",\n'
    '      "summary": "本课要点概述（1-2 句）",\n'
    '      "key_points": ["本课要点1", "本课要点2", "本课要点3", "本课要点4"],\n'
    '      "target": "本课能力目标（一句话）",\n'
    '      "prerequisites": ["第N-1 课（标题）"],  // 依赖列表，无依赖给 []\n'
    '      "core_formulas": [\n'
    '        {"name": "公式名", "formula": "标准写法", "variables": "变量说明"}\n'
    '      ],\n'
    '      "gateway_questions": [\n'
    '        "问题1（概念辨析/公式应用/简单计算）",\n'
    '        "问题2",\n'
    '        "问题3（可选）"\n'
    '      ],\n'
    '      "contribution_to_target": "本课对 course_target 的具体贡献；若是前置基础请说明为什么必要",\n'
    '      "modules": [\n'
    '        {\n'
    '          "id": "M1",\n'
    '          "title": "本模块的标题（动宾短语）",\n'
    '          "concept": "本模块要讲解的核心概念（1-2 句）",\n'
    '          "example": "用来做类比/演示的具体例子",\n'
    '          "anchor": "一句话记忆锚点",\n'
    '          "interaction": "本模块结束后安排的交互",\n'
    '          "action": "建议的 Live2D 动作描述"\n'
    '        }\n'
    '      ],\n'
    '      "contrasts": [\n'
    '        {"a": "易混淆概念A", "b": "易混淆概念B", "difference": "一句话区别"}\n'
    '      ],\n'
    '      "source_files": [\n'
    '        {"title": "资源标题", "url": "链接", "type": "pdf|docx|webpage|video", '
    '"platform": "video 类型时填写 bilibili 或 netease_open_course", '
    '"description": "简短说明", "markdown_content": "可选：公开文本直接给 Markdown 正文"}\n'
    '      ]\n'
    '    }\n'
    '  ],\n'
    '  "resources": ["全局备用资源（可选，结构与 source_files 一致）"]\n'
    "}\n\n"
    "硬性要求：\n"
    "1. units 至少 8 课，最多 16 课，由浅入深、循序渐进；total_lessons 必须等于 units 数组长度。\n"
    "2. 每个 unit 的 modules 至少 3 个、最多 6 个；module 数量足够把 target 拆解到位。\n"
    "3. 每个 module 的 concept/example/anchor/interaction/action 五个字段都必须有内容，禁止空字符串或省略。\n"
    "4. 每个 unit 必须填：\n"
    "   - core_formulas（1~3 项；超过 3 项会被截断）\n"
    "   - gateway_questions（2~3 题）\n"
    "   - contribution_to_target（1~2 句）\n"
    "   - prerequisites（依赖前置课时，没有给 []）\n"
    "5. 每个 unit 的 key_points 至少 4 个，source_files 至少 1 个真实可访问的资源链接"
    "（PDF 教材、官方文档、网页等），type 字段必须是 pdf/docx/webpage/video 之一。\n"
    "6. markdown_content 字段若资源是公开网页/文本，请直接给出关键段落 Markdown 正文（不超过 2000 字）。\n"
    "7. syllabus 字段需包含全部课时的标题列表，使用 ### 标记每课，格式为 ### 第N课：标题，并附上1-2句简要说明。\n"
    "8. key_points（全局）至少 5 个核心概念；resources（全局）至少 3 个高质量学习资源链接。\n"
    "9. 若你能搜索到与本课内容直接相关的公开课程视频，请把视频加入该课的 source_files："
    "type 固定为 video，platform 只能填 bilibili（B站，url 形如 https://www.bilibili.com/video/BV...）"
    "或 netease_open_course（网易公开课，url 形如 https://open.163.com/...）；"
    "url 必须是上述两个平台之一的真实视频页链接，找不到真实链接时不要编造。\n"
    "10. 若用户随主题提供了课程资料文档（Markdown），你必须基于该文档内容拆分单元、提炼 modules 的 concept/example，"
    "不要脱离文档凭空编造课程内容。\n"
    "11. 严禁在 JSON 中输出「思考过程」「分析」等元文本；只输出最终结构化教案。\n"
    "12. 本课若有常见的易混淆概念对（如速度vs速率、位移vs路程、质量vs重量、功率vs动能、库仑力vs电场力等），"
    "必须填入 unit 的 contrasts 字段（每个对比给出 a/b 两个概念与一句话 difference，供讲课阶段辨析用）；"
    "本课没有易混淆概念时，contrasts 填空数组 []。\n"
    "13. course_target 必须与各 unit 的 contribution_to_target 语义连贯：每个 unit 的贡献相加，应能支撑 course_target 的达成。\n"
)


def _fallback_lesson(topic: str, document_markdown: str = "") -> Dict[str, Any]:
    """本地兜底教案：单课结构，含 target + modules（教案骨架），不含题目。

    即使云端/Ollama 不可用，也要保证落盘的数据满足"阶段二讲课"的最低要求：
    每个 unit 有 target + modules[]（id/title/concept/example/anchor/interaction/action）。
    """
    doc_summary = ""
    if document_markdown:
        lines = [ln.strip() for ln in document_markdown.splitlines() if ln.strip()]
        doc_summary = "\n".join(lines[:20])
        if len(lines) > 20:
            doc_summary += "\n…"
    sample_unit = {
        "title": f"{topic} 概览",
        "summary": f"了解 {topic} 的核心概念、关键方法和实践要点。",
        "key_points": ["掌握主题的核心定义", "理解关键流程和步骤", "能够结合实例进行实践"],
        "target": f"理解 {topic} 的核心概念，并能在简单场景中加以应用。",
        # 升级版备课思考链：新增 4 个 unit 级字段（兜底时不硬编真实数据）
        "prerequisites": [],
        "core_formulas": [],
        "gateway_questions": [
            f"你能用自己的话解释 {topic} 的核心定义吗？",
            f"{topic} 与你之前学过的哪些概念有联系？",
        ],
        "contribution_to_target": f"建立 {topic} 的整体认知框架，是后续深入学习的基础。",
        "modules": [
            {
                "id": "M1",
                "title": f"理解 {topic} 的基本定义",
                "concept": f"掌握 {topic} 的核心定义、适用场景与边界。",
                "example": "先用一个生活中的类比建立直观印象，再对应到学科概念。",
                "anchor": f"一句话：{topic} 是用来解决某一类问题的标准化思路。",
                "interaction": "提问：你之前在哪里遇到过类似的概念？",
                "action": "点头致意，表示欢迎进入本课",
            },
            {
                "id": "M2",
                "title": f"{topic} 的关键方法与步骤",
                "concept": f"拆解 {topic} 的标准处理流程，了解每一步的目的与产物。",
                "example": "用一个最小例子走完整套流程，标注每一步对应概念。",
                "anchor": f"一句话：流程是 {topic} 的骨架，每一步都不能跳过。",
                "interaction": "小测验：哪一步是必要的，哪一步可以省略？",
                "action": "指向黑板，演示流程图",
            },
            {
                "id": "M3",
                "title": f"在实例中应用 {topic}",
                "concept": f"把 {topic} 应用到真实问题，掌握常见易错点。",
                "example": "选一个贴近学生生活的案例，完整应用一次并讨论可能的变化。",
                "anchor": f"一句话：{topic} 的价值在于把同类问题模板化解决。",
                "interaction": "代码/案例练习：让学生自己尝试一次。",
                "action": "绕圈手势，强调重复与循环",
            },
        ],
        # 兜底不硬编真实易混淆概念对（真实备课由模型按主题生成）
        "contrasts": [],
        "source_files": [
            {
                "title": f"{topic}参考资料",
                "url": "https://example.com/lesson-resource.pdf",
                "type": "pdf",
                "description": "参考教材与讲义（示例链接，可在设置中配置云端 API 后获取真实资源）",
                "markdown_content": (f"# 参考资料\n\n（来自用户上传文档）\n\n{doc_summary}" if doc_summary
                                     else "# 参考资料\n\n这是一个示例资料链接，指向公开示例页面。"),
            }
        ],
    }
    return {
        "topic": topic,
        "course_target": f"理解 {topic} 的核心概念，并能在简单场景中加以应用。",
        "acceptance_criteria": "完成 5 道围绕本课概念的辨析/应用题，正确率 ≥ 80%。",
        "total_lessons": 1,
        "syllabus": f"# {topic}\n\n## 1. 基础概念\n- 理解主题的基本定义\n\n## 2. 关键方法\n- 掌握实践步骤与常见误区\n\n## 3. 进阶应用\n- 结合真实案例进行训练",
        "key_points": sample_unit["key_points"],
        "resources": sample_unit["source_files"],
        "units": [sample_unit],
    }


def _strip_code_fence(text: str) -> str:
    """Remove markdown code fences around JSON if present."""
    cleaned = text.strip().lstrip("\ufeff\u200b\u200e").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        # remove optional language tag like "json"
        cleaned = re.sub(r"^[a-zA-Z]+\s*\n", "", cleaned, count=1)
        cleaned = cleaned.strip("`").strip()
    return cleaned


def _json_error_context(text: str, exc: json.JSONDecodeError) -> str:
    """从 JSON 解析异常中提取错误位置附近的原文片段，方便日志定位。"""
    if not text:
        return "N/A"
    pos = getattr(exc, "pos", None)
    if pos is None:
        return text[:300]
    lo = max(0, pos - 120)
    hi = min(len(text), pos + 120)
    return f"...{text[lo:hi]}... (pos={pos})"


def _fix_json_literal_newlines(text: str) -> str:
    """字符串感知地把 JSON 字符串值内部的裸换行/回车/Tab 转义为合法 JSON。

    模型生成 JSON 时常把多行文本直接写进字符串值（未转义的真实换行），
    导致 json.loads 报 "Expecting ',' delimiter" 之类错误。这里逐字符扫描，
    仅在字符串内部（in_string）转义裸控制字符，不触碰结构部分。
    """
    out: List[str] = []
    in_string = False
    escaped = False
    for ch in text:
        if in_string:
            if escaped:
                out.append(ch)
                escaped = False
                continue
            if ch == "\\":
                out.append(ch)
                escaped = True
                continue
            if ch == '"':
                in_string = False
                out.append(ch)
                continue
            if ch == "\n":
                out.append("\\n")
                continue
            if ch == "\r":
                out.append("\\r")
                continue
            if ch == "\t":
                out.append("\\t")
                continue
            out.append(ch)
            continue
        # 结构部分（对象/数组/冒号/逗号/花括号之外）
        if ch == '"':
            in_string = True
        out.append(ch)
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """字符串感知地移除 JSON 尾逗号（{...,} 与 [...,]），模型常见错误。"""
    out: List[str] = []
    in_string = False
    escaped = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i = j  # 逗号后的结构字符直接续上（丢弃逗号及其间空白）
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _repair_truncated_json(text: str) -> List[str]:
    """对可能被 max_tokens 截断的 JSON 生成修复候选。

    原理：正向扫描跟踪未闭合的结构括号栈与字符串状态，记录若干"结构安全点"
    （该点之后字符串已闭合、括号处于可补全状态），从尾部截断到安全点并补上
    缺失的闭合括号，生成候选。外层依次尝试 json.loads，越靠后（内容越完整）
    的候选优先。
    """
    text = str(text).rstrip()
    if not text:
        return []
    stack: List[str] = []
    in_string = False
    escaped = False
    safe_points: List[tuple[int, List[str]]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch in "{[":
            stack.append(ch)
            i += 1
            continue
        if ch in "}]":
            if stack:
                stack.pop()
            i += 1
            continue
        # 结构边界（值结束/逗号/冒号/空白等）均可作为截断点，记录靠后的若干
        safe_points.append((i, list(stack)))
        i += 1
    if not stack and not in_string:
        return []  # 括号已完全闭合，不是截断问题
    candidates: List[str] = []
    seen = set()
    for pos, st in reversed(safe_points[-200:]):
        prefix = text[:pos].rstrip()
        if prefix.endswith(","):
            prefix = prefix[:-1].rstrip()
        if not prefix:
            continue
        closing = "".join(reversed(["]" if c == "[" else "}" for c in st]))
        cand = prefix + closing
        key = (len(cand), cand[:60])
        if key in seen:
            continue
        seen.add(key)
        candidates.append(cand)
    return candidates


def _robust_json_loads(text: str) -> Any:
    """容错解析模型输出的 JSON（备课 / 出题共用）。

    依次尝试：剥围栏直接解析 → 截取最外层 {…} → 字符串内裸控制字符转义 →
    尾逗号移除 → 组合修复。全部失败则抛出携带"最近一次真实失败位置"的异常，
    便于日志定位模型到底哪里写坏了 JSON。
    """
    cleaned = _strip_code_fence(text).strip()
    candidates: List[str] = [cleaned]
    # 若正文前后混有说明文字/围栏残留，截取第一个 { 到最后一个 } 之间的部分
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start:end + 1])
    transforms = [
        lambda s: s,
        _fix_json_literal_newlines,
        _strip_trailing_commas,
        lambda s: _strip_trailing_commas(_fix_json_literal_newlines(s)),
    ]
    last_err: Optional[json.JSONDecodeError] = None
    for cand in candidates:
        for t in transforms:
            fixed = t(cand)
            if fixed is None:
                continue
            try:
                return json.loads(fixed)
            except json.JSONDecodeError as e:
                last_err = e
    # 追加截断修复：模型输出被 max_tokens 截断、JSON 尾部残缺时，砍掉残缺片段并补闭合括号
    for cand in candidates:
        for repaired in _repair_truncated_json(cand):
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                continue
    detail = f"（最近一次失败 pos={last_err.pos}）" if last_err else ""
    raise json.JSONDecodeError(f"所有容错策略均失败{detail}", cleaned[:500], last_err.pos if last_err else 0)


MIN_UNITS = 8  # 与 system prompt 硬性要求一致


def _units_target(data: Dict[str, Any], toc_count: int = 0) -> int:
    """目标课数：优先按书籍目录顶层章节数（1~40）；其次 total_lessons（至少 8）；否则 8。"""
    if toc_count and toc_count > 0:
        return min(max(int(toc_count), 1), 40)
    try:
        total = int(data.get("total_lessons") or 0)
    except (TypeError, ValueError):
        total = 0
    return max(total, MIN_UNITS)


def _count_toc_chapters(toc) -> int:
    """统计书籍目录的顶层章节数：优先 level 0 条目；无 level 0 时取最小 level。

    只计"像章节"的顶层条目（以「第N章/第N讲/第N课/N. /Chapter N」开头），
    过滤掉前言/附录/版权页等非章节页；若一条都没匹配则回退为全部顶层条目数。
    """
    if not isinstance(toc, list) or not toc:
        return 0
    items = [it for it in toc if isinstance(it, dict)]
    if not items:
        return 0
    levels = [int(it.get("level") or 0) for it in items]
    top = 0 if 0 in levels else min(levels)
    top_items = [it for it in items if int(it.get("level") or 0) == top]
    chapter_like = [
        it for it in top_items
        if re.match(r"^第\s*[\d〇零一二三四五六七八九十百千]+\s*[章讲节课]|^\d+\s*[\.、]|^[Cc]hapter\s*\d+", str(it.get("title") or "").strip())
    ]
    return len(chapter_like) if chapter_like else len(top_items)


def _extract_syllabus_titles(syllabus: str) -> List[str]:
    """从 syllabus Markdown 中提取课时标题（### 第N课：xxx / ## N. xxx 等），用于缺课补齐。"""
    if not syllabus:
        return []
    titles: List[str] = []
    for m in re.finditer(r"^#{2,4}\s*([^\n]+)$", syllabus, re.MULTILINE):
        title = m.group(1).strip()
        # 只收"第N课/N."这类明确课时标题，不收普通小标题
        if re.match(r"^第\s*\d+\s*[课讲章]", title) or re.match(r"^\d+\s*[\.、]", title):
            titles.append(title)
    return titles


def _extract_titles_from_syllabus(syllabus: str) -> List[Dict[str, str]]:
    """从 syllabus Markdown 中提取课时标题，返回 [{title}] 列表。

    与 _extract_syllabus_titles 行为相同但返回结构化格式，供两阶段备课阶段1 兜底用。
    """
    titles = _extract_syllabus_titles(syllabus)
    return [{"title": t} for t in titles]


def _build_outline_message(topic: str, document_markdown: str, toc_count: int) -> str:
    """阶段1 用户消息：只要求大纲（course_target / syllabus / key_points / units 标题）。

    阶段2 会逐课单独请求详细 modules，所以阶段1 的 units 数组里只需要 title/summary/key_points/prerequisites。
    """
    if toc_count > 0:
        lesson_req = (
            f"你收到了用户上传的整本教材，其目录共有 {toc_count} 个顶层章节。"
            f"请严格按照目录生成恰好 {toc_count} 个 unit（每章对应一课）："
            f"unit 的 title 必须与目录章节标题一一对应（如「第一章 xxx」→「第1课：xxx」），"
            f"禁止合并章节，也禁止凭空增减课时；total_lessons 必须等于 {toc_count}。"
        )
    else:
        lesson_req = "共 12 课（允许 8~16 课）"
    doc_section = ""
    if document_markdown:
        doc = document_markdown[:40000]
        doc_section = (
            f"\n以下是用户上传的课程资料文档（Markdown，请以此为准）：\n"
            f"--- 课程资料开始 ---\n{doc}\n--- 课程资料结束 ---\n"
        )
    return (
        f"请为【{topic}】设计【阶段一·大纲】：{lesson_req}。"
        f"{doc_section}"
        f"\n【本阶段只输出大纲骨架】，按下方 schema 输出 JSON：\n"
        f"{{\n"
        f'  "course_target": "课程级核心目标（一句话）",\n'
        f'  "acceptance_criteria": "可量化的验收标准",\n'
        f'  "total_lessons": <整数，=units 长度>,\n'
        f'  "topic": "{topic}",\n'
        f'  "syllabus": "### 第1课：xxx\\n### 第2课：xxx\\n...(全部课时标题，Markdown 列表)",\n'
        f'  "key_points": ["全局核心概念1", "全局核心概念2", ...至少 5 个],\n'
        f'  "units": [\n'
        f"    {{\"title\": \"第 N 课：xxx\", \"summary\": \"本课1-2句概述\", \"key_points\": [\"本课要点1\", \"本课要点2\"], \"prerequisites\": [\"第N-1课（标题）\"]}}\n"
        f"    ...(必须与 total_lessons 完全一致，每个 unit 只含这 4 个字段，不要写 modules/quiz/source_files)\n"
        f"  ]\n"
        f"}}\n"
        f"【重要】units 数组必须完整闭合，禁止只写几课就结束。\n"
    )


def _build_unit_detail_message(topic: str, seed: Dict[str, Any], document_markdown: str, total: int,
                                 diagnosis: Optional[Dict[str, Any]] = None) -> str:
    """阶段2 单课请求用户消息：基于阶段1 的标题/summary，要求详细 modules。

    每课单独一次请求：max_tokens=2048、字段精简到 30~60 字，确保必能完整闭合 JSON。
    diagnosis 包含已知/部分/未知概念列表，让模型对未知概念讲得更详细、对已知概念快速带过。
    """
    title = str(seed.get("title") or "").strip()
    summary = str(seed.get("summary") or "").strip()
    key_points = list(seed.get("key_points") or [])
    prerequisites = list(seed.get("prerequisites") or [])
    # 诊断上下文：只在本课相关概念上展开
    diag_section = ""
    if diagnosis and isinstance(diagnosis, dict):
        known = list(diagnosis.get("known") or [])
        partial = list(diagnosis.get("partial") or [])
        unknown = list(diagnosis.get("unknown") or [])
        if known or partial or unknown:
            diag_section = (
                "\n【学生摸底结果】（来自交互式备课诊断，仅本课相关）：\n"
                f"  - 已掌握：{'; '.join(known) or '（无）'}\n"
                f"  - 部分掌握：{'; '.join(partial) or '（无）'}\n"
                f"  - 未掌握：{'; '.join(unknown) or '（无）'}\n"
                "请据此调整讲解深度：未掌握的概念要详细解释（含公式推导、生活类比、易错点），"
                "已掌握的概念只做 1 句话的快速回顾即可；部分掌握的概念重点讲缺失部分。\n"
            )
    # 单课请求不带文档（节省 token）：阶段1 已经把文档上下文喂给了模型
    return (
        f"你正在为【{topic}】编写【第 {seed.get('index', 0) + 1}/{total} 课】的详细教案。\n"
        f"主题：{title}\n"
        f"概述：{summary}\n"
        f"要点：{'；'.join(key_points) or '（无）'}\n"
        f"前置：{'；'.join(prerequisites) or '无'}\n"
        f"{diag_section}\n"
        f"按下方 schema 输出 JSON（必须以右花括号闭合，禁止截断）：\n"
        f"{{\n"
        f'  "target": "本课能力目标（一句话）",\n'
        f'  "contribution_to_target": "本课对总目标的贡献（1句）",\n'
        f'  "core_formulas": [\n'
        f'    {{"name": "公式/概念名", "formula": "标准写法", "variables": "变量说明"}}\n'
        f"    ...(1~3 项)\n"
        f"  ],\n"
        f'  "gateway_questions": ["问题1", "问题2", "问题3"],\n'
        f'  "modules": [\n'
        f'    {{"id": "M1", "title": "模块标题", "concept": "本模块核心概念（30~60字）",\n'
        f'     "example": "演示例子（30~60字）", "anchor": "一句话记忆锚点",\n'
        f'     "interaction": "本模块的提问/互动", "action": "建议的 Live2D 动作"}},\n'
        f"    ...(必须有 3 个 modules)\n"
        f"  ],\n"
        f'  "contrasts": [{{"a":"易混A","b":"易混B","difference":"一句话区别"}}]\n'
        f"}}\n"
        f"【精简】每个字段不要超过 60 字；modules 严格 3 个；只输出这一个 JSON 对象，"
        f"禁止包含 units 数组或解释性文字。"
    )


def _build_user_message(topic: str, document_markdown: str = "", toc_count: int = 0) -> str:
    """构造备课的用户消息：无文档时只给主题；有文档时附上文档全文并要求基于文档拆分。

    强调"完整输出全部课时"：模型（尤其 DeepSeek-V3）经常只写几个 unit 就提前闭合 JSON，
    因此用户消息里必须显式要求课数与"禁止提前结束"。
    toc_count > 0 时（用户上传了整本教材且成功提取目录）：必须按目录顶层章节数
    生成相同数量的 unit，每章对应一课，禁止合并/增减章节。
    """
    if toc_count > 0:
        lesson_req = (
            f"你收到了用户上传的整本教材，其目录共有 {toc_count} 个顶层章节。"
            f"请严格按照目录生成恰好 {toc_count} 个 unit（每章对应一课）："
            f"unit 的 title 必须与目录章节标题一一对应（如「第一章 xxx」→「第1课：xxx」），"
            f"禁止合并章节，也禁止凭空增减课时；total_lessons 必须等于 {toc_count}。"
        )
    else:
        lesson_req = "共 12 课（允许 8~16 课）"
    tail = (
        f"【重要】units 数组必须完整包含全部课时：写出多少个标题，就必须有多少个 unit 对象，"
        f"禁止只写部分单元就提前闭合 JSON 结束输出。"
        f"【精简要求】控制总输出篇幅：每课 modules 的 concept/example/anchor/interaction/action 各 40~80 字，"
        f"key_points 每课 3~5 条、每条不超过 20 字，summary 不超过 50 字，"
        f"确保全部课时的 JSON 能在一次生成内完整闭合，避免因输出过长被截断。"
    )
    if not document_markdown:
        return (
            f"请为【{topic}】设计一份分课教案，{lesson_req}，"
            f"每课独立含资源与要点。\n{tail}"
        )
    doc = document_markdown[:60000]  # 截断防 token 爆炸
    return (
        f"以下是用户上传的课程资料文档（Markdown，请以此为准，不要脱离文档编造内容）：\n\n"
        f"--- 课程资料开始 ---\n{doc}\n--- 课程资料结束 ---\n\n"
        f"请基于以上文档为【{topic}】设计一份分课教案，{lesson_req}，"
        f"每课独立含资源与要点。\n{tail}"
    )


def _mark_fallback(data: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """给备课结果打上"兜底模板"标记，供上层 API 向前端提示（避免用户误以为模板=真实教案）。"""
    data["_prepared_fallback"] = True
    data["_prepared_reason"] = reason
    return data


def _call_siliconflow(topic: str, config: Dict[str, Any] | None = None, document_markdown: str = "",
                      toc_count: int = 0, diagnosis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = config or {}
    api_key = (config.get("cloud_api_key") or config.get("siliconflow_api_key") or os.getenv("SILICONFLOW_API_KEY", "")).strip()
    if not api_key:
        return _mark_fallback(
            _fallback_lesson(topic, document_markdown),
            "未配置云端 API Key，请到「设置」中填写后再备课",
        )

    base_url = (config.get("cloud_base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
    if not base_url.endswith("/chat/completions"):
        url = f"{base_url}/chat/completions"
    else:
        url = base_url

    model = (config.get("cloud_model") or config.get("siliconflow_model") or DEFAULT_MODEL).strip()
    # 备课不联网搜索：教案内容应来自模型知识 + 上传文档，搜索会显著拖慢响应（实测 180s 超时回退）
    enable_search = False

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # 让云端模型按"分课"结构生成教案：units 数组里每一课独立持有 title/summary/key_points/source_files
    # 注意：不再要求模型生成题目，题目由后续单独流程结合人格提示词生成
    system_content = (config or {}).get("lesson_prompt") or _LESSON_SYSTEM_PROMPT

    # 累计 token 用量（首次 + 重试请求），随结果返回给上层展示
    usage_acc = {"prompt_tokens": 0, "completion_tokens": 0}

    def _request_once(user_content: str, max_tokens: int = 4096, temperature: float = 0.7,
                       label: str = "备课") -> Dict[str, Any]:
        """发送一次备课请求并解析 JSON，成功返回 dict，失败返回 {}。

        用户消息可被任意构造（含大纲/单课展开），不再绑死 _build_user_message。
        """
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        logger.info(
            "📦 备课·[%s] 云端请求包: model=%s max_tokens=%s topic=%r toc_count=%s",
            label, model, max_tokens, topic, toc_count,
        )
        logger.info(
            "📦 备课·[%s] system_prompt=%d字 user_message=%d字",
            label, len(system_content), len(user_content),
        )
        t0 = time.time()
        response = requests.post(url, headers=headers, json=payload, timeout=300)
        elapsed = time.time() - t0
        logger.info("📦 备课·[%s] 云端响应: status=%s 耗时=%.1fs", label, response.status_code, elapsed)
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        usage = result.get("usage") or {}
        usage_acc["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        usage_acc["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        logger.info("📦 备课·[%s] 内容: raw_len=%d preview=%s usage=%s",
                    label, len(content), content[:200], usage)
        text = _strip_code_fence(content)
        data = _robust_json_loads(text)
        if isinstance(data, dict):
            return data
        logger.warning("⚠️ 备课·[%s] 解析结果不是 dict: %s", label, type(data))
        return {}

    # ============ 两阶段生成：阶段1 拿大纲，阶段2 逐课展开 ============
    try:
        # ----- 阶段1：只要大纲（course_target/syllabus/key_points/units 标题） -----
        outline_msg = _build_outline_message(topic, document_markdown, toc_count)
        outline = _request_once(outline_msg, max_tokens=2048, temperature=0.7, label="阶段1·大纲")
        if not outline:
            raise json.JSONDecodeError("阶段1 未返回合法 JSON 对象", "", 0)
        units_seed = outline.get("units") or []
        # 从 syllabus 兜底提取标题（应对模型把 units 写成空但 syllabus 完整的情况）
        if not units_seed:
            units_seed = _extract_titles_from_syllabus(outline.get("syllabus") or "")
        n_seed = len(units_seed) if isinstance(units_seed, list) else 0
        target = _units_target(outline, toc_count)
        # 如果模型在阶段1 已给出 N 课，但少于目标，按 N 来（避免硬要补齐到过多数）
        if n_seed > 0:
            target = min(target, max(n_seed, MIN_UNITS))
        logger.info("✅ 阶段1·大纲完成 units_seed=%s target=%s", n_seed, target)

        # ----- 阶段2：每课单独请求精简详细模块（modules 3 个、字段 30~60 字） -----
        unit_titles = []
        for i in range(target):
            seed = units_seed[i] if i < n_seed and isinstance(units_seed[i], dict) else {}
            title = str(seed.get("title") or f"第 {i + 1} 课").strip()
            unit_titles.append({
                "index": i,
                "title": title,
                "summary": str(seed.get("summary") or "").strip(),
                "key_points": list(seed.get("key_points") or []),
                "prerequisites": list(seed.get("prerequisites") or []),
            })
        # 并发批量请求每课详情（max_workers=3 避免对硅基 QPS 太高）
        from concurrent.futures import ThreadPoolExecutor, as_completed
        detail_results: Dict[int, Dict[str, Any]] = {}

        def _fetch_one(i: int, seed: Dict[str, Any]) -> tuple:
            msg = _build_unit_detail_message(topic, seed, document_markdown, target, diagnosis=diagnosis)
            try:
                d = _request_once(msg, max_tokens=2048, temperature=0.7, label=f"阶段2·第{i+1}课")
            except Exception as exc:
                logger.error("❌ 阶段2·第%d课异常: %s: %s", i + 1, type(exc).__name__, exc)
                d = {}
            return i, d

        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = [ex.submit(_fetch_one, i, s) for i, s in enumerate(unit_titles)]
            for fut in as_completed(futures):
                i, d = fut.result()
                detail_results[i] = d if isinstance(d, dict) else {}
        logger.info("✅ 阶段2·逐课展开完成 成功%d/%d课", sum(1 for v in detail_results.values() if v), target)

        # 拼装最终 units：阶段1 大纲 + 阶段2 详情，缺失字段用阶段1 seed 兜底
        units_out = []
        for i in range(target):
            seed = unit_titles[i]
            detail = detail_results.get(i) or {}
            merged = dict(seed)  # title/summary/key_points/prerequisites 来自阶段1
            for k in ("target", "core_formulas", "gateway_questions",
                      "contribution_to_target", "modules", "contrasts",
                      "source_files", "quiz_preset"):
                v = detail.get(k)
                if v:
                    merged[k] = v
            units_out.append(merged)

        data = dict(outline)  # 保留阶段1 的 course_target / acceptance_criteria / total_lessons / syllabus / key_points / resources
        data["units"] = units_out
        logger.info("✅ 备课·解析成功 units=%s", len(units_out))
        data["_usage"] = dict(usage_acc)
        data["_model"] = model
        return data
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if (e.response is not None) else "?"
        fail_reason = f"云端接口返回 {status}"
        if status == 401:
            fail_reason += "（API Key 无效或过期）"
        logger.error("❌ 备课·HTTP 错误: %s 响应体: %s", e,
                     getattr(e.response, "text", "")[:500] if e.response is not None else "N/A")
    except requests.exceptions.ConnectionError as e:
        fail_reason = "无法连接云端服务"
        logger.error("❌ 备课·连接错误: %s", e)
    except requests.exceptions.Timeout:
        fail_reason = "云端备课超时（300s）"
        logger.warning("⚠️ 备课·请求超时(300s) 已回退兜底")
    except json.JSONDecodeError as e:
        _ctx = _json_error_context(e.doc, e)
        fail_reason = "AI 返回内容解析失败"
        logger.error("❌ 备课·JSON 解析失败: %s\n   错误上下文: %s", e, _ctx)
        # 把原始响应落盘，便于定位模型写坏 JSON 的位置
        try:
            dump_dir = Path(__file__).resolve().parent.parent / "data" / "debug_logs"
            dump_dir.mkdir(parents=True, exist_ok=True)
            dump_path = dump_dir / f"lesson_failed_raw_{time.strftime('%Y%m%d_%H%M%S')}.txt"
            dump_path.write_text(str(e.doc)[:200000], encoding="utf-8")
            logger.error("🗃️ 备课·原始响应已保存: %s", dump_path)
        except Exception as _dump_err:
            logger.error("⚠️ 备课·原始响应落盘失败: %s", _dump_err)
        # JSON 解析失败（多为输出过长被截断或写坏）：自动重试一次，要求精简 + 完整闭合
        try:
            retry = _request_once(
                "【重要更正】你上一次的输出 JSON 不完整（很可能因输出过长被截断）。"
                "本次必须：1) 精简每个字段（concept/example/anchor/interaction/action 各 40~80 字，"
                "key_points 每条不超过 20 字，summary 不超过 50 字），严格控制总篇幅；"
                "2) 输出结束前必须把 JSON 正确完整闭合；3) units 完整包含全部课时，禁止只写几课就结束。"
            )
        except Exception as exc2:
            retry = {}
            logger.error("❌ 备课·JSON失败重试异常: %s: %s", type(exc2).__name__, exc2)
        if isinstance(retry, dict) and retry:
            n_retry = len(retry.get("units") or []) if isinstance(retry.get("units"), list) else 0
            logger.info("✅ 备课·JSON失败后重试成功 units=%s", n_retry)
            return retry
        logger.warning("⚠️ 备课·JSON失败后重试仍未成功，回退兜底")
    except Exception as e:
        fail_reason = f"未知错误：{type(e).__name__}"
        logger.error("❌ 备课·未知错误: %s: %s", type(e).__name__, e)
        import traceback
        traceback.print_exc()

    logger.warning("⚠️ 备课·云端失败，回退到 _fallback_lesson(topic=%r)", topic)
    return _mark_fallback(_fallback_lesson(topic, document_markdown), fail_reason)


def _call_ollama_lesson(topic: str, config: Dict[str, Any] | None = None, document_markdown: str = "",
                        toc_count: int = 0, diagnosis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """用本地 Ollama 生成分课教案（与云端共用同一 system prompt，去掉 enable_search）。"""
    config = config or {}
    base_url = (config.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
    model = (config.get("ollama_model") or "qwen2.5:7b").strip()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": (config or {}).get("lesson_prompt") or _LESSON_SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_message(topic, document_markdown, toc_count)},
        ],
        "stream": False,
        # qwen3 系列默认开启 thinking：4B 模型思考会大量消耗时间/可能返回空正文，显式关闭
        "think": False,
        "options": {
            "temperature": 0.6,
            "num_ctx": int(config.get("ollama_num_ctx", 16384) or 16384),
            "num_predict": int(config.get("ollama_num_predict", 8192) or 8192),
        },
    }
    try:
        logger.info("📦 备课·Ollama请求包: %s/api/chat model=%s 文档%d字 toc_count=%s",
                    base_url, model, len(document_markdown), toc_count)
        resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=300)
        if not resp.ok:
            logger.error("❌ 备课·Ollama HTTP %s: %s", resp.status_code, resp.text[:300])
            return None
        resp_json = resp.json()
        content = (resp_json.get("message", {}).get("content") or "").strip()
        logger.info("📦 备课·Ollama响应: raw_content_len=%d preview=%s", len(content), content[:200])
        if not content:
            return None
        # 清理 qwen3 思考残留（即使 think=false 也可能输出 <|thinking|> / </think>）与开头独白
        content = _strip_thinking_residue(content)
        content = _strip_thinking_lead(content)
        text = _strip_code_fence(content)
        data = _robust_json_loads(text)
        if isinstance(data, dict):
            units = data.get("units", [])
            logger.info("✅ 备课·Ollama解析成功 units=%s", len(units) if isinstance(units, list) else "N/A")
            data["_usage"] = {
                "prompt_tokens": int(resp_json.get("prompt_eval_count") or 0),
                "completion_tokens": int(resp_json.get("eval_count") or 0),
            }
            data["_model"] = model
            return data
    except json.JSONDecodeError as exc:
        logger.error("❌ 备课·Ollama JSON解析失败: %s", exc)
    except Exception as exc:
        logger.error("❌ 备课·Ollama异常: %s: %s", type(exc).__name__, exc)
    return None


def _normalize_unit(unit: Dict[str, Any], fallback_index: int) -> Dict[str, Any]:
    """规范化单个 unit：补全字段、规整 quiz_preset 答案，并保证 target/modules 教案骨架完整。"""
    if not isinstance(unit, dict):
        unit = {}
    title = (unit.get("title") or f"第 {fallback_index + 1} 课").strip()
    summary = (unit.get("summary") or "").strip()
    key_points = unit.get("key_points") or []
    if not isinstance(key_points, list):
        key_points = []
    source_files = unit.get("source_files") or unit.get("resources") or []
    if not isinstance(source_files, list):
        source_files = []
    # 截断过长的 markdown_content，避免 metadata 体积过大
    for sf in source_files:
        if isinstance(sf, dict) and isinstance(sf.get("markdown_content"), str):
            if len(sf["markdown_content"]) > MAX_UNIT_CONTENT_CHARS:
                sf["markdown_content"] = sf["markdown_content"][:MAX_UNIT_CONTENT_CHARS] + "\n\n…（已截断）"
    quiz_preset = unit.get("quiz_preset") or []
    if not isinstance(quiz_preset, list):
        quiz_preset = []
    for quiz in quiz_preset:
        ans = str(quiz.get("answer", "")).strip()
        if ans:
            quiz["answer"] = ans[0].upper()

    # 教案骨架：target（学习目标）+ modules（讲解模块序列）
    target = (unit.get("target") or summary or f"理解并应用本课的核心概念").strip()
    raw_modules = unit.get("modules")
    modules: List[Dict[str, Any]] = []
    if isinstance(raw_modules, list):
        for mi, m in enumerate(raw_modules):
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or f"M{mi + 1}").strip() or f"M{mi + 1}"
            modules.append({
                "id": mid,
                "title": str(m.get("title") or "").strip(),
                "concept": str(m.get("concept") or "").strip(),
                "example": str(m.get("example") or "").strip(),
                "anchor": str(m.get("anchor") or "").strip(),
                "interaction": str(m.get("interaction") or "").strip(),
                "action": str(m.get("action") or "").strip(),
            })
    # 兜底：如果模型没给 modules，用 key_points 派生一份最简骨架，保证讲课 prompt 有东西可循
    if not modules:
        kp_fallback = [str(k).strip() for k in key_points if str(k).strip()] or ["核心概念"]
        modules = []
        for mi, kp in enumerate(kp_fallback[:5]):
            modules.append({
                "id": f"M{mi + 1}",
                "title": f"理解「{kp}」",
                "concept": kp,
                "example": "结合生活中的类比或最小例子说明。",
                "anchor": f"一句话：{kp} 是本课的关键要点之一。",
                "interaction": "提问：你对这个概念熟悉吗？",
                "action": "指向黑板",
            })
    # 过滤空字段模块
    modules = [m for m in modules if m.get("concept") or m.get("title")]
    if not modules:
        modules = [{
            "id": "M1",
            "title": "理解本课核心",
            "concept": summary or title,
            "example": "用一个生活例子帮助理解。",
            "anchor": "一句话：抓住本课主线。",
            "interaction": "提问：你有什么疑问？",
            "action": "点头",
        }]

    # 升级版备课思考链：新增 4 个 unit 级字段（prerequisites / core_formulas / gateway_questions / contribution_to_target / contrasts）
    prerequisites_raw = unit.get("prerequisites")
    prerequisites: List[str] = []
    if isinstance(prerequisites_raw, list):
        for p in prerequisites_raw:
            if p is None:
                continue
            ps = str(p).strip()
            if ps:
                prerequisites.append(ps)

    core_formulas_raw = unit.get("core_formulas")
    core_formulas: List[Dict[str, str]] = []
    if isinstance(core_formulas_raw, list):
        for cf in core_formulas_raw[:3]:  # 硬性要求 ≤ 3
            if not isinstance(cf, dict):
                continue
            name = str(cf.get("name") or "").strip()
            formula = str(cf.get("formula") or "").strip()
            variables = str(cf.get("variables") or "").strip()
            if name or formula:
                core_formulas.append({
                    "name": name,
                    "formula": formula,
                    "variables": variables,
                })

    gateway_questions_raw = unit.get("gateway_questions")
    gateway_questions: List[str] = []
    if isinstance(gateway_questions_raw, list):
        for q in gateway_questions_raw[:3]:  # 硬性要求 2~3 个
            if q is None:
                continue
            qs = str(q).strip()
            if qs:
                gateway_questions.append(qs)

    contribution_to_target = str(unit.get("contribution_to_target") or "").strip()

    # 易混淆概念对比（没有就给空数组）
    contrasts = unit.get("contrasts") or []
    if not isinstance(contrasts, list):
        contrasts = []
    cleaned_contrasts = []
    for c in contrasts:
        if isinstance(c, dict) and (c.get("a") or c.get("b")):
            cleaned_contrasts.append({
                "a": str(c.get("a") or "").strip(),
                "b": str(c.get("b") or "").strip(),
                "difference": str(c.get("difference") or "").strip(),
            })

    return {
        "title": title,
        "summary": summary,
        "key_points": key_points,
        "target": target,
        "prerequisites": prerequisites,
        "core_formulas": core_formulas,
        "gateway_questions": gateway_questions,
        "contribution_to_target": contribution_to_target,
        "modules": modules,
        "contrasts": cleaned_contrasts,
        "source_files": source_files,
        "quiz_preset": quiz_preset,
    }


def prepare_lesson(topic: str, config: Dict[str, Any] | None = None, document_markdown: str = "",
                   doc_toc: Optional[List[Dict[str, Any]]] = None,
                   diagnosis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a lesson plan payload and gracefully fall back if external API is unavailable.

    Args:
        topic: The lesson topic.
        config: Optional runtime config dict (from config.json). When provided,
                its cloud_api_key / cloud_model / cloud_base_url / enable_search
                are used instead of environment variables.
        document_markdown: Optional uploaded course document (Markdown). When provided,
                the model must split units based on this document.
        doc_toc: Optional PDF outline ([{title, page, level}]) of the uploaded textbook.
                When present, units are generated to match its top-level chapter count.
        diagnosis: Optional interactive diagnosis result from /api/prep_diagnose/finish.
                Dict with keys known / partial / unknown (list of concept strings).
                When present, the model is told to spend more detail on unknown concepts
                and quickly recap known concepts.

    Returns:
        dict with keys: topic, syllabus, key_points, resources, quiz_preset, units
        其中 units 是分课数组，每项含 title/summary/key_points/source_files/quiz_preset。
        旧字段 syllabus/key_points/resources/quiz_preset 保留兼容。
        _meta 内含 fallback / reason / warning / model / usage / toc_count / diagnosis。
    """
    provider = (str((config or {}).get("lesson_provider") or "cloud")).strip().lower()
    toc_count = _count_toc_chapters(doc_toc)
    logger.info("📦 备课·开始 provider=%s topic=%r 文档%d字 toc_count=%s",
                provider, topic, len(document_markdown), toc_count)
    prep_warning = ""
    if provider == "ollama":
        # 本地 Ollama 备课，失败回退云端
        data = _call_ollama_lesson(topic, config=config, document_markdown=document_markdown, toc_count=toc_count, diagnosis=diagnosis)
        if not data:
            logger.warning("⚠️ 备课·Ollama 备课失败，回退云端")
            prep_warning = "本地 Ollama 不可用，已自动改用云端备课"
            data = _call_siliconflow(topic, config=config, document_markdown=document_markdown, toc_count=toc_count, diagnosis=diagnosis)
    else:
        # cloud / auto：云端备课；失败（返回兜底标记）时若本地 Ollama 可用则自动回退
        data = _call_siliconflow(topic, config=config, document_markdown=document_markdown, toc_count=toc_count, diagnosis=diagnosis)
        if data.get("_prepared_fallback"):
            logger.warning("⚠️ 备课·云端失败，尝试回退本地 Ollama")
            local_data = _call_ollama_lesson(topic, config=config, document_markdown=document_markdown, toc_count=toc_count, diagnosis=diagnosis)
            if local_data:
                data = local_data
                prep_warning = "云端备课失败，已自动改用本地 Ollama 备课"
            else:
                prep_warning = "云端备课失败且本地 Ollama 不可用，已生成基础模板"
    data.setdefault("topic", topic)
    data.setdefault("syllabus", f"# {topic}\n\n请根据实际课程内容补充。")
    data.setdefault("key_points", ["基础概念", "核心原理", "实践应用"])
    data.setdefault("resources", [])
    data.setdefault("quiz_preset", [])

    # 记录本次备课实际使用的模型与 token 用量（供前端预览弹窗展示）
    model_used = data.pop("_model", "") or ""
    usage_used = data.pop("_usage", None) or {}

    # 规整 units：若云端未返回则从 syllabus 兜底成单课
    raw_units = data.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        # 把全局 resources / quiz_preset 合并到单课，保证旧逻辑可用
        raw_units = [
            {
                "title": topic,
                "summary": data.get("syllabus", "").splitlines()[0] if data.get("syllabus") else "",
                "key_points": data.get("key_points", []),
                "source_files": data.get("resources", []),
                "quiz_preset": data.get("quiz_preset", []),
            }
        ]
    units = [_normalize_unit(u, i) for i, u in enumerate(raw_units)]
    data["units"] = units

    # 数量兜底：模型经常只输出部分 unit（如 12 课只给 2 课）就闭合 JSON。
    # 不足目标课数时，从 syllabus 的课时标题列表补齐为最简骨架课，保证预览课数完整、可逐课编辑。
    target_units = _units_target(data, toc_count)
    if len(units) < target_units:
        titles = _extract_syllabus_titles(str(data.get("syllabus") or ""))
        global_kp = data.get("key_points") if isinstance(data.get("key_points"), list) else []
        kp_pool = [str(k).strip() for k in global_kp if str(k).strip()] or ["基础概念", "核心原理", "实践应用"]
        added = 0
        while len(units) < target_units:
            idx = len(units)
            title = titles[idx] if idx < len(titles) else f"第 {idx + 1} 课：进阶与实践"
            units.append(_normalize_unit({
                "title": title,
                "summary": f"{topic} 进阶内容：{title}",
                "key_points": [kp_pool[i % len(kp_pool)] for i in range(idx, idx + 3)],
            }, idx))
            added += 1
        data["units"] = units
        real_n = len(units) - added
        prep_warning = (
            f"{prep_warning}\n" if prep_warning else ""
        ) + f"模型只完整生成了 {real_n} 课，已按课程大纲自动补齐到 {target_units} 课（补出的课需在预览中补充要点）"
        logger.warning("⚠️ 备课·units不足已补齐: 实际%d课 → %d课（从 syllabus 提取标题）", real_n, target_units)

    # 反向同步：若全局 resources/quiz_preset 为空，从 units 汇总，保持向后兼容
    if not data.get("resources"):
        data["resources"] = [sf for u in units for sf in u.get("source_files", [])]
    if not data.get("quiz_preset"):
        data["quiz_preset"] = [q for u in units for q in u.get("quiz_preset", [])]

    # 规整全局 quiz_preset 的 answer
    for quiz in data.get("quiz_preset", []):
        ans = str(quiz.get("answer", "")).strip()
        if ans:
            quiz["answer"] = ans[0].upper()

    # 备课来源元信息（供 api_prepare_lesson 透传前端提示，不写入教案本身）
    data["_meta"] = {
        "fallback": bool(data.pop("_prepared_fallback", False)),
        "reason": data.pop("_prepared_reason", ""),
        "warning": prep_warning,
        "model": model_used,
        "usage": usage_used,
        "toc_count": toc_count,
        "diagnosis": diagnosis if diagnosis else None,
    }
    return data


def generate_quiz_with_model(
    unit_content: Dict[str, Any],
    personality_prompt: str = "",
    config: Dict[str, Any] | None = None,
    chat_history: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Use the model to generate quiz questions in the personality style.

    Args:
        unit_content: Dict with unit title, summary, key_points, source_files.
        personality_prompt: Personality style prompt.
        config: Cloud config dict.
        chat_history: Recent conversation history, used to make questions
            reflect what was actually discussed.

    Returns:
        List of quiz question dicts.
    """
    config = config or {}
    api_key = config.get("cloud_api_key", "").strip() or config.get("siliconflow_api_key", "").strip()
    if not api_key:
        return []

    base_url = (config.get("cloud_api_base_url") or "").strip() or SILICONFLOW_URL
    if config.get("cloud_provider") == "siliconflow":
        base_url = (config.get("siliconflow_base_url") or "").strip() or SILICONFLOW_URL

    model = (config.get("cloud_model") or config.get("siliconflow_model") or DEFAULT_MODEL).strip()

    unit_title = unit_content.get("title", "")
    unit_summary = unit_content.get("summary", "")
    key_points = unit_content.get("key_points", [])
    source_files = unit_content.get("source_files", [])

    # Build context from source_files
    context_parts = [f"## 单元：{unit_title}"]
    if unit_summary:
        context_parts.append(f"### 概述\n{unit_summary}")
    if key_points:
        context_parts.append(f"### 关键要点\n" + "\n".join(f"- {kp}" for kp in key_points))
    if source_files:
        md_contents = []
        for sf in source_files:
            if isinstance(sf, dict) and sf.get("markdown_content"):
                md_contents.append(f"### 资源：{sf.get('title', '')}\n{sf['markdown_content'][:3000]}")
        if md_contents:
            context_parts.append("\n".join(md_contents))

    # 注入聊天记录：让题目基于本课师生实际对话内容动态生成
    if chat_history:
        recent = chat_history[-20:]
        lines = []
        for m in recent:
            role = "学生" if m.get("role") == "user" else "老师"
            content = (m.get("content") or "").strip()
            if content:
                if len(content) > 400:
                    content = content[:400] + "…"
                lines.append(f"{role}：{content}")
        if lines:
            context_parts.append("### 本课对话摘要\n" + "\n".join(lines))

    context_text = "\n\n".join(context_parts)

    system_content = (
        "你是一位专业的出题专家。请根据给定的课程内容，结合自己的角色设定，"
        "生成高质量的随堂测验题目。"
    )
    if personality_prompt:
        system_content += f"\n\n【你的角色】\n{personality_prompt}"

    system_content += (
        "\n\n请严格以 JSON 格式返回题目数组（不要输出任何额外文字），每题格式如下：\n"
        "{\n"
        '  "question": "题目内容",\n'
        '  "type": "single|multiple|boolean|fill",\n'
        '  "options": ["A. 选项文本", "B. 选项文本", ...],  // single/multiple/boolean 题型\n'
        '  "answer": "A"  // single: 字母; multiple: 字母组合如 "AC"; boolean: T/F; fill: 正确答案文本\n'
        "}\n"
        "要求：\n"
        "1. 生成 4-6 道题目，涵盖单选题、多选题、判断题、填空题。\n"
        "2. 题目内容需结合课程内容，体现角色的语言风格和教学特点。\n"
        "3. 单选题 2-3 道，选项 A/B/C/D；多选题 1-2 道；判断题 1 道；填空题 1 道。\n"
        "4. 答案必须准确，与课程内容一致。\n"
        "5. 只返回 JSON 数组，不要返回其他内容。\n"
        "6. 【绝对禁止】不得出现与具体知识点无关的泛化题、元认知题或学习态度题，例如：\n"
        "   - 『关于「xx」中的关键概念，以下说法正确的是？（多选）』这类没有明确考点的题；\n"
        "   - 选项含『只需记住结论即可 / 不需要思考为什么 / 需要理解概念的内涵和适用条件 / "
        "通过实例加深理解』等学习态度套话的题；\n"
        "   - 『学习「xx」不需要理解，只需记忆』这类判断题。\n"
        "   每道题的考点必须是本单元的具体知识点，题目对错应能依据课程资料独立判断。"
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": f"请根据以下课程内容生成测验题目：\n\n{context_text[:8000]}"},
        ],
        "temperature": 0.7,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(base_url, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        content = _strip_thinking_lead(content)
        text = _strip_code_fence(content)
        questions = json.loads(text)
        if isinstance(questions, list):
            return _normalize_quiz_questions(questions)
    except Exception:
        pass

    # 失败返回空列表，由调用方决定是否走更低级兜底
    return []


def _strip_code_fence(text: str) -> str:
    """Remove ```json ... ``` code fences from model output."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_markdown_quiz_table(text: str) -> List[Dict[str, Any]]:
    """Parse Markdown table output from exam_prompt_template into quiz questions."""
    questions: List[Dict[str, Any]] = []
    if not text:
        return questions

    # 容错1：模型可能在题干里输出 ``` 多行代码块，把表格行拆断成多行。
    # 先把代码块内的换行压成 <br>，保证表格每行是单一逻辑行。
    text = re.sub(r"```[\s\S]*?```", lambda m: m.group(0).replace("\n", "<br>"), text)

    # 容错2：重构逻辑表格行——表格区域中非 "|" 开头的行（如代码续行）拼回上一表格行；
    # 连续 2 个空行视为表格结束。
    logical_rows: List[str] = []
    in_table = False
    blank_run = 0
    for raw in text.split("\n"):
        s = raw.strip()
        if s.startswith("|"):
            logical_rows.append(raw)
            in_table = True
            blank_run = 0
        elif in_table and s:
            logical_rows[-1] += "\n" + raw
            blank_run = 0
        elif in_table:
            blank_run += 1
            if blank_run >= 2:
                in_table = False
    if len(logical_rows) < 2:
        return questions

    # 模型常会先输出一版表再输出"修订版"，检测到新表头（含"题号/题型/题干"列）时
    # 丢弃之前收集的所有行，只保留最后一张表，避免重复题/垃圾行（如 qwen3.5 实测）。
    data_lines = []
    for line in logical_rows[1:]:
        # 跳过分隔行 |---|---|...
        if re.match(r"^\|[\s\-:|]+\|$", line):
            continue
        # 新表头：列名含 题号/题型/题干，说明进入下一张表 → 丢弃旧数据
        if re.search(r"题号|题型|题干", line) and re.search(r"选项|正确答案|答案", line):
            data_lines = []
            continue
        data_lines.append(line)

    type_map = {
        "单选": "single", "单选题": "single",
        "多选": "multiple", "多选题": "multiple",
        "判断": "boolean", "判断题": "boolean",
        "填空": "fill", "填空题": "fill",
        "简答": "fill", "简答题": "fill",
    }

    for line in data_lines:
        # 按 | 分割，去掉首尾空列
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 2:
            continue
        # 去掉首尾因 | 产生的空字符串
        cells = [c for c in cells if c != "" or True]
        # 首尾可能有空，精确分割
        raw_cells = line.split("|")
        # 去掉首尾的空（因为 line 以 | 开头和结尾）
        if raw_cells and raw_cells[0].strip() == "":
            raw_cells = raw_cells[1:]
        if raw_cells and raw_cells[-1].strip() == "":
            raw_cells = raw_cells[:-1]
        cells = [c.strip() for c in raw_cells]

        # 列：题号 | 题型 | 题干 | 选项A | 选项B | 选项C | 选项D | 正确答案 | 解析
        if len(cells) < 8:
            continue

        qtype_raw = cells[1].strip()
        qtype = type_map.get(qtype_raw, "single")
        question_text = cells[2].strip()
        if not question_text:
            continue

        opt_a = cells[3].strip()
        opt_b = cells[4].strip()
        opt_c = cells[5].strip()
        opt_d = cells[6].strip()
        answer = cells[7].strip()
        explanation = cells[8].strip() if len(cells) > 8 else ""

        # 容错：qwen3.5 等模型把选项文本内嵌在题干列（<br>A. xxx），
        # 而"选项A-D"列只填 A/B/C/D 字母占位。此时从题干拆分出真实选项。
        embedded_opts = []  # [(letter, text), ...]
        if "<br" in question_text.lower():
            br_parts = re.split(r"<br\s*/?>", question_text, flags=re.I)
            for p in br_parts[1:]:
                mm = re.match(r"^\s*([A-D])[.、)．:：]?\s*(.+)$", p.strip(), flags=re.S)
                if mm and len(embedded_opts) < 4:
                    embedded_opts.append((mm.group(1).upper(), mm.group(2).strip()))
            if len(embedded_opts) >= 2:
                question_text = br_parts[0].strip()

        def _is_letter_placeholder(opts):
            """表格选项列是否全是字母占位（如 'A' / 'A, B'），而非真实选项文本。"""
            vals = [o for o in opts if o and o not in ("/", "（留空）")]
            if not vals:
                return False
            return all(
                len(o) <= 10
                and re.fullmatch(r"[A-D](?:[,\s，、]+[A-D])*", o, flags=re.I)
                for o in vals
            )

        options = []
        if qtype in ("single", "multiple", "boolean"):
            table_opts = [opt_a, opt_b, opt_c, opt_d]
            if embedded_opts and _is_letter_placeholder(table_opts):
                # 使用题干内嵌的选项
                options = [f"{letter}. {text}" for letter, text in embedded_opts]
            else:
                for letter, opt in [("A", opt_a), ("B", opt_b), ("C", opt_c), ("D", opt_d)]:
                    if opt and opt != "/" and opt != "（留空）":
                        # 去掉选项前缀如 "A." "A、" "A)"
                        opt_clean = re.sub(r"^[A-D][.、)：:]\s*", "", opt)
                        options.append(f"{letter}. {opt_clean}")
                    else:
                        options.append(f"{letter}. /")
            if qtype == "boolean":
                # 判断题：用 T/F
                if answer.upper().startswith("T") or answer in ("正确", "对", "是"):
                    answer = "T"
                else:
                    answer = "F"
        elif qtype == "fill":
            # 填空/简答：answer 直接是文本
            answer = answer
            options = []

        questions.append({
            "question": question_text,
            "type": qtype,
            "options": options,
            "answer": answer,
            "explanation": explanation,
        })

    return questions


def generate_quiz_with_ollama(
    unit_content: Dict[str, Any],
    personality_prompt: str = "",
    config: Dict[str, Any] | None = None,
    chat_history: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Use local Ollama model to generate quiz questions using exam_prompt_template."""
    config = config or {}
    base_url = (config.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
    model = (config.get("ollama_model") or "qwen2.5:7B").strip()

    unit_title = unit_content.get("title", "")
    unit_summary = unit_content.get("summary", "")
    key_points = unit_content.get("key_points", [])
    source_files = unit_content.get("source_files", [])

    # 构建课程上下文
    context_parts = [f"课程单元：{unit_title}"]
    if unit_summary:
        context_parts.append(f"概述：{unit_summary}")
    if key_points:
        context_parts.append("关键要点：")
        for i, kp in enumerate(key_points, 1):
            context_parts.append(f"  {i}. {kp}")
    if source_files:
        md_contents = []
        for sf in source_files[:3]:
            if isinstance(sf, dict) and sf.get("markdown_content"):
                md_contents.append(sf["markdown_content"][:3000])
        if md_contents:
            context_parts.append("参考资料全文：\n" + "\n---\n".join(md_contents))

    # 注入聊天记录：让题目基于本课师生实际对话内容动态生成
    if chat_history:
        recent = chat_history[-20:]  # 只取最近 20 条，避免 prompt 过长
        lines = []
        for m in recent:
            role = "学生" if m.get("role") == "user" else "老师"
            content = (m.get("content") or "").strip()
            if content:
                # 截断过长的单条消息
                if len(content) > 400:
                    content = content[:400] + "…"
                lines.append(f"{role}：{content}")
        if lines:
            context_parts.append("本课师生对话摘要（用于出题参考，应反映对话中讨论的知识点）：\n" + "\n".join(lines))

    context_text = "\n".join(context_parts)
    topic = unit_title or "课程测验"

    # 使用用户的提示词模板
    seed = unit_content.get("random_seed", 42)

    def _build_exam_prompt(seed_val: int) -> str:
        return f"""你是一位资深学科教师，正在为【{topic}】这门课设计一份高质量的随堂测验。

以下是你需要参考的课程资料（来自教材/讲义）：
---
{context_text[:8000]}
---

请根据以上资料，生成 6-8 道题目，题型包括：单选题3-4道、多选题1-2道、判断题1道、填空题1-2道，难度为：中等。

【重要】本次出题的随机种子为 {seed_val}，请基于此种子设计**全新、独特**的题目，不要使用任何模板化或通用的问题。
每次测验的题目都必须不同 —— 请变换题干措辞、选项干扰、考察角度。

设计要求：
1. **每道题必须明确考核资料中的某个具体知识点**（例如：考"二次函数顶点的求法"，而不是泛泛的"理解概念"）。
2. **选项设计要有迷惑性**：错误选项应基于常见误解，不能太明显，也不能太离谱。
3. **避免"全是正确选项"或"全是错误选项"**，尤其是多选题，必须确保至少有一个错误项。
4. **填空题答案要具体、明确**，不要用通用词语。
5. **所有题目必须附带详细解析**（不仅给出答案，还要解释为什么对/为什么错）。
6. **【绝对禁止】严禁出现任何泛化的、与具体知识点无关的题目**，尤其是：
   - 『关于「xx」中的关键概念，以下说法正确的是？（多选）』这类没有明确考点的题；
   - 『下列哪个概念属于本课讲授的具体知识点？』『本课还讲解了______』这类只考记忆课程大纲的题；
   - 含『只需记住结论即可 / 不需要思考为什么 / 需要理解概念的内涵和适用条件 / 通过实例加深理解』等学习态度套话选项的题；
   - 『学习「xx」不需要理解，只需记忆』这类判断学习态度的题。
   每道题的题干和选项都必须落在本课的具体知识点上，能根据上文资料独立判断对错。

【高质量出题示例】（仅参考其风格与题型设计，严禁照抄示例内容，必须换成你手上资料中的知识点）：
- 单选（考具体操作结果）：在 Python 中，执行 `x = "10"; y = int(x) + 2` 后，`y` 的值是？
  A. "102"  B. 12  C. "10+2"  D. 程序报错    正确答案：B    解析：int("10") 把字符串转为整数 10，10+2=12。
- 多选（考多个相关操作）：下列哪些表达式可以把字符串 `"3.5"` 转换为数值类型？
  A. int("3.5")  B. float("3.5")  C. eval("3.5")  D. float(3)    正确答案：B,C    解析：int("3.5") 会报 ValueError；float("3.5")=3.5；eval("3.5")=3.5；float(3)=3.0 但不是由字符串转来。
- 判断（考概念边界）：在 Python 中，`2 == "2"` 的运算结果为 True。    正确答案：F    解析：整数 2 与字符串 "2" 类型不同，恒不相等。
- 填空（考具体函数）：Python 中把字符串转换为整数的内置函数是______。    正确答案：int()

请严格按照以下 Markdown 表格格式输出（直接输出表格本身，不要任何开场白、分析、思考过程或额外文字）：
| 题号 | 题型 | 题干 | 选项A | 选项B | 选项C | 选项D | 正确答案 | 解析 |
|------|------|------|-------|-------|-------|-------|----------|------|
| 1    | 单选 | ...  | ...   | ...   | ...   | ...   | A        | ...  |
| 2    | 多选 | ...  | ...   | ...   | ...   | ...   | A,C      | ...  |
| 3    | 填空 | ...  | /     | /     | /     | /     | 具体答案 | ...  |

【严格格式约束（必须遵守，否则视为不合格）】
1. 每题只能占一行表格；禁止在题干或选项里使用 `<br>`、`\\n`、```、多行。
2. 禁止在题干里出现"修正题干 / 修订版 / Revised / 标准答案 / Correct answer / 注意："等元说明。
3. 一道题只问一个具体知识点；不要在题干里堆叠多个子问题。
4. 题干里的代码片段请用反引号 `code` 包住，例如：在 Python 中执行 `x = "10"` 后，`x` 的数据类型是？
5. 不要输出额外解释、分析或开场白；只输出表格本身。
6. 不要重复题目；每题编号唯一。"""

    system_content = "你是一位资深学科教师，擅长设计高质量考试题。"
    if personality_prompt:
        system_content += f"\n你的教学风格：{personality_prompt}"

    def _request_and_parse(seed_val: int) -> Optional[List[Dict[str, Any]]]:
        """请求一次 Ollama 并尝试解析出题目；解析失败返回 None。"""
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": _build_exam_prompt(seed_val)},
            ],
            "stream": False,
            # qwen3 默认 thinking 会消耗大量时间（4B 模型实测 60s 超时仍无输出），显式关闭
            "think": False,
            "options": {
                "temperature": 0.8,
                "num_ctx": 8192,
                "num_predict": 3000,
                "seed": seed_val,
            },
        }
        try:
            # 4B 本地模型生成 6-8 道题较慢：60s 太紧会超时导致"exam 没输出"，放宽到 180s
            response = requests.post(f"{base_url}/api/chat", json=payload, timeout=180)
            response.raise_for_status()
            result = response.json()
            content = (result.get("message", {}).get("content") or result.get("response", "")).strip()
            # 清理 qwen3 思考残留 + 开头独白（"首先，用户要求我…"）
            content = _strip_thinking_residue(content)
            content = _strip_thinking_lead(content)
            print(f"[ollama-quiz] raw_response_len={len(content)}, preview={content[:300]}", flush=True)

            # 尝试解析 Markdown 表格
            questions = _parse_markdown_quiz_table(content)
            if questions:
                normalized = _normalize_quiz_questions(questions)
                print(f"[ollama-quiz] parsed {len(normalized)} questions from markdown table", flush=True)
                if normalized:
                    print(f"[ollama-quiz] first_q: {normalized[0].get('question', '')[:100]}", flush=True)
                return normalized

            # 如果表格解析失败，尝试 JSON 解析
            text = _strip_code_fence(content)
            try:
                json_questions = json.loads(text)
                if isinstance(json_questions, list):
                    normalized = _normalize_quiz_questions(json_questions)
                    print(f"[ollama-quiz] parsed {len(normalized)} questions from JSON fallback", flush=True)
                    return normalized
            except json.JSONDecodeError:
                pass

            print(f"[ollama-quiz] 本次解析失败（表格/JSON 均不可用）", flush=True)
        except Exception as exc:
            print(f"[ollama-quiz] 出题异常: {exc}", flush=True)
            import traceback
            traceback.print_exc()
        return None

    # 4B 模型输出格式不稳定（表格缺列/多表/题干嵌代码块），解析失败时换随机种子重试
    for attempt in range(3):
        seed_val = seed if attempt == 0 else random.randint(1, 99999)
        print(f"[ollama-quiz] 出题尝试 {attempt + 1}/3 (seed={seed_val})", flush=True)
        parsed = _request_and_parse(seed_val)
        if parsed:
            return parsed

    print(f"[ollama-quiz] 3 次尝试均失败，返回空（由调用方决定云端/fallback 兜底）", flush=True)
    return []


# 无意义题检测：选项中的"学习态度/元认知"套话 + 课程大纲归属干扰项
_MEANINGLESS_OPTION_RE = re.compile(
    r"只需记住|只要记住|死记硬背|记住结论|背下来|"
    r"不需要思考|不用思考|不需思考|无需思考|"
    r"不需要理解|不需理解|不用理解|无需理解|不要理解|不理解也|"
    r"跳过此部分|跳过即可|"
    r"需要理解概念|理解概念的内|通过实例加深理解|理解比记忆|重在理解|"
    r"以上都对|以上都不对|"
    r"与本课程无关的背景知识|其他学科才涉及的概念|本课程未涉及的概念|以上均不属于本课内容"
)
# 题干是"学习/掌握/理解……只需记住/不需理解/不需思考"这类方法论套路句
_MEANINGLESS_QUESTION_RE = re.compile(
    r"(?:学习|掌握|理解).{0,12}?(?:只需记住|死记硬背|不需要理解|不需理解|不需要思考)"
)
# 题干是"「x」中最关键的知识点是 / 最基础的概念是 / 核心要点是什么"这类无具体考点的问法
_MEANINGLESS_QUESTION_RE2 = re.compile(r"最关键的知识点|最基础的概念|核心要点是什么")
# 题干是"关于…关键概念/核心要点，以下说法正确的是"这类泛化问法
_MEANINGLESS_QUESTION_RE3 = re.compile(
    r"关于.{0,20}?(?:的关键概念|中的关键概念|的核心要点|中的核心要点|的核心概念)"
)
# 题干是"课程大纲归属"问法：问"哪个概念属于本课 / 本课讲了哪些内容"，
# 这类题只考记忆课程目录，不考知识理解（用户实测反馈的典型垃圾题）
_MEANINGLESS_QUESTION_RE4 = re.compile(
    r"属于本课(?:讲授|所学|讲解)的?具体知识点|"
    r"哪些(?:个)?概念属于|哪些属于本课|"
    r"下列哪些(?:概念|内容)属于|"
    r"除「[^」]+」外，本课还(?:重点)?讲解|"
    r"属于「[^」]+」的讲授内容"
)


def _is_meaningful_question(q: Dict[str, Any]) -> bool:
    """过滤"元认知/学习态度"套路题 —— 这类题与具体知识点无关，无法独立判断对错。

    **绝对不允许出现**的典型题：
    - 关于「流程控制语句」中的关键概念，以下说法正确的是？（多选）
      A. 需要理解概念的内涵和适用条件  B. 只需记住结论即可  C. 通过实例加深理解  D. 不需要思考为什么
    - 学习「xx」不需要理解，只需记忆。
    - 「xx」中最关键的知识点是______。
    """
    question = str(q.get("question", "")).strip()
    if not question:
        return False
    # 题干过短或纯占位符（如表格解析出的 "/"、"-"）→ 无意义题
    if len(question) < 2 or question in ("/", "-", "—", "|", "…"):
        return False
    # 题干中泄漏了答案/模型自改痕迹（"**正确答案** / Revised Q"）→ 无效题
    if re.search(r"\*\*?正确答案\*\*?|标准答案|Correct answer|Revised", question, re.IGNORECASE):
        return False
    # 题干里出现元说明 / 修正痕迹（"修正题干"/"修订版"/"注意："）→ 模型崩坏题
    if re.search(r"修正题干|修订版|原题|题干是|正确答案[:：]|参考答案", question):
        return False
    # 题干里出现 HTML 换行 / 反斜杠 n → 模型崩坏题（一题多行被压成一行）
    if "<br" in question.lower() or "\\n" in question or "```" in question:
        return False
    # 题干里同时出现多个问号 → 一题多问，丢掉
    if question.count("?") + question.count("？") >= 2:
        return False
    options = [str(o) for o in (q.get("options") or []) if str(o).strip()]
    # 选项里含 HTML 换行 / 反斜杠 n / 多个字母选项被合并 → 崩坏题
    for o in options:
        if "<br" in o.lower() or "\\n" in o or "```" in o:
            return False

    meta_hits = sum(1 for o in options if _MEANINGLESS_OPTION_RE.search(o))
    # 选项大面积是"只需记住/不需思考/不需理解/跳过/以上都对"等套话 → 无意义题
    if options and meta_hits >= max(2, int(len(options) * 0.5)):
        return False
    # 题干是学习方法/学习态度套路句
    if _MEANINGLESS_QUESTION_RE.search(question):
        return False
    # 题干是"最关键的知识点是 / 最基础的概念是"这类无具体考点的问法
    if _MEANINGLESS_QUESTION_RE2.search(question):
        return False
    # 题干是"关于…关键概念…说法正确的是"且选项含套话 → 泛化题
    if _MEANINGLESS_QUESTION_RE3.search(question) and meta_hits >= 1:
        return False
    # 题干是"课程大纲归属"问法（"哪个概念属于本课 / 本课讲了哪些内容 / 除…外还讲解了什么"）
    # → 只考记忆课程目录，不考知识理解，直接丢弃
    if _MEANINGLESS_QUESTION_RE4.search(question):
        return False
    return True


def _normalize_quiz_questions(questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize quiz questions: fix answer format, ensure type, strip option prefixes."""
    normalized = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        qtype = str(q.get("type", "single")).strip().lower()
        if qtype not in ("single", "multiple", "boolean", "fill"):
            qtype = "single"
        question_text = str(q.get("question", "")).strip()
        if not question_text:
            continue
        options = q.get("options") or []
        if isinstance(options, str):
            options = [options]
        if not isinstance(options, list):
            options = []
        # Strip A./B./C./D. prefixes from option text for display
        cleaned_options = []
        for i, opt in enumerate(options):
            opt_text = str(opt).strip()
            opt_text = re.sub(r"^\s*[A-D][.、)：:]\s*", "", opt_text)
            letter = chr(65 + i)
            cleaned_options.append(f"{letter}. {opt_text}")
        # Normalize answer
        answer = str(q.get("answer", "")).strip().upper()
        if qtype == "boolean":
            if answer not in ("T", "F"):
                answer = "T"
        elif qtype == "single":
            if answer not in ("A", "B", "C", "D"):
                answer = "A"
        elif qtype == "multiple":
            answer = re.sub(r"[^A-D]", "", answer)
            if not answer:
                answer = "A"
        item = {
            "question": question_text,
            "type": qtype,
            "options": cleaned_options,
            "answer": answer,
        }
        # 保留解析字段（来自 Markdown 表格的解析列）
        explanation = str(q.get("explanation", "")).strip()
        if explanation:
            item["explanation"] = explanation
        # 过滤"元认知/学习态度"套路题（绝对不允许出现无意义题）
        if not _is_meaningful_question(item):
            continue
        normalized.append(item)
    return normalized


def _fallback_quiz(unit_content: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """本地兜底题库：基于单元 key_points（+ summary 拆分）构造与具体知识点绑定的理解题。

    **绝对禁止**出现"关于「xx」中的关键概念，以下说法正确的是？（多选）"、
    "下列哪个概念属于本课讲授的具体知识点"、"只需记住结论即可 / 不需要思考为什么"
    这类与具体知识点无关的元认知 / 大纲归属 / 学习态度套路题。

    本函数所有题目都以 key_points（或 summary 拆分出的短语）为考点，
    题干落在具体知识点上，对错可依据课程资料独立判断，且不会触发本模块的无意义题过滤器。
    """
    unit_content = unit_content or {}
    title = str(unit_content.get("title") or "本单元")
    summary = str(unit_content.get("summary") or "")
    kp = [str(k).strip() for k in (unit_content.get("key_points") or []) if str(k).strip()]
    if not kp and summary:
        # 无 key_points 时从 summary 中拆分具体短语作为考点
        kp = [p.strip() for p in re.split(r"[，。、；;：:]|以及|和|与", summary) if len(p.strip()) >= 2]
    # 兜底拆出的短语可能很长（整句 summary），按标点再细分一次
    extra = []
    for k in kp:
        for sub in re.split(r"[，。、；;：:]|以及|和|与", k):
            sub = sub.strip()
            if 2 <= len(sub) <= 30:
                extra.append(sub)
    kp = list(dict.fromkeys(extra + kp))[:5]
    if len(kp) < 2:
        while len(kp) < 2:
            kp.append(f"「{title}」的核心内容")

    def _lab(i: int) -> str:
        k = kp[i]
        return k if len(k) <= 22 else k[:19] + "…"

    # 从 summary 中抽取可作为"正确说法"的短句（判断题/选项素材）
    sents = [s.strip() for s in re.split(r"[。！？\n]+", summary) if 6 <= len(s.strip()) <= 42]
    truth = sents[0] if sents else f"「{title}」会系统讲解「{_lab(0)}」"
    truth2 = sents[1] if len(sents) > 1 else f"「{title}」会讲解「{_lab(1)}」"

    return [
        {
            # 单选：从课程概述中辨析"哪个说法正确"（正确项来自真实内容）
            "question": f"根据「{title}」的学习内容，下列哪项说法是正确的？",
            "type": "single",
            "options": [
                truth,
                f"「{_lab(1)}」就是「{_lab(0)}」的另一种叫法",
                f"「{title}」的内容与「{_lab(0)}」完全无关",
                f"学习「{_lab(0)}」只需要背诵，不需要练习",
            ],
            "answer": "A",
        },
        {
            # 多选：多个正确说法（2 真 + 2 假）
            "question": f"根据「{title}」，下列哪些说法符合课程内容？（多选）",
            "type": "multiple",
            "options": [
                truth,
                truth2,
                f"「{_lab(0)}」与「{_lab(1)}」是完全相同的知识点",
                f"「{title}」中没有需要掌握的知识点",
            ],
            "answer": "AB",
        },
        {
            # 判断：两个不同知识点被误判为同一概念 → 错误
            "question": f"「{_lab(0)}」与「{_lab(1)}」是同一个概念。",
            "type": "boolean",
            "options": ["正确", "错误"],
            "answer": "F",
        },
        {
            # 填空：回忆另一个具体知识点
            "question": f"「{title}」除了「{_lab(0)}」，还重点讲解了______。",
            "type": "fill",
            "answer": _lab(1),
        },
    ]


def _strip_unit_seq_prefix(title: str) -> str:
    """剥离「第N课 / Lesson N / Unit N / 第N讲」等序号前缀。"""
    return re.sub(
        r"^\s*(?:第\s*\d+\s*(?:课|讲|章|节)|lesson\s*\d+|unit\s*\d+)[、.\s:：-]*",
        "",
        str(title or ""),
        flags=re.IGNORECASE,
    ).strip()


def generate_knowledge_graph(lesson_data: Dict[str, Any]) -> Dict[str, Any]:
    """从备课结果（lesson_data.units）确定性生成知识图谱。

    不依赖 LLM，保证任何备课来源（云端 / Ollama / 兜底模板）都能得到一致的图谱：
    - 每个 unit 对应一个知识点节点（KG001、KG002…）；
    - 节点概念取自 unit 的 key_points / modules.concept / target；
    - prerequisite 边优先使用 unit.prerequisites（按标题匹配节点），
      没有显式前置时按单元顺序串成链条（第 i 课是第 i+1 课的前置）。

    lesson_data 建议字段：
      topic: 课程主题
      units: [ { title, summary, key_points, target, modules, prerequisites } ]

    返回结构（与 knowledge_graph.py 兼容）：
      { "nodes": [...], "edges": [...] }
    """
    units = lesson_data.get("units") or []
    if not isinstance(units, list) or not units:
        return {"nodes": [], "edges": []}

    topic = str(lesson_data.get("topic") or "课程")
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    # title -> node_id 映射，用于解析 unit.prerequisites 中的标题引用
    title_to_id: Dict[str, str] = {}

    for i, unit in enumerate(units, 1):
        if not isinstance(unit, dict):
            continue
        node_id = f"KG{i:03d}"
        title = _strip_unit_seq_prefix(unit.get("title") or f"第 {i} 课")
        title_to_id[title] = node_id

        # 收集概念：key_points > modules.concept > target
        concepts: List[str] = []
        for kp in (unit.get("key_points") or []):
            k = str(kp).strip()
            if k and k not in concepts:
                concepts.append(k)
        for m in (unit.get("modules") or []):
            if isinstance(m, dict):
                c = str(m.get("concept") or "").strip()
                if c and c not in concepts:
                    concepts.append(c)
        if not concepts:
            target = str(unit.get("target") or "").strip()
            if target and target not in concepts:
                concepts.append(target)
        if not concepts:
            concepts = [title]

        nodes.append({
            "id": node_id,
            "name": title,
            "description": str(unit.get("summary") or f"{topic}：{title}").strip(),
            "prerequisites": [],
            "concepts": concepts[:6],
            "skills": [],
            "tags": [topic],
            "difficulty": round(min(0.95, 0.2 + 0.15 * (i - 1)), 2),  # 按单元顺序递增难度
            "estimated_time": 30,
            "related_quiz_questions": [],
        })

    # 构建 prerequisite 边
    for i, unit in enumerate(units, 1):
        if not isinstance(unit, dict):
            continue
        node_id = f"KG{i:03d}"
        prereqs: List[str] = []
        explicit = unit.get("prerequisites") or []
        for ref in explicit:
            ref = str(ref).strip()
            target_id = title_to_id.get(ref) or title_to_id.get(_strip_unit_seq_prefix(ref))
            if target_id and target_id != node_id:
                prereqs.append(target_id)
        if not prereqs and i > 1:
            # 无显式前置时按顺序串链
            prereqs.append(f"KG{i - 1:03d}")
        nodes[i - 1]["prerequisites"] = prereqs
        for p in prereqs:
            edges.append({
                "from": p,
                "to": node_id,
                "type": "prerequisite",
                "strength": 0.9,
            })

    return {"nodes": nodes, "edges": edges}
