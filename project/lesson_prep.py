import json
import os
import re
from typing import Any, Dict, List, Optional

import requests


SILICONFLOW_URL = "https://api.siliconflow.cn/v1/chat/completions"
DEFAULT_MODEL = "deepseek-ai/DeepSeek-V3"

# 单课资料正文最大长度（截断防 token 爆炸）
MAX_UNIT_CONTENT_CHARS = 8000

# 备课 system prompt（云端 / 本地 Ollama 共用）
_LESSON_SYSTEM_PROMPT = (
    "你是一位顶级学科专家与课程设计师。请针对用户给定的主题，搜索最新、权威的资料，"
    "并将内容拆分为若干个可循序渐进教学的「课」（unit）。\n"
    "请严格以 JSON 格式返回以下字段（不要输出任何额外文字）：\n"
    "{\n"
    '  "topic": "主题",\n'
    '  "syllabus": "整体章节大纲（Markdown 格式，列出全部 unit 标题与简要内容）",\n'
    '  "key_points": ["全局核心概念1", "全局核心概念2", ...],\n'
    '  "units": [\n'
    '    {\n'
    '      "title": "第 1 课标题",\n'
    '      "summary": "本课要点概述（1-2 句）",\n'
    '      "key_points": ["本课要点1", "本课要点2", "本课要点3", "本课要点4"],\n'
    '      "source_files": [\n'
    '        {"title": "资源标题", "url": "下载链接", "type": "pdf|docx|webpage", '
    '"description": "简短说明", "markdown_content": "可选：若资源是公开文本，直接提供 Markdown 正文"}\n'
    '      ]\n'
    '    }\n'
    '  ],\n'
    '  "resources": ["全局备用资源（可选，结构与 source_files 一致）"]\n'
    "}\n"
    "要求：\n"
    "1. units 至少 12 课，最多 20 课，由浅入深、循序渐进。每课标题需明确体现该课的教学内容。\n"
    "2. 每个 unit 的 key_points 至少 4 个，source_files 至少 1 个真实可访问的下载链接（PDF 教材、官方文档等），type 字段必须是 pdf/docx/webpage 之一。\n"
    "3. markdown_content 字段若资源是公开网页/文本，请直接给出关键段落 Markdown 正文（不超过 2000 字）。\n"
    "4. syllabus 字段需包含全部课时的标题列表，使用 ### 标记每课，格式为 ### 第N课：标题，并附上1-2句简要说明。\n"
    "5. key_points（全局）至少 5 个核心概念。\n"
    "6. resources（全局）至少 3 个高质量学习资源链接。"
)


def _fallback_lesson(topic: str) -> Dict[str, Any]:
    """本地兜底教案：单课结构，含一个示例 unit（不含题目，题目由模型单独生成）。"""
    sample_unit = {
        "title": f"{topic} 概览",
        "summary": f"了解 {topic} 的核心概念、关键方法和实践要点。",
        "key_points": ["掌握主题的核心定义", "理解关键流程和步骤", "能够结合实例进行实践"],
        "source_files": [
            {
                "title": f"{topic}参考资料",
                "url": "https://example.com/lesson-resource.pdf",
                "type": "pdf",
                "description": "参考教材与讲义（示例链接，可在设置中配置云端 API 后获取真实资源）",
                "markdown_content": "# 参考资料\n\n这是一个示例资料链接，指向公开示例页面。",
            }
        ],
    }
    return {
        "topic": topic,
        "syllabus": f"# {topic}\n\n## 1. 基础概念\n- 理解主题的基本定义\n\n## 2. 关键方法\n- 掌握实践步骤与常见误区\n\n## 3. 进阶应用\n- 结合真实案例进行训练",
        "key_points": sample_unit["key_points"],
        "resources": sample_unit["source_files"],
        "units": [sample_unit],
    }


def _strip_code_fence(text: str) -> str:
    """Remove markdown code fences around JSON if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        # remove optional language tag like "json"
        cleaned = re.sub(r"^[a-zA-Z]+\s*\n", "", cleaned, count=1)
        cleaned = cleaned.strip("`").strip()
    return cleaned


def _call_siliconflow(topic: str, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    config = config or {}
    api_key = (config.get("cloud_api_key") or config.get("siliconflow_api_key") or os.getenv("SILICONFLOW_API_KEY", "")).strip()
    if not api_key:
        return _fallback_lesson(topic)

    base_url = (config.get("cloud_base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
    if not base_url.endswith("/chat/completions"):
        url = f"{base_url}/chat/completions"
    else:
        url = base_url

    model = (config.get("cloud_model") or config.get("siliconflow_model") or DEFAULT_MODEL).strip()
    enable_search = bool(config.get("enable_search", True))

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    # 让云端模型按"分课"结构生成教案：units 数组里每一课独立持有 title/summary/key_points/source_files
    # 注意：不再要求模型生成题目，题目由后续单独流程结合人格提示词生成
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (config or {}).get("lesson_prompt") or _LESSON_SYSTEM_PROMPT,
            },
            {"role": "user", "content": f"请为【{topic}】设计一份分课教案，至少12课，每课独立含资源与要点。"},
        ],
        "enable_search": enable_search,
        "max_tokens": 8192,
        "temperature": 0.7,
    }

    try:
        print(f"[PREP-DEBUG] calling API: url={url}, model={model}, enable_search={enable_search}", flush=True)
        response = requests.post(url, headers=headers, json=payload, timeout=180)
        print(f"[PREP-DEBUG] API response status={response.status_code}", flush=True)
        response.raise_for_status()
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        print(f"[PREP-DEBUG] raw_content_len={len(content)}, preview={content[:300]}", flush=True)
        text = _strip_code_fence(content)
        data = json.loads(text)
        if isinstance(data, dict):
            units = data.get("units", [])
            print(f"[PREP-DEBUG] parsed OK, units={len(units) if isinstance(units, list) else 'N/A'}", flush=True)
            return data
        print(f"[PREP-DEBUG] parsed data is not dict: {type(data)}", flush=True)
    except requests.exceptions.HTTPError as e:
        print(f"[PREP-DEBUG] HTTP error: {e}", flush=True)
        print(f"[PREP-DEBUG] response body: {response.text[:500] if response else 'N/A'}", flush=True)
    except requests.exceptions.ConnectionError as e:
        print(f"[PREP-DEBUG] connection error: {e}", flush=True)
    except requests.exceptions.Timeout:
        print(f"[PREP-DEBUG] timeout (60s)", flush=True)
    except json.JSONDecodeError as e:
        print(f"[PREP-DEBUG] JSON decode error: {e}", flush=True)
        print(f"[PREP-DEBUG] raw text (first 500): {text[:500] if 'text' in dir() else content[:500]}", flush=True)
    except Exception as e:
        print(f"[PREP-DEBUG] unexpected error: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()

    print(f"[PREP-DEBUG] falling back to _fallback_lesson(topic={topic})", flush=True)
    return _fallback_lesson(topic)


def _call_ollama_lesson(topic: str, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """用本地 Ollama 生成分课教案（与云端共用同一 system prompt，去掉 enable_search）。"""
    config = config or {}
    base_url = (config.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
    model = (config.get("ollama_model") or "qwen2.5:7b").strip()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": (config or {}).get("lesson_prompt") or _LESSON_SYSTEM_PROMPT},
            {"role": "user", "content": f"请为【{topic}】设计一份分课教案，至少12课，每课独立含资源与要点。"},
        ],
        "stream": False,
        "options": {
            "temperature": 0.6,
            "num_ctx": int(config.get("ollama_num_ctx", 16384) or 16384),
            "num_predict": int(config.get("ollama_num_predict", 8192) or 8192),
        },
    }
    try:
        print(f"[PREP-DEBUG] calling Ollama: {base_url}/api/chat | model={model}", flush=True)
        resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=300)
        if not resp.ok:
            print(f"[PREP-DEBUG] Ollama HTTP {resp.status_code}: {resp.text[:300]}", flush=True)
            return None
        content = (resp.json().get("message", {}).get("content") or "").strip()
        print(f"[PREP-DEBUG] Ollama raw_content_len={len(content)}, preview={content[:200]}", flush=True)
        if not content:
            return None
        text = _strip_code_fence(content)
        data = json.loads(text)
        if isinstance(data, dict):
            units = data.get("units", [])
            print(f"[PREP-DEBUG] Ollama parsed OK, units={len(units) if isinstance(units, list) else 'N/A'}", flush=True)
            return data
    except json.JSONDecodeError as exc:
        print(f"[PREP-DEBUG] Ollama JSON decode error: {exc}", flush=True)
    except Exception as exc:
        print(f"[PREP-DEBUG] Ollama error: {type(exc).__name__}: {exc}", flush=True)
    return None


def _normalize_unit(unit: Dict[str, Any], fallback_index: int) -> Dict[str, Any]:
    """规范化单个 unit：补全字段、规整 quiz_preset 答案。"""
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
    return {
        "title": title,
        "summary": summary,
        "key_points": key_points,
        "source_files": source_files,
        "quiz_preset": quiz_preset,
    }


def prepare_lesson(topic: str, config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Return a lesson plan payload and gracefully fall back if external API is unavailable.

    Args:
        topic: The lesson topic.
        config: Optional runtime config dict (from config.json). When provided,
                its cloud_api_key / cloud_model / cloud_base_url / enable_search
                are used instead of environment variables.

    Returns:
        dict with keys: topic, syllabus, key_points, resources, quiz_preset, units
        其中 units 是分课数组，每项含 title/summary/key_points/source_files/quiz_preset。
        旧字段 syllabus/key_points/resources/quiz_preset 保留兼容。
    """
    provider = (str((config or {}).get("lesson_provider") or "cloud")).strip().lower()
    if provider == "ollama":
        # 本地 Ollama 备课，失败回退云端
        data = _call_ollama_lesson(topic, config=config)
        if not data:
            print("[PREP-DEBUG] Ollama 备课失败，回退云端", flush=True)
            data = _call_siliconflow(topic, config=config)
    else:
        # cloud / auto：云端备课（失败时内部回退到本地兜底教案）
        data = _call_siliconflow(topic, config=config)
    data.setdefault("topic", topic)
    data.setdefault("syllabus", f"# {topic}\n\n请根据实际课程内容补充。")
    data.setdefault("key_points", ["基础概念", "核心原理", "实践应用"])
    data.setdefault("resources", [])
    data.setdefault("quiz_preset", [])

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
        return _fallback_quiz(unit_content)

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
        "5. 只返回 JSON 数组，不要返回其他内容。"
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
        text = _strip_code_fence(content)
        questions = json.loads(text)
        if isinstance(questions, list):
            return _normalize_quiz_questions(questions)
    except Exception:
        pass

    return _fallback_quiz(unit_content)


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
    lines = text.strip().split("\n")
    # 找到表格行（以 | 开头）
    table_lines = [l.strip() for l in lines if l.strip().startswith("|")]
    if len(table_lines) < 2:
        return questions

    # 跳过表头和分隔行
    data_lines = []
    for line in table_lines[1:]:
        # 跳过分隔行 |---|---|...
        if re.match(r"^\|[\s\-:|]+\|$", line):
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

        options = []
        if qtype in ("single", "multiple", "boolean"):
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
    exam_prompt = f"""你是一位资深学科教师，正在为【{topic}】这门课设计一份高质量的随堂测验。

以下是你需要参考的课程资料（来自教材/讲义）：
---
{context_text[:8000]}
---

请根据以上资料，生成 6-8 道题目，题型包括：单选题3-4道、多选题1-2道、判断题1道、填空题1-2道，难度为：中等。

【重要】本次出题的随机种子为 {seed}，请基于此种子设计**全新、独特**的题目，不要使用任何模板化或通用的问题。
每次测验的题目都必须不同 —— 请变换题干措辞、选项干扰、考察角度。

设计要求：
1. **每道题必须明确考核资料中的某个具体知识点**（例如：考"二次函数顶点的求法"，而不是泛泛的"理解概念"）。
2. **选项设计要有迷惑性**：错误选项应基于常见误解，不能太明显，也不能太离谱。
3. **避免"全是正确选项"或"全是错误选项"**，尤其是多选题，必须确保至少有一个错误项。
4. **填空题答案要具体、明确**，不要用通用词语。
5. **所有题目必须附带详细解析**（不仅给出答案，还要解释为什么对/为什么错）。

请严格按照以下 Markdown 表格格式输出（不要有多余的文字）：
| 题号 | 题型 | 题干 | 选项A | 选项B | 选项C | 选项D | 正确答案 | 解析 |
|------|------|------|-------|-------|-------|-------|----------|------|
| 1    | 单选 | ...  | ...   | ...   | ...   | ...   | A        | ...  |
| 2    | 多选 | ...  | ...   | ...   | ...   | ...   | A,C      | ...  |
| 3    | 填空 | ...  | (留空) | (留空) | (留空) | (留空) | 具体答案 | ...  |

注意：填空没有选项，请在"选项A~D"列填写"/"占位，但"正确答案"列必须填标准答案。
判断题的选项A填"正确"，选项B填"错误"，正确答案填T或F。"""

    system_content = "你是一位资深学科教师，擅长设计高质量考试题。"
    if personality_prompt:
        system_content += f"\n你的教学风格：{personality_prompt}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": exam_prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 0.8,
            "num_ctx": 8192,
            "num_predict": 4096,
            "seed": seed,
        },
    }

    try:
        response = requests.post(f"{base_url}/api/chat", json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        content = (result.get("message", {}).get("content") or result.get("response", "")).strip()
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

        print(f"[ollama-quiz] failed to parse response, using fallback", flush=True)
    except Exception as exc:
        print(f"[ollama-quiz] 出题异常: {exc}", flush=True)
        import traceback
        traceback.print_exc()

    return _fallback_quiz(unit_content)


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
        normalized.append(item)
    return normalized


def _fallback_quiz(unit_content: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Local fallback quiz when model API is unavailable — uses content-based questions."""
    title = unit_content.get("title", "本单元")
    summary = unit_content.get("summary", "")
    kp = unit_content.get("key_points", [])
    kp_text = "、".join(str(k) for k in kp[:3]) if kp else title

    # 从 key_points 中提取第一个具体概念作为示例
    first_kp = str(kp[0])[:20] if kp else title

    return [
        {
            "question": f"在「{title}」中，{kp_text} 相关的核心要点是？",
            "type": "single",
            "options": [
                f"A. {first_kp}等概念的理解与应用",
                "B. 只需记住结论即可",
                "C. 不需要实践练习",
                "D. 以上都不对",
            ],
            "answer": "A",
        },
        {
            "question": f"关于「{title}」中的关键概念，以下说法正确的是？（多选）",
            "type": "multiple",
            "options": [
                "A. 需要理解概念的内涵和适用条件",
                "B. 只需记住结论即可",
                "C. 通过实例加深理解",
                "D. 不需要思考为什么",
            ],
            "answer": "AC",
        },
        {
            "question": f"学习「{title}」时，理解概念比死记硬背更重要。",
            "type": "boolean",
            "options": ["A. 正确", "B. 错误"],
            "answer": "T",
        },
        {
            "question": f"「{title}」中最关键的知识点是______。",
            "type": "fill",
            "answer": kp_text or first_kp or "核心概念",
        },
    ]
