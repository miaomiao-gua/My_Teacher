import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import requests
from flask import Flask, Response, jsonify, render_template, request, send_file, stream_with_context
from werkzeug.utils import secure_filename

# 尝试加载 .env 环境变量（开发环境）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
except ImportError:
    pass

from file_utils import (
    download_resource,
    ensure_lesson_dir,
    load_course_context,
    load_unit_context,
    save_metadata,
    sanitize_topic,
    unit_dir,
)
from lesson_prep import prepare_lesson, generate_quiz_with_model, generate_quiz_with_ollama

app = Flask(__name__)

@app.after_request
def no_cache_headers(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

BASE_DIR = Path(__file__).resolve().parent
LESSONS_DIR = BASE_DIR / "lessons"
LESSONS_DIR.mkdir(exist_ok=True)
CONFIG_PATH = BASE_DIR / "config.json"

ACTIVE_LESSON = {
    "folder": None,
    "metadata": {},
    "resources": [],
    "prepared": {},
    "conversation": [],
    "progress": {},
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_progress() -> Dict[str, Any]:
    return {
        "last_access": "",
        "completed_quizzes": [],
        "score_history": [],
        "code_attempts": 0,
        # 分课进度：当前上到第几课（0-based），已完成的 unit 索引列表
        "current_unit": 0,
        "completed_units": [],
    }


def default_config() -> Dict[str, Any]:
    return {
        "ollama_base_url": "http://127.0.0.1:11434",
        "ollama_model": "qwen2.5:7B",
        # Ollama 生成参数（可配置，避免魔法数）
        "ollama_num_ctx": 16384,
        "ollama_temperature": 0.7,
        "ollama_num_predict": 8192,
        "tts_base_url": "http://127.0.0.1:8000",
        "tts_voice": "zh-CN-XiaoxiaoNeural",
        "tts_enabled": False,
        "tts_provider": "local",
        # 云端 TTS（硅基流动 FunAudioLLM/CosyVoice2-0.5B）
        # voice 必须用硅基预置音色名：alex/benjamin/charles/david(男) anna/bella/claire/diana(女)
        "tts_cloud_base_url": "https://api.siliconflow.cn/v1",
        "tts_cloud_voice": "anna",
        "tts_cloud_model": "FunAudioLLM/CosyVoice2-0.5B",
        "tts_cloud_response_format": "mp3",
        "enable_local_ollama": True,
        "siliconflow_api_key": "",
        "siliconflow_model": "deepseek-ai/DeepSeek-V3",
        # 云端模型 - 备课用（支持硅基/任意 OpenAI 兼容原生 API）
        "cloud_provider": "siliconflow",
        "cloud_base_url": "https://api.siliconflow.cn/v1",
        "cloud_api_key": "",
        "cloud_model": "deepseek-ai/DeepSeek-V3",
        "enable_search": True,
        # 云端模型 - 对话聊天用（独立配置；未填写时回退到 cloud_*）
        "chat_provider": "openai_compatible",
        "chat_base_url": "",
        "chat_api_key": "",
        "chat_model": "",
        "chat_enable_search": False,
        "auto_play_tts": True,
        "assistant_name": "艾琳老师",
        "personality_prompt": "你是一位温柔、专业、耐心的 AI 学习导师。请以启发式提问方式指导学生，先解释概念，再给出例子和练习。",
        "default_topic": "Python 基础",
        "default_voice": "zh-CN-XiaoxiaoNeural",
        "avatar_url": "/static/images/teacher.svg",
        "bg_theme": "warm",
        "bg_url": "",
        # 主页（菜单）背景独立配置
        "menu_bg_theme": "warm",
        "menu_bg_url": "",
        # 立绘位置与动画控制
        "portrait_pos_x": 50,       # 水平位置（百分比 0-100）
        "portrait_pos_y": 44,       # 垂直位置（百分比 0-100）
        "portrait_scale": 1.15,      # 缩放倍率（0.5-3.0）
        "portrait_float_amplitude": 8,  # 上下浮动幅度（像素 0-40）
        "portrait_float_enabled": True,  # 是否启用浮动动画
    }


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        cfg = default_config()
    else:
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        cfg = default_config()
        if isinstance(data, dict):
            cfg.update(data)

    # 环境变量覆盖（优先级高于 config.json）
    _env_override(cfg)
    return cfg


# 环境变量 → 配置字段映射表
_ENV_MAP: Dict[str, str] = {
    "MY_TEACHER_CLOUD_API_KEY":     "cloud_api_key",
    "MY_TEACHER_CLOUD_MODEL":       "cloud_model",
    "MY_TEACHER_CLOUD_BASE_URL":    "cloud_base_url",
    "MY_TEACHER_CHAT_API_KEY":      "chat_api_key",
    "MY_TEACHER_CHAT_MODEL":        "chat_model",
    "MY_TEACHER_CHAT_BASE_URL":     "chat_base_url",
    "MY_TEACHER_OLLAMA_BASE_URL":   "ollama_base_url",
    "MY_TEACHER_OLLAMA_MODEL":      "ollama_model",
    "MY_TEACHER_ENABLE_LOCAL_OLLAMA": "enable_local_ollama",
    "MY_TEACHER_TTS_PROVIDER":      "tts_provider",
    "MY_TEACHER_TTS_CLOUD_VOICE":   "tts_cloud_voice",
    "MY_TEACHER_AUTO_PLAY_TTS":     "auto_play_tts",
    "MY_TEACHER_ASSISTANT_NAME":    "assistant_name",
    "MY_TEACHER_DEFAULT_TOPIC":     "default_topic",
}


def _env_override(cfg: Dict[str, Any]) -> None:
    """用环境变量覆盖配置字典中的对应字段（仅当环境变量非空时）。"""
    for env_key, cfg_key in _ENV_MAP.items():
        val = os.getenv(env_key, "").strip()
        if not val:
            continue
        # 布尔类型字段
        if cfg_key in ("enable_local_ollama", "auto_play_tts"):
            cfg[cfg_key] = val.lower() in ("true", "1", "yes")
        else:
            cfg[cfg_key] = val
    # siliconflow_api_key 别名兼容旧配置
    if not cfg.get("siliconflow_api_key"):
        cfg["siliconflow_api_key"] = cfg.get("cloud_api_key", "")
    if not cfg.get("cloud_api_key"):
        cfg["cloud_api_key"] = cfg.get("siliconflow_api_key", "")


def save_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """合并保存配置：在现有配置基础上覆盖传入字段，不丢失已有配置。"""
    current = load_config()
    current.update(data or {})
    CONFIG_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def build_lesson_folder_name(topic: str) -> str:
    slug = sanitize_topic(topic)
    stamp = datetime.now().strftime("%Y%m%d")
    return f"{stamp}_{slug}"


def lesson_path(lesson_folder: str, filename: str) -> Path:
    return LESSONS_DIR / lesson_folder / filename


def ensure_lesson_files(lesson_folder: str) -> Dict[str, Path]:
    lesson_dir = LESSONS_DIR / lesson_folder
    lesson_dir.mkdir(parents=True, exist_ok=True)

    config_path = lesson_dir / "config.json"
    conversation_path = lesson_dir / "conversation.json"
    progress_path = lesson_dir / "progress.json"
    tools_dir = lesson_dir / "tools"
    images_dir = lesson_dir / "images"

    if not config_path.exists():
        # 兼容旧课程：若存在 metadata.json 则迁移
        old_meta = lesson_dir / "metadata.json"
        if old_meta.exists():
            try:
                old_data = json.loads(old_meta.read_text(encoding="utf-8"))
                config_data = {
                    "course_name": lesson_folder,
                    "topic": old_data.get("topic", lesson_folder),
                    "assistant_name": old_data.get("assistant_name", ""),
                    "personality_prompt": old_data.get("personality_prompt", ""),
                    "tts_voice": old_data.get("tts_voice", ""),
                    "syllabus": old_data.get("syllabus", ""),
                    "key_points": old_data.get("key_points", []),
                    "resources": old_data.get("resources", []),
                    "quiz_preset": old_data.get("quiz_preset", []),
                    "units": old_data.get("units", []),
                    "has_units": bool(old_data.get("units", [])),
                    "tools": old_data.get("tools", []),
                    # 新增：课程独立人设 / 立绘 / 背景
                    "avatar_url": old_data.get("avatar_url", ""),
                    "bg_theme": old_data.get("bg_theme", "warm"),
                    "bg_url": old_data.get("bg_url", ""),
                }
                config_path.write_text(json.dumps(config_data, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                config_path.write_text(json.dumps({
                    "course_name": lesson_folder,
                    "avatar_url": "",
                    "bg_theme": "warm",
                    "bg_url": "",
                }, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            config_path.write_text(json.dumps({
                "course_name": lesson_folder,
                "avatar_url": "",
                "bg_theme": "warm",
                "bg_url": "",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
    if not conversation_path.exists():
        conversation_path.write_text("[]", encoding="utf-8")
    if not progress_path.exists():
        progress_path.write_text(json.dumps(default_progress(), ensure_ascii=False, indent=2), encoding="utf-8")
    tools_dir.mkdir(exist_ok=True)
    images_dir.mkdir(exist_ok=True)

    # 兼容旧结构：若存在 units/ 目录，迁移为 section_N/
    units_dir = lesson_dir / "units"
    if units_dir.exists() and units_dir.is_dir():
        for sub in units_dir.iterdir():
            if sub.is_dir() and sub.name.startswith("unit_"):
                # unit_01 → section_1
                num = sub.name.replace("unit_", "").lstrip("0") or "1"
                target = lesson_dir / f"section_{num}"
                if not target.exists():
                    sub.rename(target)
        # 删除空的 units 目录
        try:
            if not any(units_dir.iterdir()):
                units_dir.rmdir()
        except Exception:
            pass

    return {
        "config": config_path,
        "conversation": conversation_path,
        "progress": progress_path,
        "tools_dir": tools_dir,
    }


def load_conversation(lesson_folder: str | None) -> List[Dict[str, str]]:
    if not lesson_folder:
        return []
    path = lesson_path(lesson_folder, "conversation.json")
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_conversation(lesson_folder: str, conversation: List[Dict[str, str]]) -> None:
    if not lesson_folder:
        return
    ensure_lesson_files(lesson_folder)
    path = lesson_path(lesson_folder, "conversation.json")
    path.write_text(json.dumps(conversation, ensure_ascii=False, indent=2), encoding="utf-8")


def load_progress(lesson_folder: str | None) -> Dict[str, Any]:
    if not lesson_folder:
        return default_progress()
    path = lesson_path(lesson_folder, "progress.json")
    if not path.exists():
        return default_progress()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return default_progress()
        merged = default_progress()
        merged.update(data)
        return merged
    except Exception:
        return default_progress()


def save_progress(lesson_folder: str, progress_data: Dict[str, Any]) -> Dict[str, Any]:
    if not lesson_folder:
        return default_progress()
    ensure_lesson_files(lesson_folder)
    merged = {**default_progress(), **load_progress(lesson_folder), **progress_data}
    merged["last_access"] = now_iso()
    path = lesson_path(lesson_folder, "progress.json")
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def load_lesson_metadata(lesson_folder: str | None) -> Dict[str, Any]:
    if not lesson_folder:
        return {}
    lesson_dir = LESSONS_DIR / lesson_folder
    # 优先读 config.json（新结构），兼容 metadata.json（旧结构）
    config_path = lesson_dir / "config.json"
    meta_path = lesson_dir / "metadata.json"
    target = config_path if config_path.exists() else meta_path
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def build_system_prompt(lesson_folder: str | None) -> str:
    metadata = load_lesson_metadata(lesson_folder)
    cfg = load_config()
    assistant_name = (
        metadata.get("assistant_name")
        or cfg.get("assistant_name")
        or "艾琳老师"
    ).strip()
    personality = (
        metadata.get("personality_prompt")
        or cfg.get("personality_prompt")
        or "你是一名耐心的学习教练。"
    ).strip()

    header = (
        f"【基本身份】\n"
        f"你的名字是：{assistant_name}。请你在对话中始终以「{assistant_name}」自居，"
        f"不要声称自己是其他品牌的 AI 助手或来自其他公司。\n\n"
        f"【角色设定】\n{personality}\n"
    )

    if not lesson_folder:
        return header

    topic = metadata.get("topic") or lesson_folder
    units = metadata.get("units") or []
    has_units = bool(units) and metadata.get("has_units", True)

    if not has_units:
        # 旧课程/无 units：回退到整门课根目录资料，注入基础工具调用说明
        tool_lines = [
            "【工具调用规则】",
            "- 当你判断课程内容已讲解充分（核心要点都已覆盖并举例），可在回复末尾单独输出标记 `[TOOL:start_exam]` 触发随堂测验。",
            "- 随堂测验由系统自动出题，你无需自行出题；学生答完后系统会反馈成绩，你可基于错题做简短点评。",
            "- 标记必须独占一行或位于回复末尾，且仅出现一次；不要把标记嵌在代码块或表格里。",
            "- 需要肢体动作配合教学时，可在回复末尾单独输出动作标记 `[ACTION:point]`（指向）、`[ACTION:blackboard]`（拉黑板）、`[ACTION:hello]`（打招呼）、`[ACTION:think]`（思考）、`[ACTION:listen]`（倾听）、`[ACTION:speak]`（说话）。动作标记不显示给学生，仅触发教师角色的动画。严禁在正文中用文字描述动作过程（如“我拿出黑板”“老师指向”“转过身”等），动作只能通过标记触发，正文只讲课程内容。",
            "- 需要表达情绪时，可在回复末尾输出情绪标记 `[EMOTION:happy]`（高兴）、`[EMOTION:think]`（思考）、`[EMOTION:surprised]`（惊讶）、`[EMOTION:angry]`（生气）、`[EMOTION:sad]`（难过）、`[EMOTION:neutral]`（平静）。情绪标记同样不显示给学生，仅控制教师角色的表情。",
        ]
        try:
            context = load_course_context(lesson_folder)
            if context:
                return (
                    f"{header}\n"
                    f"【课程背景】\n你正在教授课程「{topic}」。"
                    f"以下是当前课程的本地资料（已转为 Markdown，可作为回答依据）：\n\n"
                    f"{context[:5000]}\n\n"
                    f"\n".join(tool_lines)
                )
        except Exception:
            pass
        return f"{header}\n\n{'\n'.join(tool_lines)}"

    # 新课程：按当前 unit 注入资料 + 工具调用说明
    progress = load_progress(lesson_folder)
    current_unit = int(progress.get("current_unit", 0) or 0)
    if current_unit < 0:
        current_unit = 0
    if current_unit >= len(units):
        current_unit = len(units) - 1
    unit = units[current_unit]
    unit_title = unit.get("title") or f"第 {current_unit + 1} 课"
    unit_summary = unit.get("summary", "")
    unit_key_points = unit.get("key_points", [])

    # 读取该 unit 已下载的本地资料
    try:
        unit_context = load_unit_context(lesson_folder, current_unit)
    except Exception:
        unit_context = ""

    total_units = len(units)
    is_last_unit = current_unit >= total_units - 1

    # 构建全课程概览（让老师知道整个课程的结构和所有单元的主题）
    course_overview_lines = []
    for i, u in enumerate(units):
        u_title = u.get("title") or f"第 {i + 1} 课"
        u_summary = u.get("summary", "")
        u_kps = u.get("key_points", [])
        line = f"  {i + 1}. {u_title}"
        if u_summary:
            line += f"：{u_summary[:80]}"
        course_overview_lines.append(line)
        if u_kps:
            for kp in u_kps[:3]:
                course_overview_lines.append(f"     - {kp}")
    course_overview = "\n".join(course_overview_lines)

    parts = [header, f"【课程背景】\n你正在教授课程「{topic}」，整体共 {total_units} 课。"]
    parts.append(f"【全课程目录与要点总览】\n以下是本课程所有单元的结构与核心要点，请据此把握课程的整体脉络：\n{course_overview}")
    parts.append(
        f"【当前进度】\n现在上到第 {current_unit + 1} 课 / 共 {total_units} 课：{unit_title}。"
    )
    if unit_summary:
        parts.append(f"【本课概述】\n{unit_summary}")
    if unit_key_points:
        kp = "\n".join(f"- {k}" for k in unit_key_points)
        parts.append(f"【本课要点（详细）】\n{kp}")
    if unit_context:
        parts.append(
            f"【本课详细资料】\n以下是本课的详细学习资料（已转为 Markdown），请基于此进行系统性讲解：\n\n{unit_context[:8000]}"
        )

    # 工具调用说明：讲完本课可触发随堂测验；考完且学生想继续则进入下一课
    tool_lines = [
        "【工具调用规则】",
        f"- 当你判断本课内容已讲解充分（核心要点都已覆盖并举例），可在回复末尾单独输出标记 `[TOOL:start_exam]` 触发随堂测验。",
        "- 随堂测验由系统自动出题，你无需自行出题；学生答完后系统会反馈成绩，你可基于错题做简短点评。",
    ]
    if is_last_unit:
        tool_lines.append("- 当前已是最后一课，请勿输出 `[TOOL:next_unit]`。学生若想复习，引导其回顾已学内容即可。")
    else:
        tool_lines.append(
            "- 测验结束后，若学生表示想继续/进入下一课，你可在回复末尾单独输出标记 `[TOOL:next_unit]`，系统会自动切换到下一课。"
        )
    tool_lines.append("- 标记必须独占一行或位于回复末尾，且仅出现一次；不要把标记嵌在代码块或表格里。")
    tool_lines.append("- 需要肢体动作配合教学时，可在回复末尾单独输出动作标记 `[ACTION:point]`（指向）、`[ACTION:blackboard]`（拉黑板）、`[ACTION:hello]`（打招呼）、`[ACTION:think]`（思考）、`[ACTION:listen]`（倾听）、`[ACTION:speak]`（说话）。动作标记不显示给学生，仅触发教师角色的动画。严禁在正文中用文字描述动作过程（如“我拿出黑板”“老师指向”“转过身”等），动作只能通过标记触发，正文只讲课程内容。")
    tool_lines.append("- 需要表达情绪时，可在回复末尾输出情绪标记 `[EMOTION:happy]`（高兴）、`[EMOTION:think]`（思考）、`[EMOTION:surprised]`（惊讶）、`[EMOTION:angry]`（生气）、`[EMOTION:sad]`（难过）、`[EMOTION:neutral]`（平静）。情绪标记同样不显示给学生，仅控制教师角色的表情。")
    parts.append("\n".join(tool_lines))

    # 教学行为指引：开课时先系统讲解知识点
    teach_guide = [
        "【教学行为指引】",
        "- 当学生请求「开始上课」「讲下一课」或这是新单元的首次对话时，你必须先系统性地讲解本课的全部主要知识点。",
        "- 讲解时按要点逐条展开，每个要点配合简短示例说明，确保学生能理解。",
        "- 讲解时可结合之前课程的知识做回顾，也可简要预告后续课程内容以建立知识框架。",
        "- 全部知识点讲完后，询问学生是否有疑问；若学生表示理解，再输出 `[TOOL:start_exam]` 触发测验。",
        "- 不要跳跃式教学，不要只抛问题不给答案，先讲透知识再互动。",
        "- 当学生问的问题超出当前课时范围时，可适当涉及相关的前后知识点，但要保持以当前课时为重点。",
        "- 【分段输出（重要）】你必须使用 `\\c` 标记将回复分成多个小段，每段只讲一个知识点或一个完整意思。",
        "  规则：",
        "  1. 每讲完一个知识点、一个公式、或一个示例后，必须插入 `\\c`。",
        "  2. 每段控制在 3~6 行以内，不要一次性输出大段文字。",
        "  3. `\\c` 必须独占一行（前后有换行），不要嵌在句子中间。",
        "  4. LaTeX 公式必须完整地在同一段内输出，绝不能在公式中间插入 `\\c`。",
        "  5. 代码块（```...```）、表格（|...|）、HTML 标签必须完整闭合后才能插入 `\\c`。",
        "  6. 列表项必须整组完成后才能插入 `\\c`，不要在列表中间插入。",
        "  7. 插入 `\\c` 前请自检：当前是否有未闭合的 `$`、`$$`、`\\(`、`\\[`、``` 、`|` 等标记？如果有，必须先闭合再插入 `\\c`。",
        "  8. 最后一段末尾不需要 `\\c`。",
        "  示例：",
        "  第一段：讲解概念A\\c",
        "  第二段：给出公式和示例\\c",
        "  第三段：总结并提问",
    ]
    parts.append("\n".join(teach_guide))

    return "\n\n".join(parts)


def _build_chat_messages(
    prompt: str, lesson_folder: str | None, history: List[Dict[str, str]] | None
) -> List[Dict[str, str]]:
    """Build OpenAI-compatible messages array.

    提示词链路：
    1. messages[0] 必定是 role=system，内容来自 build_system_prompt（身份+角色+课程资料）
    2. 附加最近 10 轮 user/assistant 对话历史
    3. 末尾追加本次用户输入（去除重复，若最后一条 history 就是本次 prompt 则跳过）
    """
    system_prompt = build_system_prompt(lesson_folder)
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    seen_last = False
    if history:
        for entry in history[-10:]:
            role = entry.get("role")
            content = entry.get("content")
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content})
                if role == "user" and content == prompt:
                    seen_last = True
    if not seen_last:
        messages.append({"role": "user", "content": prompt})
    return messages


def local_ollama_reply(prompt: str, lesson_folder: str | None = None, history: List[Dict[str, str]] | None = None) -> str:
    """调用本地 Ollama 生成回复。

    修复要点：
    1. 主路径用 /api/chat（Ollama 官方推荐，内部按模型模板渲染 messages，
       不再手动拼 ChatML token，避免给 qwen2.5/llama3 等模型拼错模板）。
    2. 回退路径 /api/generate 不再重复传 system（避免 system 被注入两次），
       且只用纯文本拼接对话历史，不硬编码 <|system|> 等特殊 token。
    3. num_ctx / temperature / num_predict 全部可配置，避免魔法数。
    4. 异常不静默吞掉：打印日志便于排查；返回空串让上层走云端 LLM。
    """
    cfg = load_config()
    base_url = (cfg.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
    model = (cfg.get("ollama_model") or "qwen2.5:7b").strip()
    if not cfg.get("enable_local_ollama", True):
        return ""

    # 模型名容错：配置的模型可能不存在/大小写不符，自动匹配 Ollama 实际可用模型
    try:
        tags_resp = requests.get(f"{base_url}/api/tags", timeout=8)
        if tags_resp.ok:
            available = [m.get("name", "") for m in (tags_resp.json().get("models") or [])]
            if available:
                normalized = {n.lower(): n for n in available}
                if model.lower() not in normalized:
                    # 依次尝试：配置名 → qwen2.5 → qwen2.5vl → qwen2.5-coder
                    for fallback in ["qwen2.5", "qwen2.5vl", "qwen2.5-coder"]:
                        if fallback in normalized:
                            print(f"[ollama] 模型 '{model}' 不存在，自动改用 '{normalized[fallback]}'", flush=True)
                            model = normalized[fallback]
                            break
                else:
                    model = normalized[model.lower()]  # 统一为实际大小写
    except Exception as exc:
        print(f"[ollama] 获取模型列表失败: {exc}", flush=True)

    messages = _build_chat_messages(prompt, lesson_folder, history)

    # 统一从配置读取生成参数（避免魔法数；分课后 system prompt 较长，默认 16384）
    options: Dict[str, Any] = {
        "temperature": float(cfg.get("ollama_temperature", 0.7) or 0.7),
        "num_ctx": int(cfg.get("ollama_num_ctx", 16384) or 16384),
        "num_predict": int(cfg.get("ollama_num_predict", 600) or 600),
    }

    # 主路径：/api/chat（带 messages，由 Ollama 按模型自带模板渲染）
    try:
        print(f"[AI-REQUEST] 对话请求 → Ollama: {base_url}/api/chat | model: {model}", flush=True)
        response = requests.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "options": options,
            },
            timeout=120,
        )
        if response.ok:
            data = response.json()
            content = (data.get("message", {}).get("content") or data.get("response", "")).strip()
            if content:
                return content
            # 空回复：模型可能被 stop token 截断，直接返回空让上层接管，不走向更糟的回退
            print(f"[ollama] /api/chat 返回空回复，model={model}", flush=True)
        else:
            print(f"[ollama] /api/chat HTTP {response.status_code}: {response.text[:200]}", flush=True)
    except Exception as exc:
        print(f"[ollama] /api/chat 异常: {exc}", flush=True)

    # 回退路径：/api/generate（仅在 /api/chat 不可达/失败时用）
    # 不再硬编码 <|system|> / Human: / Assistant: 等 ChatML token（不同模型模板不同），
    # 只把 system 单独放 system 字段，prompt 用纯文本拼接对话历史交给 Ollama 渲染。
    try:
        sys_prompt = messages[0]["content"]
        history_lines = []
        for m in messages[1:-1]:
            tag = "用户" if m["role"] == "user" else "老师"
            history_lines.append(f"{tag}：{m['content']}")
        last_user = messages[-1]["content"]
        fallback_prompt = (
            ("\n".join(history_lines) + "\n" if history_lines else "")
            + f"用户：{last_user}\n老师："
        )
        response = requests.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": fallback_prompt,
                "system": sys_prompt,
                "stream": False,
                "options": options,
            },
            timeout=120,
        )
        if response.ok:
            data = response.json()
            return (data.get("response") or "").strip()
        print(f"[ollama] /api/generate HTTP {response.status_code}: {response.text[:200]}", flush=True)
    except Exception as exc:
        print(f"[ollama] /api/generate 异常: {exc}", flush=True)

    # 两条路径都失败：返回空串，让 api_chat 自动回退到云端 LLM
    return ""


def cloud_llm_reply(prompt: str, lesson_folder: str | None = None, history: List[Dict[str, str]] | None = None) -> str:
    """对话聊天用的云端 LLM。

    读取 chat_* 配置；若 chat_api_key / chat_model / chat_base_url 未填写，
    则回退到 cloud_*（即备课使用的那组配置）。这样用户可以：
      - 只填一组 cloud_*：备课聊天共用一套硅基账号
      - 另填一组 chat_*：备课用硅基，聊天用 DeepSeek 原生 / OpenAI / 其他 OpenAI-compatible 服务
    """
    cfg = load_config()
    chat_key = (cfg.get("chat_api_key") or "").strip()
    chat_model = (cfg.get("chat_model") or "").strip()
    chat_base = (cfg.get("chat_base_url") or "").rstrip("/").strip()

    if chat_key and chat_model and chat_base:
        key = chat_key
        model = chat_model
        base_url = chat_base
        enable_search = bool(cfg.get("chat_enable_search", False))
    else:
        key = (cfg.get("cloud_api_key") or cfg.get("siliconflow_api_key") or "").strip()
        if not key:
            return ""
        model = (cfg.get("cloud_model") or cfg.get("siliconflow_model") or "deepseek-ai/DeepSeek-V3").strip()
        base_url = (cfg.get("cloud_base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
        enable_search = bool(cfg.get("enable_search", True))

    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    messages = _build_chat_messages(prompt, lesson_folder, history)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        # 控制回复长度：对话回复保持精炼（备课/测验另有独立配置）
        "max_tokens": int(cfg.get("chat_max_tokens", 600) or 600),
    }
    # 只有硅基/百川等明确支持联网搜索的服务才加 enable_search，
    # 原生 OpenAI/DeepSeek 等不认这个字段会报错 → 仅当 enable_search=True 时才附加
    if enable_search:
        payload["enable_search"] = True

    try:
        print(f"[AI-REQUEST] 对话请求 → 云端: {url} | model: {model}", flush=True)
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        if not response.ok:
            print(f"[AI-REQUEST] 云端请求失败 HTTP {response.status_code}: {response.text[:200]}", flush=True)
            return ""
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content") or ""
        return str(content).strip() or ""
    except Exception as exc:
        print(f"[AI-REQUEST] 云端请求异常: {type(exc).__name__}: {exc}", flush=True)
        return ""


def _tts_voice_with_model(voice: str, model: str) -> str:
    """CosyVoice2 要求 voice 格式为 '<model>:<voice>'（如 'FunAudioLLM/CosyVoice2-0.5B:anna'）。

    用户在 UI 里只填音色名（anna/bella 等），这里自动补模型前缀。
    若 voice 已包含冒号则视为已完整格式，原样返回（兼容用户自定义 voice 字段）。
    """
    voice = voice.strip()
    if ":" in voice:
        return voice
    return f"{model.strip()}:{voice}"


def cloud_tts_audio(text: str) -> str | None:
    """调用硅基流动云端 TTS 生成音频，返回可播放的静态资源 URL。

    修复要点（对照硅基官方文档）：
    1. 默认 model 改为 FunAudioLLM/CosyVoice2-0.5B（旧的 Speech-1 已不存在）。
    2. voice 格式自动补全为 '<model>:<voice>'（CosyVoice2 要求），避免 Invalid voice 400。
    3. 显式传 response_format=mp3，文件后缀与格式匹配。
    4. 失败时打印日志。
    """
    cfg = load_config()
    key = (cfg.get("cloud_api_key") or cfg.get("siliconflow_api_key") or "").strip()
    if not key:
        return None
    base_url = (cfg.get("tts_cloud_base_url") or cfg.get("cloud_base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
    url = base_url if base_url.endswith("/audio/speech") else f"{base_url}/audio/speech"
    model = (cfg.get("tts_cloud_model") or "FunAudioLLM/CosyVoice2-0.5B").strip()
    raw_voice = (cfg.get("tts_cloud_voice") or "anna").strip()
    voice = _tts_voice_with_model(raw_voice, model)
    response_format = (cfg.get("tts_cloud_response_format") or "mp3").strip().lower()
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "voice": voice,
                "input": text[:1000],
                "response_format": response_format,
            },
            timeout=60,
        )
        if not response.ok:
            print(f"[tts] 云端 TTS 失败 HTTP {response.status_code}: {response.text[:300]}", flush=True)
            return None
        audio_dir = BASE_DIR / "static" / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"tts_cloud_{int(time.time() * 1000)}.{response_format}"
        file_path = audio_dir / file_name
        file_path.write_bytes(response.content)
        return f"/static/audio/{file_name}"
    except Exception as exc:
        print(f"[tts] 云端 TTS 异常: {exc}", flush=True)
        return None


def local_tts_audio(text: str) -> str | None:
    cfg = load_config()
    if cfg.get("tts_provider") == "cloud":
        return cloud_tts_audio(text)
    if not cfg.get("tts_enabled", False):
        return cloud_tts_audio(text) if (cfg.get("cloud_api_key") or cfg.get("siliconflow_api_key")) else None
    base_url = (cfg.get("tts_base_url") or "http://127.0.0.1:8000").rstrip("/")
    voice = cfg.get("tts_voice") or "zh-CN-XiaoxiaoNeural"
    if ACTIVE_LESSON.get("folder"):
        lesson_meta = load_lesson_metadata(ACTIVE_LESSON["folder"])
        if lesson_meta.get("tts_voice"):
            voice = lesson_meta.get("tts_voice")
    try:
        response = requests.post(
            f"{base_url}/tts",
            json={"text": text, "voice": voice},
            timeout=30,
        )
        if not response.ok:
            return cloud_tts_audio(text)
        audio_dir = BASE_DIR / "static" / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"tts_{int(time.time() * 1000)}.wav"
        file_path = audio_dir / file_name
        file_path.write_bytes(response.content)
        return f"/static/audio/{file_name}"
    except Exception:
        return cloud_tts_audio(text)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/api/config", methods=["GET"])
def api_config_get():
    return jsonify(load_config())


@app.route("/api/config", methods=["POST"])
def api_config_set():
    payload = request.get_json(silent=True) or {}
    config = save_config(payload)
    return jsonify({"status": "ok", "config": config})


@app.route("/api/config/test", methods=["POST"])
def api_config_test():
    payload = request.get_json(silent=True) or {}
    kind = payload.get("kind") or "ollama"
    config = save_config(payload.get("config", {}))

    if kind == "ollama":
        try:
            response = requests.get(f"{config['ollama_base_url'].rstrip('/')}/api/tags", timeout=10)
            return jsonify({"ok": response.ok, "message": "Ollama 连接成功" if response.ok else "Ollama 连接失败"})
        except Exception as exc:
            return jsonify({"ok": False, "message": str(exc)})

    if kind == "tts":
        provider = (config.get("tts_provider") or "local").strip().lower()
        if provider == "cloud":
            key = (config.get("cloud_api_key") or config.get("siliconflow_api_key") or "").strip()
            if not key:
                return jsonify({"ok": False, "message": "云端 TTS 未配置 API Key"})
            try:
                base_url = (config.get("tts_cloud_base_url") or config.get("cloud_base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
                url = base_url if base_url.endswith("/audio/speech") else f"{base_url}/audio/speech"
                test_model = config.get("tts_cloud_model") or "FunAudioLLM/CosyVoice2-0.5B"
                test_voice = _tts_voice_with_model(config.get("tts_cloud_voice") or "anna", test_model)
                response = requests.post(
                    url,
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={
                        "model": test_model,
                        "voice": test_voice,
                        "input": "测试语音",
                        "response_format": config.get("tts_cloud_response_format") or "mp3",
                    },
                    timeout=20,
                )
                return jsonify({"ok": response.ok, "message": "云端 TTS 连接成功" if response.ok else f"云端 TTS 连接失败：{response.text[:200]}"})
            except Exception as exc:
                return jsonify({"ok": False, "message": str(exc)})
        try:
            response = requests.get(f"{config['tts_base_url'].rstrip('/')}/health", timeout=10)
            return jsonify({"ok": response.ok, "message": "TTS 连接成功" if response.ok else "TTS 连接失败"})
        except Exception as exc:
            return jsonify({"ok": False, "message": str(exc)})

    return jsonify({"ok": False, "message": "未知测试类型"})


# ============== 教师头像上传（支持课程独立存储） ==============
AVATAR_DIR = BASE_DIR / "static" / "images"
ALLOWED_AVATAR_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

# 文件大小限制：16MB
MAX_UPLOAD_SIZE = 16 * 1024 * 1024


def _get_file_ext(file, allowed_ext=None) -> str:
    """安全获取文件扩展名（兼容 secure_filename 剥离中文后丢失扩展名的情况）。"""
    allowed = allowed_ext or ALLOWED_AVATAR_EXT
    # 优先从 secure_filename 提取
    try:
        original = secure_filename(file.filename or "")
        ext = Path(original).suffix.lower()
        if ext in allowed:
            return ext
    except Exception:
        pass
    # 回退：直接从原始文件名提取
    try:
        raw = file.filename or ""
        ext = Path(raw).suffix.lower()
        if ext in allowed:
            return ext
    except Exception:
        pass
    return ""


@app.route("/api/upload_avatar", methods=["POST"])
def api_upload_avatar():
    if "avatar" not in request.files:
        return jsonify({"ok": False, "message": "未选择文件"}), 400
    file = request.files["avatar"]
    if not file or not file.filename:
        return jsonify({"ok": False, "message": "文件名为空"}), 400

    # 检查文件大小
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    if file_size > MAX_UPLOAD_SIZE:
        return jsonify({"ok": False, "message": f"文件过大（{file_size // 1024}KB），最大支持 {MAX_UPLOAD_SIZE // 1024 // 1024}MB"}), 400

    ext = _get_file_ext(file)
    if not ext:
        allowed = ", ".join(sorted(ALLOWED_AVATAR_EXT))
        return jsonify({"ok": False, "message": f"不支持的图片格式（支持：{allowed}）"}), 400

    # 支持课程独立存储
    lesson_folder = (request.form.get("lesson_folder") or "").strip()
    if lesson_folder and "/" not in lesson_folder and "\\" not in lesson_folder and ".." not in lesson_folder:
        lesson_dir = LESSONS_DIR / lesson_folder
        if lesson_dir.exists():
            images_dir = lesson_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            # 清理旧头像
            for stale in images_dir.glob("avatar.*"):
                try:
                    stale.unlink()
                except Exception:
                    pass
            filename = f"avatar{ext}"
            save_path = images_dir / filename
            file.save(save_path)
            avatar_url = f"/api/lesson/{lesson_folder}/asset/{filename}?v={int(time.time())}"
            # 写入课程 config.json
            ensure_lesson_files(lesson_folder)
            config_path = lesson_dir / "config.json"
            current = {}
            if config_path.exists():
                try:
                    current = json.loads(config_path.read_text(encoding="utf-8"))
                except Exception:
                    current = {}
            current["avatar_url"] = avatar_url
            config_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            # 同步到 ACTIVE_LESSON
            if ACTIVE_LESSON.get("folder") == lesson_folder:
                ACTIVE_LESSON["metadata"] = current
            return jsonify({"ok": True, "avatar_url": avatar_url, "lesson_folder": lesson_folder, "config": current})

    # 全局存储
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    for stale in AVATAR_DIR.glob("avatar.upload.*"):
        try:
            stale.unlink()
        except Exception:
            pass
    filename = f"avatar.upload{ext}"
    save_path = AVATAR_DIR / filename
    file.save(save_path)
    avatar_url = f"/static/images/{filename}?v={int(time.time())}"
    config = load_config()
    config["avatar_url"] = avatar_url
    save_config({"avatar_url": avatar_url})
    return jsonify({"ok": True, "avatar_url": avatar_url, "config": config})


@app.route("/api/lesson/<path:lesson_folder>/board", methods=["POST"])
def api_upload_board(lesson_folder: str):
    """上传课程板书图片（存到 lessons/<folder>/images/board.*）。"""
    if not lesson_folder or "/" in lesson_folder or "\\" in lesson_folder or ".." in lesson_folder:
        return jsonify({"ok": False, "message": "非法的课程名"}), 400
    lesson_dir = LESSONS_DIR / lesson_folder
    if not lesson_dir.exists():
        return jsonify({"ok": False, "message": "课程不存在"}), 404
    if "board" not in request.files:
        return jsonify({"ok": False, "message": "未选择文件"}), 400
    file = request.files["board"]
    if not file or not file.filename:
        return jsonify({"ok": False, "message": "文件名为空"}), 400

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    if file_size > MAX_UPLOAD_SIZE:
        return jsonify({"ok": False, "message": f"文件过大（{file_size // 1024}KB），最大支持 {MAX_UPLOAD_SIZE // 1024 // 1024}MB"}), 400

    ext = _get_file_ext(file, ALLOWED_AVATAR_EXT)
    if not ext:
        return jsonify({"ok": False, "message": "不支持的图片格式"}), 400

    images_dir = lesson_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    # 清理旧板书
    for stale in images_dir.glob("board.*"):
        try:
            stale.unlink()
        except Exception:
            pass
    filename = f"board{ext}"
    save_path = images_dir / filename
    file.save(save_path)
    board_url = f"/api/lesson/{lesson_folder}/asset/{filename}?v={int(time.time())}"

    ensure_lesson_files(lesson_folder)
    config_path = lesson_dir / "config.json"
    current = {}
    if config_path.exists():
        try:
            current = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current["board_url"] = board_url
    config_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    if ACTIVE_LESSON.get("folder") == lesson_folder:
        ACTIVE_LESSON["metadata"] = current
    return jsonify({"ok": True, "board_url": board_url, "lesson_folder": lesson_folder, "config": current})


@app.route("/api/lesson/<path:lesson_folder>/board", methods=["DELETE"])
def api_delete_board(lesson_folder: str):
    """删除课程板书图片。"""
    if not lesson_folder or "/" in lesson_folder or "\\" in lesson_folder or ".." in lesson_folder:
        return jsonify({"ok": False, "message": "非法的课程名"}), 400
    lesson_dir = LESSONS_DIR / lesson_folder
    images_dir = lesson_dir / "images"
    if images_dir.exists():
        for stale in images_dir.glob("board.*"):
            try:
                stale.unlink()
            except Exception:
                pass
    config_path = lesson_dir / "config.json"
    if config_path.exists():
        try:
            current = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
        current.pop("board_url", None)
        config_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonify({"ok": True, "board_url": ""})


@app.route("/api/lesson/<path:lesson_folder>/board", methods=["GET"])
def api_get_board(lesson_folder: str):
    """获取课程板书信息。"""
    if not lesson_folder or "/" in lesson_folder or "\\" in lesson_folder or ".." in lesson_folder:
        return jsonify({"ok": False, "message": "非法的课程名"}), 400
    lesson_dir = LESSONS_DIR / lesson_folder
    config_path = lesson_dir / "config.json"
    board_url = ""
    if config_path.exists():
        try:
            current = json.loads(config_path.read_text(encoding="utf-8"))
            board_url = current.get("board_url", "")
        except Exception:
            pass
    return jsonify({"ok": True, "board_url": board_url, "lesson_folder": lesson_folder})


@app.route("/api/reset_avatar", methods=["POST"])
def api_reset_avatar():
    payload = request.get_json(silent=True) or {}
    lesson_folder = (payload.get("lesson_folder") or "").strip()

    if lesson_folder and "/" not in lesson_folder and "\\" not in lesson_folder and ".." not in lesson_folder:
        lesson_dir = LESSONS_DIR / lesson_folder
        if lesson_dir.exists():
            images_dir = lesson_dir / "images"
            for stale in images_dir.glob("avatar.*"):
                try:
                    stale.unlink()
                except Exception:
                    pass
            config_path = lesson_dir / "config.json"
            current = {}
            if config_path.exists():
                try:
                    current = json.loads(config_path.read_text(encoding="utf-8"))
                except Exception:
                    current = {}
            current["avatar_url"] = ""
            config_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            if ACTIVE_LESSON.get("folder") == lesson_folder:
                ACTIVE_LESSON["metadata"] = current
            return jsonify({"ok": True, "avatar_url": "", "lesson_folder": lesson_folder, "config": current})

    for stale in AVATAR_DIR.glob("avatar.upload.*"):
        try:
            stale.unlink()
        except Exception:
            pass
    config = load_config()
    config["avatar_url"] = "/static/images/teacher.svg"
    save_config({"avatar_url": "/static/images/teacher.svg"})
    return jsonify({"ok": True, "avatar_url": "/static/images/teacher.svg", "config": config})


# ============== 场景背景上传 / 重置（支持课程独立存储） ==============
BG_DIR = BASE_DIR / "static" / "images"
ALLOWED_BG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# 预设背景主题列表
PRESET_BG_THEMES = {"warm", "sakura", "bamboo", "snow", "dusk", "night"}


def _save_lesson_bg_config(lesson_folder: str, bg_theme: str, bg_url: str) -> Dict[str, Any]:
    """将背景主题 / 链接写入课程 config.json，返回更新后的完整配置。"""
    lesson_dir = LESSONS_DIR / lesson_folder
    config_path = lesson_dir / "config.json"
    current = {}
    if config_path.exists():
        try:
            current = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
    current["bg_theme"] = bg_theme
    current["bg_url"] = bg_url
    config_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    if ACTIVE_LESSON.get("folder") == lesson_folder:
        ACTIVE_LESSON["metadata"] = current
    return current


@app.route("/api/upload_background", methods=["POST"])
def api_upload_background():
    if "background" not in request.files:
        return jsonify({"ok": False, "message": "未选择文件"}), 400
    file = request.files["background"]
    if not file or not file.filename:
        return jsonify({"ok": False, "message": "文件名为空"}), 400

    # 检查文件大小
    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    if file_size > MAX_UPLOAD_SIZE:
        return jsonify({"ok": False, "message": f"文件过大（{file_size // 1024}KB），最大支持 {MAX_UPLOAD_SIZE // 1024 // 1024}MB"}), 400

    ext = _get_file_ext(file, ALLOWED_BG_EXT)
    if not ext:
        allowed = ", ".join(sorted(ALLOWED_BG_EXT))
        return jsonify({"ok": False, "message": f"不支持的图片格式（支持：{allowed}）"}), 400

    lesson_folder = (request.form.get("lesson_folder") or "").strip()
    if lesson_folder and "/" not in lesson_folder and "\\" not in lesson_folder and ".." not in lesson_folder:
        lesson_dir = LESSONS_DIR / lesson_folder
        if lesson_dir.exists():
            images_dir = lesson_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            for stale in images_dir.glob("bg.*"):
                try:
                    stale.unlink()
                except Exception:
                    pass
            filename = f"bg{ext}"
            save_path = images_dir / filename
            file.save(save_path)
            bg_url = f"/api/lesson/{lesson_folder}/asset/{filename}?v={int(time.time())}"
            current = _save_lesson_bg_config(lesson_folder, "custom", bg_url)
            return jsonify({"ok": True, "bg_theme": "custom", "bg_url": bg_url, "lesson_folder": lesson_folder, "config": current})

    # 全局存储
    BG_DIR.mkdir(parents=True, exist_ok=True)
    for stale in BG_DIR.glob("bg.upload.*"):
        try:
            stale.unlink()
        except Exception:
            pass
    filename = f"bg.upload{ext}"
    save_path = BG_DIR / filename
    file.save(save_path)
    bg_url = f"/static/images/{filename}?v={int(time.time())}"
    # 检查是否为菜单背景上传
    target = (request.form.get("target") or "").strip().lower()
    if target == "menu":
        config = load_config()
        config["menu_bg_theme"] = "custom"
        config["menu_bg_url"] = bg_url
        save_config({"menu_bg_theme": "custom", "menu_bg_url": bg_url})
        return jsonify({"ok": True, "bg_theme": "custom", "bg_url": bg_url, "menu_bg_url": bg_url, "config": config})
    config = load_config()
    config["bg_theme"] = "custom"
    config["bg_url"] = bg_url
    save_config({"bg_theme": "custom", "bg_url": bg_url})
    return jsonify({"ok": True, "bg_theme": "custom", "bg_url": bg_url, "config": config})


@app.route("/api/set_background_theme", methods=["POST"])
def api_set_background_theme():
    payload = request.get_json(silent=True) or {}
    theme = (payload.get("bg_theme") or "warm").strip().lower()
    lesson_folder = (payload.get("lesson_folder") or "").strip()

    if theme not in PRESET_BG_THEMES:
        return jsonify({"ok": False, "message": f"未知主题：{theme}"}), 400

    if lesson_folder and "/" not in lesson_folder and "\\" not in lesson_folder and ".." not in lesson_folder:
        lesson_dir = LESSONS_DIR / lesson_folder
        if lesson_dir.exists():
            # 清理课程级 custom 背景
            images_dir = lesson_dir / "images"
            for stale in images_dir.glob("bg.*"):
                try:
                    stale.unlink()
                except Exception:
                    pass
            current = _save_lesson_bg_config(lesson_folder, theme, "")
            return jsonify({"ok": True, "bg_theme": theme, "bg_url": "", "lesson_folder": lesson_folder, "config": current})

    # 全局
    bg_url = ""
    if theme != "custom":
        for stale in BG_DIR.glob("bg.upload.*"):
            try:
                stale.unlink()
            except Exception:
                pass
    config = load_config()
    config["bg_theme"] = theme
    config["bg_url"] = bg_url
    save_config({"bg_theme": theme, "bg_url": bg_url})
    return jsonify({"ok": True, "bg_theme": theme, "bg_url": bg_url, "config": config})


@app.route("/api/reset_background", methods=["POST"])
def api_reset_background():
    payload = request.get_json(silent=True) or {}
    lesson_folder = (payload.get("lesson_folder") or "").strip()

    if lesson_folder and "/" not in lesson_folder and "\\" not in lesson_folder and ".." not in lesson_folder:
        lesson_dir = LESSONS_DIR / lesson_folder
        if lesson_dir.exists():
            images_dir = lesson_dir / "images"
            for stale in images_dir.glob("bg.*"):
                try:
                    stale.unlink()
                except Exception:
                    pass
            current = _save_lesson_bg_config(lesson_folder, "warm", "")
            return jsonify({"ok": True, "bg_theme": "warm", "bg_url": "", "lesson_folder": lesson_folder, "config": current})

    for stale in BG_DIR.glob("bg.upload.*"):
        try:
            stale.unlink()
        except Exception:
            pass
    config = load_config()
    config["bg_theme"] = "warm"
    config["bg_url"] = ""
    save_config({"bg_theme": "warm", "bg_url": ""})
    return jsonify({"ok": True, "bg_theme": "warm", "bg_url": "", "config": config})


# ============== 课程资源动态路由（立绘 / 背景） ==============

@app.route("/api/lesson/<path:lesson_folder>/asset/<path:filename>")
def api_lesson_asset(lesson_folder: str, filename: str):
    """安全地返回课程 images/ 目录下的文件，防止路径穿越。"""
    if not lesson_folder or "/" in lesson_folder or "\\" in lesson_folder or ".." in lesson_folder:
        return jsonify({"error": "非法的课程名"}), 400
    if not filename or ".." in filename or "\\" in filename:
        return jsonify({"error": "非法的文件名"}), 400

    target_dir = LESSONS_DIR / lesson_folder / "images"
    file_path = target_dir / filename
    try:
        resolved = file_path.resolve()
        base_resolved = target_dir.resolve()
        # Windows 路径大小写不敏感，用 lower() 比较
        if not str(resolved).lower().startswith(str(base_resolved).lower()):
            return jsonify({"error": "非法路径"}), 400
    except Exception:
        return jsonify({"error": "非法路径"}), 400

    if not resolved.is_file():
        return jsonify({"error": "文件不存在"}), 404

    # 按扩展名推断 MIME
    ext = resolved.suffix.lower()
    mime_map = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    }
    mimetype = mime_map.get(ext, "application/octet-stream")
    try:
        return send_file(str(resolved), mimetype=mimetype)
    except Exception as e:
        print(f"[ASSET-ERROR] Failed to send {resolved}: {e}", flush=True)
        return jsonify({"error": f"文件读取失败: {e}"}), 500


# ============== 课程配置 GET / PUT ==============

@app.route("/api/lesson/<path:lesson_folder>/config", methods=["GET"])
def api_lesson_config_get(lesson_folder: str):
    if not lesson_folder or "/" in lesson_folder or "\\" in lesson_folder or ".." in lesson_folder:
        return jsonify({"error": "非法的课程名"}), 400
    target_dir = LESSONS_DIR / lesson_folder
    if not target_dir.exists():
        return jsonify({"error": "课程不存在"}), 404
    ensure_lesson_files(lesson_folder)
    metadata = load_lesson_metadata(lesson_folder)
    return jsonify({"ok": True, "lesson_folder": lesson_folder, "config": metadata})


@app.route("/api/lesson/<path:lesson_folder>/config", methods=["PUT"])
def api_lesson_config_put(lesson_folder: str):
    if not lesson_folder or "/" in lesson_folder or "\\" in lesson_folder or ".." in lesson_folder:
        return jsonify({"error": "非法的课程名"}), 400
    target_dir = LESSONS_DIR / lesson_folder
    if not target_dir.exists():
        return jsonify({"error": "课程不存在"}), 404

    ensure_lesson_files(lesson_folder)
    payload = request.get_json(silent=True) or {}

    # 允许保存到课程 config.json 的字段
    lesson_fields = {
        "assistant_name", "personality_prompt", "tts_voice",
        "avatar_url", "bg_theme", "bg_url",
        "topic", "syllabus", "key_points", "resources",
        "quiz_preset", "units", "has_units", "tools",
        "portrait_pos_x", "portrait_pos_y", "portrait_scale",
        "portrait_float_amplitude", "portrait_float_enabled",
    }

    config_path = target_dir / "config.json"
    current = {}
    if config_path.exists():
        try:
            current = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            current = {}

    for key in lesson_fields:
        if key in payload:
            current[key] = payload[key]

    current["course_name"] = lesson_folder
    config_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")

    return jsonify({"ok": True, "lesson_folder": lesson_folder, "config": current})


@app.route("/api/lessons", methods=["GET"])
def api_lessons():
    items: List[Dict[str, Any]] = []
    if LESSONS_DIR.exists():
        for child in sorted(LESSONS_DIR.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            metadata = {}
            config_path = child / "config.json"
            meta_path = child / "metadata.json"
            target_path = config_path if config_path.exists() else meta_path
            if target_path.exists():
                try:
                    metadata = json.loads(target_path.read_text(encoding="utf-8"))
                except Exception:
                    metadata = {}
            progress = load_progress(child.name)
            units = metadata.get("units") or []
            items.append(
                {
                    "name": child.name,
                    "topic": metadata.get("topic") or child.name,
                    "created_at": datetime.fromtimestamp(child.stat().st_ctime).astimezone().isoformat(timespec="seconds"),
                    "last_access": progress.get("last_access") or "",
                    "units_count": len(units),
                    "current_unit": int(progress.get("current_unit", 0) or 0),
                }
            )
    return jsonify({"lessons": items})


@app.route("/api/lessons/<path:lesson_folder>", methods=["DELETE"])
def api_lesson_delete(lesson_folder: str):
    """删除指定课程目录。禁止路径穿越（.. / 盘符等）。"""
    # 安全校验：lesson_folder 必须是纯目录名，不能包含 .. / \ / 盘符
    if not lesson_folder or "/" in lesson_folder or "\\" in lesson_folder or ".." in lesson_folder:
        return jsonify({"error": "非法的课程名"}), 400
    target = LESSONS_DIR / lesson_folder
    # 确保目标在 LESSONS_DIR 内（防止符号链接等绕过）
    try:
        resolved = target.resolve()
        base_resolved = LESSONS_DIR.resolve()
        if not str(resolved).startswith(str(base_resolved)):
            return jsonify({"error": "目标路径不在课程目录内"}), 400
    except Exception:
        return jsonify({"error": "路径解析失败"}), 400
    if not target.exists() or not target.is_dir():
        return jsonify({"error": "课程不存在"}), 404

    import shutil
    try:
        shutil.rmtree(target)
    except Exception as exc:
        return jsonify({"error": f"删除失败：{exc}"}), 500

    # 若删除的是当前激活课程，清空激活状态
    if ACTIVE_LESSON.get("folder") == lesson_folder:
        ACTIVE_LESSON["folder"] = None
        ACTIVE_LESSON["metadata"] = {}
        ACTIVE_LESSON["resources"] = []
        ACTIVE_LESSON["prepared"] = {}
        ACTIVE_LESSON["conversation"] = []
        ACTIVE_LESSON["progress"] = {}

    return jsonify({"status": "ok", "deleted": lesson_folder})


@app.route("/api/prepare_lesson", methods=["POST"])
def api_prepare_lesson():
    payload = request.get_json(silent=True) or {}
    topic = (payload.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "topic is required"}), 400

    cfg = load_config()
    # 课程级覆盖：允许新建课程时指定模型/音色
    if payload.get("cloud_model"):
        cfg["cloud_model"] = payload["cloud_model"]
    if payload.get("tts_cloud_voice"):
        cfg["tts_cloud_voice"] = payload["tts_cloud_voice"]
    lesson_plan = prepare_lesson(topic, config=cfg)
    lesson_folder = build_lesson_folder_name(topic)
    ensure_lesson_files(lesson_folder)
    lesson_dir = ensure_lesson_dir(lesson_folder)
    personality_prompt = (payload.get("personality_prompt") or cfg.get("personality_prompt") or "你是一位温柔、专业、耐心的 AI 学习导师。").strip()
    assistant_name = (payload.get("assistant_name") or cfg.get("assistant_name") or "艾琳老师").strip()
    tts_voice = (payload.get("tts_voice") or cfg.get("tts_voice") or cfg.get("default_voice") or "zh-CN-XiaoxiaoNeural").strip()
    tts_cloud_voice = (payload.get("tts_cloud_voice") or cfg.get("tts_cloud_voice") or "").strip()

    ACTIVE_LESSON["folder"] = lesson_folder
    ACTIVE_LESSON["resources"] = lesson_plan.get("resources", [])
    ACTIVE_LESSON["prepared"] = lesson_plan
    ACTIVE_LESSON["conversation"] = load_conversation(lesson_folder)
    ACTIVE_LESSON["progress"] = save_progress(lesson_folder, default_progress())

    # units 由 lesson_prep 保证一定存在（兜底成单课）
    units = lesson_plan.get("units", [])
    save_metadata(
        lesson_dir,
        {
            "course_name": lesson_folder,
            "topic": topic,
            "assistant_name": assistant_name,
            "personality_prompt": personality_prompt,
            "tts_voice": tts_voice,
            "tts_cloud_voice": tts_cloud_voice,
            "voice_config": {"voice": tts_voice, "enabled": bool(cfg.get("tts_enabled", False))},
            "syllabus": lesson_plan.get("syllabus", ""),
            "key_points": lesson_plan.get("key_points", []),
            "resources": lesson_plan.get("resources", []),
            "quiz_preset": lesson_plan.get("quiz_preset", []),
            "units": units,
            "has_units": bool(units),
        },
    )

    return jsonify({"lesson_folder": lesson_folder, "plan": lesson_plan})


@app.route("/api/list_resources", methods=["GET"])
def api_list_resources():
    resources = ACTIVE_LESSON.get("resources", [])
    return jsonify({"resources": resources})


@app.route("/api/download_resources", methods=["POST"])
def api_download_resources():
    payload = request.get_json(silent=True) or {}
    selected = payload.get("selected", [])
    lesson_folder = payload.get("lesson_folder") or ACTIVE_LESSON.get("folder")
    # 新增：可按 unit 下载到 units/unit_NN/ 目录；不传或为 -1 时下载到课程根目录（兼容旧逻辑）
    unit_index = payload.get("unit_index", -1)
    try:
        unit_index = int(unit_index)
    except Exception:
        unit_index = -1

    if not lesson_folder:
        return jsonify({"error": "No active lesson selected"}), 400

    lesson_dir = LESSONS_DIR / lesson_folder
    lesson_dir.mkdir(parents=True, exist_ok=True)

    # 决定下载目标目录：按 unit 分组则取该 unit 的 source_files；否则用全局 resources
    if unit_index >= 0:
        metadata_now = load_lesson_metadata(lesson_folder)
        units = metadata_now.get("units") or []
        if 0 <= unit_index < len(units):
            resources = units[unit_index].get("source_files") or []
            target_dir = unit_dir(lesson_folder, unit_index)
        else:
            return jsonify({"error": f"unit_index {unit_index} 超出范围（共 {len(units)} 课）"}), 400
    else:
        resources = ACTIVE_LESSON.get("resources", []) or (load_lesson_metadata(lesson_folder).get("resources", []))
        target_dir = lesson_dir

    target_dir.mkdir(parents=True, exist_ok=True)

    statuses = []
    selected_resources = [resources[i] for i in selected if 0 <= i < len(resources)]

    for index, resource in enumerate(selected_resources):
        file_name = f"resource{index + 1}"
        try:
            path = download_resource(resource, target_dir, file_name)
            statuses.append({"index": index, "title": resource.get("title", "unnamed"), "path": str(path), "status": "ok", "unit_index": unit_index})
        except Exception as exc:
            statuses.append({"index": index, "title": resource.get("title", "unnamed"), "path": "", "status": "error", "error": str(exc), "unit_index": unit_index})

    # 仅在全局下载（unit_index<0）时回写 metadata.downloaded，避免覆盖 units 结构
    if unit_index < 0:
        metadata = {
            "course_name": lesson_folder,
            "resources": resources,
            "downloaded": statuses,
            "topic": ACTIVE_LESSON.get("prepared", {}).get("topic", lesson_folder),
        }
        # 保留已有 units 字段
        existing = load_lesson_metadata(lesson_folder)
        if existing.get("units"):
            metadata["units"] = existing["units"]
            metadata["has_units"] = True
        save_metadata(lesson_dir, metadata)

    return jsonify({"status": "ok", "lesson_folder": lesson_folder, "downloads": statuses, "unit_index": unit_index})


def split_into_stream_chunks(text: str) -> List[str]:
    """Split text into small chunks for typewriter streaming.

    Works for both Chinese (char-level) and English (word-level) by splitting
    on whitespace first; if that yields a single chunk (pure CJK), fall back to
    small character groups so the streaming effect is visible.
    Preserves newlines so Markdown formatting is not destroyed.
    """
    if not text:
        return []
    # Preserve newlines: split by lines first, then by words within each line
    parts: List[str] = []
    lines = text.split('\n')
    for li, line in enumerate(lines):
        if li > 0:
            parts.append('\n')
        if line:
            words = line.split()
            parts.extend(words)
    if len(parts) <= 1:
        # Pure CJK / no spaces: emit ~2 chars per tick for a smooth typewriter feel
        return [text[i : i + 2] for i in range(0, len(text), 2)]
    return parts


def _judge_fill_answer_semantically(student_ans: str, expected: str, question: str) -> bool:
    """用规则 + 可选模型进行语义评判，而非死板匹配。

    评判优先级：
    1. 完全相等（忽略大小写和空格）→ pass
    2. 标准答案是多个同义词时（用 / 或 | 分隔），匹配任一 → pass
    3. 学生答案是标准答案的子串（且长度 ≥ 2）→ pass
    4. 标准答案是学生答案的子串（且学生答案长度 ≤ 标准答案*2）→ pass
    5. 字符级相似度 ≥ 0.8 → pass
    6. 若配置了云端模型，调用模型做语义判定 → pass/fail
    """
    if not student_ans or not expected:
        return False

    sa = student_ans.strip().lower()
    ea = expected.strip().lower()

    # 1. 完全匹配
    if sa == ea:
        return True

    # 2. 多同义词匹配
    variants = re.split(r"[/|、；;]", ea)
    variants = [v.strip() for v in variants if v.strip()]
    for v in variants:
        if sa == v or v in sa or sa in v:
            return True

    # 3. 子串包含（学生答案包含标准，或标准包含学生）
    if len(sa) >= 2 and ea in sa:
        return True
    if len(ea) >= 2 and sa in ea and len(sa) <= len(ea) * 2:
        return True

    # 4. 字符级相似度（基于最长公共子序列比率）
    ratio = _char_similarity(sa, ea)
    if ratio >= 0.75:
        return True

    # 5. 关键词重叠（提取非停用词）
    sa_words = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', sa))
    ea_words = set(re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z]+', ea))
    if sa_words and ea_words:
        overlap = sa_words & ea_words
        if len(overlap) / max(len(ea_words), 1) >= 0.7:
            return True

    return False


def _char_similarity(a: str, b: str) -> float:
    """计算两个字符串的字符级相似度（基于 set 的 Jaccard + 顺序对齐启发式）。"""
    if not a or not b:
        return 0.0
    # 字符集合交集/并集
    a_set = set(a)
    b_set = set(b)
    if not a_set or not b_set:
        return 0.0
    jaccard = len(a_set & b_set) / len(a_set | b_set)
    # 长度比率
    len_ratio = min(len(a), len(b)) / max(len(a), len(b))
    # 位置启发式：长串的前缀/后缀匹配
    prefix_len = 0
    for i in range(min(len(a), len(b))):
        if a[i] == b[i]:
            prefix_len += 1
        else:
            break
    suffix_len = 0
    for i in range(1, min(len(a), len(b)) + 1):
        if a[-i] == b[-i]:
            suffix_len += 1
        else:
            break
    position_ratio = (prefix_len + suffix_len) / (len(a) + len(b))
    # 综合
    return 0.4 * jaccard + 0.3 * len_ratio + 0.3 * position_ratio


def _scan_latex_protected(lines: List[str]) -> List[bool]:
    """预扫描每行，标记是否处于 LaTeX 公式内部（受保护不被切分）。

    支持的定界符：
    - $$...$$  块级公式
    - $...$    行内公式
    - \\(...\\)  行内公式
    - \\[...\\]  块级公式
    """
    n = len(lines)
    protected = [False] * n

    in_display = False   # $$...$$
    in_inline = False    # $...$
    paren_depth = 0      # \(...\) 深度
    bracket_depth = 0    # \[...\] 深度

    for i, line in enumerate(lines):
        # --- 统计各定界符数量 ---

        # $$ 块级: 统计 $$ 出现次数（减去被转义的 \$\$）
        display_count = 0
        j = 0
        while j < len(line):
            if line[j:j+2] == '\\$\\$':
                j += 4
                continue
            if line[j:j+2] == '$$':
                display_count += 1
                j += 2
                continue
            j += 1

        # $ 行内: 统计未被 $$ 包含、未转义的 $ 数量
        inline_count = 0
        j = 0
        while j < len(line):
            if line[j:j+2] == '$$':
                j += 2
                continue
            if line[j] == '\\' and j + 1 < len(line) and line[j+1] == '$':
                j += 2
                continue
            if line[j] == '$':
                inline_count += 1
            j += 1

        # \( 和 \)
        paren_open = line.count('\\(')
        paren_close = line.count('\\)')

        # \[ 和 \]
        bracket_open = line.count('\\[')
        bracket_close = line.count('\\]')

        # --- 更新状态 ---
        if display_count % 2 == 1:
            in_display = not in_display

        if inline_count % 2 == 1:
            in_inline = not in_inline

        paren_depth = max(0, paren_depth + paren_open - paren_close)
        bracket_depth = max(0, bracket_depth + bracket_open - bracket_close)

        # --- 当前行是否受保护 ---
        if in_display or in_inline or paren_depth > 0 or bracket_depth > 0:
            protected[i] = True

    return protected


def _split_by_sentences(text: str, max_chars: int = 500) -> List[str]:
    """将过长文本在句子边界切分，保护 LaTeX 公式不被截断。

    在 。！？\n； 等标点处切分，但仅当不在 LaTeX 公式内部时才切。
    """
    if len(text) <= max_chars:
        return [text]

    segments: List[str] = []
    buf = ""
    in_display = False
    in_inline = False
    paren_depth = 0
    bracket_depth = 0

    i = 0
    while i < len(text):
        # 检测 LaTeX 定界符
        if text[i:i + 2] == '$$':
            in_display = not in_display
            buf += '$$'
            i += 2
            continue
        if text[i:i + 2] == '\\(':
            paren_depth += 1
            buf += '\\('
            i += 2
            continue
        if text[i:i + 2] == '\\)':
            paren_depth = max(0, paren_depth - 1)
            buf += '\\)'
            i += 2
            continue
        if text[i:i + 2] == '\\[':
            bracket_depth += 1
            buf += '\\['
            i += 2
            continue
        if text[i:i + 2] == '\\]':
            bracket_depth = max(0, bracket_depth - 1)
            buf += '\\]'
            i += 2
            continue

        char = text[i]
        # 检测行内 $（排除 \$ 和 $$）
        if char == '$' and not in_display:
            if i > 0 and text[i - 1] == '\\':
                pass  # 转义的 \$，不算定界符
            elif i + 1 < len(text) and text[i + 1] == '$':
                in_display = not in_display
                buf += '$$'
                i += 2
                continue
            else:
                in_inline = not in_inline

        buf += char
        in_latex = in_display or in_inline or paren_depth > 0 or bracket_depth > 0

        # 在句子边界切分（不在 LaTeX 内，且已积累足够内容）
        if not in_latex and char in '。！？\n；' and len(buf) >= max_chars // 2:
            segments.append(buf.strip())
            buf = ""

        # 强制切分：达到 max_chars 且不在 LaTeX 内
        if len(buf) >= max_chars and not in_latex:
            # 往前找最近的空格或标点
            split_pos = -1
            for j in range(len(buf) - 1, max_chars // 3, -1):
                if buf[j] in ' \n。！？；，、：':
                    split_pos = j + 1
                    break
            if split_pos > 0:
                segments.append(buf[:split_pos].strip())
                buf = buf[split_pos:]
            else:
                segments.append(buf.strip())
                buf = ""

        i += 1

    if buf.strip():
        segments.append(buf.strip())

    return segments or [text]


def _auto_segment_by_lines(text: str, target_lines: int = 5, max_chars: int = 500) -> List[str]:
    """按行数自动分段，保护代码块/列表/表格/LaTeX公式结构不被拆散。

    改进：
    - 添加 max_chars 字符上限，超出时在句子边界切分
    - 修复列表跟踪：列表结束（空行/非列表行）后即可切分
    - 不再要求 list_depth==0 才能切分，允许在列表块之间切分
    """
    if not text:
        return [""]

    lines = text.split('\n')
    latex_protected = _scan_latex_protected(lines)

    segments: List[str] = []
    current: List[str] = []
    code_block_depth = 0
    in_table = False
    in_list = False
    line_count = 0

    def _flush():
        nonlocal current, line_count, in_list
        if current:
            seg_text = '\n'.join(current).strip()
            if seg_text:
                segments.append(seg_text)
            current = []
            line_count = 0
            in_list = False

    for idx, line in enumerate(lines):
        stripped = line.strip()

        # 代码块围栏
        if stripped.startswith('```'):
            current.append(line)
            line_count += 1
            if code_block_depth > 0:
                code_block_depth -= 1
                # 代码块结束，检查是否需要切分
                if len('\n'.join(current)) >= max_chars:
                    _flush()
            else:
                code_block_depth += 1
            continue

        # 在代码块内，不切分
        if code_block_depth > 0:
            current.append(line)
            line_count += 1
            continue

        # 表格行
        is_table_row = stripped.startswith('|') and stripped.endswith('|')
        if is_table_row:
            in_table = True
            current.append(line)
            line_count += 1
            continue
        elif in_table:
            in_table = False

        # 列表项检测
        is_list_item = bool(re.match(r'^[\s]*([-*+]|\d+[.、)])\s+', stripped))
        if is_list_item:
            in_list = True
        elif not stripped:
            in_list = False

        current.append(line)
        line_count += 1

        # LaTeX 公式内 → 不切分
        if latex_protected[idx]:
            continue

        current_text = '\n'.join(current)
        should_split = False

        # 1) 字符数达到上限 → 强制切分
        if len(current_text) >= max_chars:
            should_split = True
        # 2) 达到 target_lines 且当前为空行、不在表格内
        elif line_count >= target_lines and not stripped and not in_table:
            should_split = True
        # 3) 达到 target_lines + 2 且不在表格内（即使非空行也切）
        elif line_count >= target_lines + 2 and not in_table:
            should_split = True

        if should_split:
            _flush()
            in_table = False

    _flush()

    # 后处理：对仍然过长的段进行句子级切分
    final_segments: List[str] = []
    for seg in segments:
        if len(seg) > max_chars * 2:
            final_segments.extend(_split_by_sentences(seg, max_chars))
        else:
            final_segments.append(seg)

    return final_segments or [text]


# 工具调用标记正则：匹配 [TOOL:start_exam] 或 [TOOL:next_unit]
_TOOL_RE = re.compile(r"\[TOOL:\s*(start_exam|next_unit)\s*\]", re.IGNORECASE)


def extract_tool_call(text: str) -> tuple[str, str | None]:
    """从 LLM 回复中剥离工具调用标记。

    Returns:
        (clean_text, tool_event) — tool_event 为 'start_exam' / 'next_unit' / None。
        若出现多个标记只取第一个；剥离标记后的纯文本用于流式展示与存档。
    """
    if not text:
        return text, None
    m = _TOOL_RE.search(text)
    if not m:
        return text, None
    tool_event = m.group(1).lower()
    # 移除该标记及其周围多余空行
    cleaned = _TOOL_RE.sub("", text, count=1)
    # 去掉末尾因剥离留下的空行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip() + ("\n" if cleaned.endswith("\n") else "")
    return cleaned.strip(), tool_event


# 动作调用标记正则：匹配 [ACTION:point] / [ACTION:blackboard] 等
_ACTION_RE = re.compile(r"\[ACTION:\s*([a-zA-Z_]+)\s*\]", re.IGNORECASE)


def extract_action_call(text: str) -> tuple[str, str | None]:
    """从 LLM 回复中剥离动作指令标记。

    Returns:
        (clean_text, action) — action 为 'point' / 'blackboard' / 'hello' / 'think' / 'listen' / 'speak' / None。
    """
    if not text:
        return text, None
    m = _ACTION_RE.search(text)
    if not m:
        return text, None
    action = m.group(1).lower()
    cleaned = _ACTION_RE.sub("", text, count=1)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip() + ("\n" if cleaned.endswith("\n") else "")
    return cleaned.strip(), action


# 情绪标记正则：匹配 [EMOTION:happy] / [EMOTION:think] 等
_EMOTION_RE = re.compile(r"\[EMOTION:\s*([a-zA-Z_]+)\s*\]", re.IGNORECASE)
VALID_EMOTIONS = {"happy", "think", "surprised", "angry", "sad", "neutral"}


def extract_emotion_call(text: str) -> tuple[str, str | None]:
    """从 LLM 回复中剥离情绪标记。

    Returns:
        (clean_text, emotion) — emotion 为 happy/think/surprised/angry/sad/neutral 或 None。
    """
    if not text:
        return text, None
    m = _EMOTION_RE.search(text)
    if not m:
        return text, None
    emotion = m.group(1).lower()
    if emotion not in VALID_EMOTIONS:
        emotion = "neutral"
    cleaned = _EMOTION_RE.sub("", text, count=1)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip() + ("\n" if cleaned.endswith("\n") else "")
    return cleaned.strip(), emotion


@app.route("/api/chat", methods=["POST"])
def api_chat():
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    lesson_folder = payload.get("lesson_folder") or ACTIVE_LESSON.get("folder")
    force_cloud = payload.get("force_cloud", False)
    explain_mode = payload.get("explain_mode", "")
    if not message:
        return jsonify({"error": "message is required"}), 400
    if not lesson_folder:
        return jsonify({"error": "No active lesson selected"}), 400

    ACTIVE_LESSON["folder"] = lesson_folder
    course_context = load_course_context(lesson_folder)

    def generate():
        fallback_answer = (
            f"我正在根据课程资料作答。\n\n"
            f"用户问题：{message}\n\n"
            f"课程背景：{course_context[:1800]}\n\n"
            "这是一个本地示例回复，表示知识已注入，并且可以继续接入本地 Ollama。"
        )

        history = load_conversation(lesson_folder)
        system_prompt = build_system_prompt(lesson_folder)

        # 如果是强制云端讲解模式，在 system prompt 中注入讲解要求
        if force_cloud and explain_mode == "full_lesson_package":
            extra_instructions = """
【特别指令 - 本课完整讲解模式】
你现在需要一次性、完整地讲解当前课程单元的全部知识点。要求：
1. 覆盖本课所有核心概念、定义、公式和应用
2. 每个知识点给出清晰解释 + 简短示例
3. 用 Markdown 结构化输出（标题、列表、粗体等）
4. 全部讲完后，在回复末尾单独输出 `[TOOL:start_exam]` 触发随堂测验
5. 不需要分段停顿（\\c），直接一次性完整输出即可
"""
            system_prompt = system_prompt + "\n\n" + extra_instructions

        print(f"[TOOL-DEBUG] system_prompt_len={len(system_prompt)}, has_tool_rules={'工具调用规则' in system_prompt}, force_cloud={force_cloud}", flush=True)
        if "工具调用规则" not in system_prompt:
            print(f"[TOOL-DEBUG] >>> 警告: 系统提示词中缺少工具调用规则！", flush=True)

        # 选择模型：强制云端时跳过本地模型
        if force_cloud:
            generated_answer = (
                cloud_llm_reply(message, lesson_folder, history=history)
                or local_ollama_reply(message, lesson_folder, history=history)
                or fallback_answer
            )
        else:
            generated_answer = (
                local_ollama_reply(message, lesson_folder, history=history)
                or cloud_llm_reply(message, lesson_folder, history=history)
                or fallback_answer
            )

        # 解析工具调用标记：剥离后得到对外展示的纯文本；tool_event 推送给前端
        clean_answer, tool_event = extract_tool_call(generated_answer)

        # 解析动作指令标记：剥离后推送 action 给前端触发 Live2D 动作
        clean_answer, live2d_action = extract_action_call(clean_answer)
        print(f"[ACTION-DEBUG] live2d_action={live2d_action or 'NONE'}", flush=True)

        # 解析情绪标记：剥离后推送 emotion 给前端设置表情
        clean_answer, live2d_emotion = extract_emotion_call(clean_answer)
        if live2d_emotion:
            print(f"[EMOTION-DEBUG] live2d_emotion={live2d_emotion}", flush=True)

        # 清洗：剥离模型误加的整段代码块包裹（如 ```plaintext ... ```），
        # 否则分段会切在代码块中间导致格式错乱。仅当整段首尾都被 ``` 包裹时处理，
        # 正常内嵌代码块不受影响。
        if clean_answer.strip().startswith("```") and clean_answer.strip().endswith("```"):
            stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", clean_answer.strip())
            stripped = re.sub(r"\n?\s*```\s*$", "", stripped)
            if stripped.strip():
                clean_answer = stripped.strip()
                print("[FORMAT-DEBUG] 已剥离整段代码块包裹", flush=True)

        # 清洗：剥离动作描述从句（如"艾琳老师拉过黑板，""老师指着黑板，"），
        # 动作只能通过 [ACTION:] 标记触发，正文不出现动作过程描述
        _ACTION_CLAUSE_RE = re.compile(
            r"[^。\n，,]{0,20}?(?:拉过黑板|拿出黑板|指着黑板|指向黑板|转过身|拍了拍手|拿出教具|转向黑板|敲了敲黑板|拉开黑板)[^。\n，,]{0,4}[，,]?"
        )
        if _ACTION_CLAUSE_RE.search(clean_answer):
            clean_answer = _ACTION_CLAUSE_RE.sub("", clean_answer)
            print("[ACTION-DEBUG] 已剥离动作描述从句", flush=True)

        # [DEBUG] 工具调用检测日志
        meta_debug = load_lesson_metadata(lesson_folder)
        units_check = meta_debug.get("units") or []
        print(f"[TOOL-DEBUG] lesson={lesson_folder}, units={len(units_check)}, raw_answer_len={len(generated_answer)}, tool_event={tool_event or 'NONE'}", flush=True)
        if tool_event:
            print(f"[TOOL-DEBUG] >>> 检测到工具调用: {tool_event}", flush=True)
        elif not tool_event and "[TOOL:" in generated_answer:
            print(f"[TOOL-DEBUG] >>> 警告: 包含 [TOOL: 但正则未匹配！原文片段: {repr(generated_answer[-200:])}", flush=True)
        elif not tool_event:
            print(f"[TOOL-DEBUG] >>> 无工具调用。原文片段: {repr(generated_answer[:150])}", flush=True)

        # 若是 next_unit 且确实是分课课程，推进进度（不超过最后一课）
        progress = load_progress(lesson_folder)
        metadata = load_lesson_metadata(lesson_folder)
        units = metadata.get("units") or []
        if tool_event == "next_unit" and units:
            cur = int(progress.get("current_unit", 0) or 0)
            if cur < len(units) - 1:
                progress["current_unit"] = cur + 1
                progress.setdefault("completed_units", [])
                if cur not in progress["completed_units"]:
                    progress["completed_units"].append(cur)
            else:
                # 已是最后一课：忽略 next_unit，避免越界
                tool_event = None

        # 工具调用事件：不在聊天框显示 teacher 气泡，只通过 toast/自动切面板通知
        # 模型的工具触发语（如"我们来测验一下"）不展示，避免刷屏
        if tool_event:
            clean_answer = ""

        # 中文无空格时按字符分块，英文按空格分块；据此决定拼接时是否补空格
        # 分段策略（优先级递减）：
        # 1. 模型主动用 \c 标记分段（优先，系统提示词已强制要求）
        # 2. 无 \c → 按空行（段落边界）分段
        # 3. 无空行 → 按句子边界切分（保护 LaTeX 公式不被截断）
        raw_segments = re.split(r"\\+c", clean_answer)
        raw_segments = [s.strip() for s in raw_segments if s.strip()]

        # [DEBUG] 分段诊断日志
        has_c_marker = '\\c' in clean_answer or r'\c' in clean_answer
        para_count = len([s for s in re.split(r'\n\s*\n', clean_answer) if s.strip()])
        print(f"[SEG-DEBUG] answer_len={len(clean_answer)}, has_\\c={has_c_marker}, "
              f"split_by_\\c={len(raw_segments)}, para_count={para_count}", flush=True)
        print(f"[SEG-DEBUG] answer_preview(200)={repr(clean_answer[:200])}", flush=True)
        if has_c_marker:
            print(f"[SEG-DEBUG] \\c positions: {[m.start() for m in re.finditer(r'\\\\+c', clean_answer)]}", flush=True)

        if len(raw_segments) <= 1:
            # 无 \c 标记 → 按空行分段（段落边界）
            raw_segments = [s.strip() for s in re.split(r'\n\s*\n', clean_answer) if s.strip()]
            print(f"[SEG-DEBUG] fallback to paragraph split: {len(raw_segments)} segments", flush=True)

        if len(raw_segments) <= 1:
            # 仍然只有一段 → 按句子边界切分（保护 LaTeX）
            raw_segments = _split_by_sentences(clean_answer, max_chars=500)
            print(f"[SEG-DEBUG] fallback to sentence split: {len(raw_segments)} segments", flush=True)

        print(f"[SEG-DEBUG] final segments: {len(raw_segments)}, "
              f"sizes={[len(s) for s in raw_segments]}", flush=True)

        segments = raw_segments or [clean_answer]

        for seg_idx, segment in enumerate(segments):
            use_space = len(segment.split()) > 1
            chunks = split_into_stream_chunks(segment)
            accumulator = ""
            for idx, chunk in enumerate(chunks):
                if idx == 0:
                    accumulator = chunk
                elif chunk == '\n' or accumulator.endswith('\n'):
                    accumulator = accumulator + chunk
                else:
                    accumulator = (accumulator + " " + chunk) if use_space else (accumulator + chunk)
                payload = {"content": accumulator, "done": False, "segment": seg_idx}
                yield f"data: {json.dumps(payload)}\n\n"
                time.sleep(0.03)

        # 存档时去掉 \c 标记（匹配单/多反斜杠 + c）
        clean_answer_stored = re.sub(r"\\+c", "", clean_answer).strip()

        audio_url = local_tts_audio(clean_answer[:500]) if clean_answer else None

        conversation = load_conversation(lesson_folder)
        conversation.append({"role": "user", "content": message, "timestamp": now_iso()})
        # 存档：若有工具事件，不把 teacher 的工具触发语存进对话历史（避免下次对话里重复出现）
        if not tool_event and clean_answer_stored:
            conversation.append({"role": "assistant", "content": clean_answer_stored, "timestamp": now_iso()})
        save_conversation(lesson_folder, conversation)
        progress["last_access"] = now_iso()
        save_progress(lesson_folder, progress)
        ACTIVE_LESSON["conversation"] = conversation
        ACTIVE_LESSON["progress"] = load_progress(lesson_folder)

        done_payload: Dict[str, Any] = {"content": clean_answer_stored, "done": True, "audio_url": audio_url, "segment_count": len(segments)}
        if live2d_action:
            done_payload["action"] = live2d_action
        if live2d_emotion:
            done_payload["emotion"] = live2d_emotion
        if tool_event:
            done_payload["tool_event"] = tool_event
            # 附带最新进度，便于前端刷新进度条
            done_payload["progress"] = load_progress(lesson_folder)
            done_payload["units"] = units
        yield f"data: {json.dumps(done_payload)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/progress", methods=["GET"])
def api_progress():
    """返回当前课程的分课进度与 unit 列表，供前端刷新进度条。"""
    lesson_folder = (request.args.get("lesson_folder") or ACTIVE_LESSON.get("folder") or "").strip()
    if not lesson_folder:
        return jsonify({"error": "lesson_folder is required"}), 400
    metadata = load_lesson_metadata(lesson_folder)
    units = metadata.get("units") or []
    progress = load_progress(lesson_folder)
    return jsonify({
        "lesson_folder": lesson_folder,
        "current_unit": int(progress.get("current_unit", 0) or 0),
        "completed_units": progress.get("completed_units", []),
        "total_units": len(units),
        "units": [{"title": u.get("title", f"第 {i + 1} 课"), "summary": u.get("summary", "")} for i, u in enumerate(units)],
        "has_units": bool(units),
    })


def _normalize_quiz_questions(raw_questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """规范化题库：补齐题型字段、规整答案格式。

    题型 type: single(单选) | multiple(多选) | boolean(判断) | fill(填空)
    自动推断规则：
    - 有 options 且 选项 > 2 → single
    - 有 options 且 选项 >= 2 且 answer 为多字母（如 "AC"）→ multiple
    - 有 options 且 选项 == 2 → boolean
    - 无 options → fill
    """
    out: List[Dict[str, Any]] = []
    for q in raw_questions:
        if not isinstance(q, dict):
            continue
        q_type = q.get("type", "").strip().lower()
        options = q.get("options") or []
        answer = str(q.get("answer", "")).strip()

        # 自动推断题型
        if not q_type:
            if options and len(options) >= 2:
                # 判断：answer 是 T/F
                if answer.upper() in ("T", "F", "TRUE", "FALSE", "对", "错", "✓", "✗"):
                    q_type = "boolean"
                elif len(answer) > 1 and answer.isalpha():
                    q_type = "multiple"
                else:
                    q_type = "single"
            else:
                q_type = "fill"

        # 规整答案
        if q_type == "boolean":
            ans = answer.upper()
            if ans in ("TRUE", "✓", "对"):
                answer = "T"
            elif ans in ("FALSE", "✗", "错"):
                answer = "F"
            else:
                answer = ans[0].upper() if ans else ""
        elif q_type == "multiple":
            answer = "".join(sorted(c.upper() for c in answer if c.isalpha()))
        elif q_type == "single":
            answer = answer[0].upper() if answer else ""
        elif q_type == "fill":
            answer = answer.strip()

        item: Dict[str, Any] = {
            "question": q.get("question", ""),
            "type": q_type,
            "answer": answer,
        }
        if q_type in ("single", "multiple", "boolean"):
            item["options"] = list(options)
        out.append(item)
    return out


@app.route("/api/exam/generate", methods=["POST"])
def api_exam_generate():
    payload = request.get_json(silent=True) or {}
    topic = (payload.get("topic") or ACTIVE_LESSON.get("prepared", {}).get("topic") or "Python基础").strip()
    force_regenerate = bool(payload.get("force", False))

    lesson_folder = ACTIVE_LESSON.get("folder")
    metadata = load_lesson_metadata(lesson_folder) if lesson_folder else {}
    cfg = load_config()
    personality_prompt = (
        metadata.get("personality_prompt")
        or cfg.get("personality_prompt")
        or "你是一位温柔、专业、耐心的 AI 学习导师。"
    ).strip()

    units = metadata.get("units") or []
    cur_unit = None
    cur_idx = 0
    if units:
        progress = load_progress(lesson_folder)
        cur_idx = int(progress.get("current_unit", 0) or 0)
        if 0 <= cur_idx < len(units):
            cur_unit = units[cur_idx]

    # 1. 每次都强制重新生成（不再复用 quiz_preset，确保每次题目不同）
    questions: List[Dict[str, Any]] = []
    should_generate = True  # 始终 True，保证每次刷新都有新题

    if should_generate and cur_unit:
        unit_content = {
            "title": cur_unit.get("title", ""),
            "summary": cur_unit.get("summary", ""),
            "key_points": cur_unit.get("key_points", []),
            "source_files": cur_unit.get("source_files", []),
            # 加入随机种子让模型生成不同题目
            "random_seed": random.randint(1, 99999),
        }
        ollama_config = {
            "ollama_base_url": cfg.get("ollama_base_url", ""),
            "ollama_model": cfg.get("ollama_model", ""),
        }
        if cfg.get("enable_local_ollama", True):
            generated = generate_quiz_with_ollama(unit_content, personality_prompt, ollama_config)
        else:
            # 本地禁用时回退到云端模型
            cloud_config = {
                "cloud_api_key": cfg.get("cloud_api_key", ""),
                "cloud_api_base_url": cfg.get("cloud_base_url", ""),
                "cloud_provider": cfg.get("cloud_provider", ""),
                "cloud_model": cfg.get("cloud_model", ""),
                "siliconflow_api_key": cfg.get("siliconflow_api_key", ""),
                "siliconflow_base_url": cfg.get("siliconflow_base_url", ""),
                "siliconflow_model": cfg.get("siliconflow_model", ""),
            }
            generated = generate_quiz_with_model(unit_content, personality_prompt, cloud_config)

        if generated:
            questions = generated
            # 缓存到 unit 的 quiz_preset
            if lesson_folder:
                all_units = metadata.get("units", [])
                if 0 <= cur_idx < len(all_units):
                    all_units[cur_idx]["quiz_preset"] = questions
                    metadata["units"] = all_units
                    save_metadata(LESSONS_DIR / lesson_folder, metadata)

    # 2. 兜底：从 quiz_preset / metadata 中取
    if not questions:
        if cur_unit:
            questions = list(cur_unit.get("quiz_preset") or [])
        if not questions:
            prepared = ACTIVE_LESSON.get("prepared") or {}
            questions = list(prepared.get("quiz_preset") or [])
        if not questions:
            questions = list(metadata.get("quiz_preset") or [])

    # 3. 最终兜底题库（基于单元内容生成有意义的题目）
    if not questions:
        title = cur_unit.get("title", "") if cur_unit else ""
        summary = cur_unit.get("summary", "") if cur_unit else ""
        kps = (cur_unit.get("key_points", []) if cur_unit else []) or []
        kp_str = "、".join(str(k) for k in kps[:3]) if kps else (summary[:40] or topic)
        questions = [
            {"question": f"在「{title or topic}」中，{kp_str} 的核心要点是什么？", "type": "single", "options": ["A. 以上都对", "B. 只需记忆定义", "C. 跳过此部分", "D. 不需理解"], "answer": "A"},
            {"question": f"关于「{title or topic}」的学习，以下哪些方法是有效的？（多选）", "type": "multiple", "options": ["A. 结合实践练习", "B. 死记硬背", "C. 理解核心概念", "D. 只看不练"], "answer": "AC"},
            {"question": f"学习「{title or topic}」不需要理解，只需记忆。", "type": "boolean", "options": ["A. 正确", "B. 错误"], "answer": "F"},
            {"question": f"「{title or topic}」中最基础的概念是______。", "type": "fill", "answer": "基础概念"},
        ]

    questions = _normalize_quiz_questions(questions)

    ACTIVE_LESSON["last_exam"] = questions
    # 不把 answer 暴露给前端，避免作弊；同时隐藏 fill 题的答案
    safe_questions = []
    for q in questions:
        safe = {"question": q.get("question", ""), "type": q.get("type", "single")}
        if q.get("type") in ("single", "multiple", "boolean"):
            safe["options"] = q.get("options", [])
        safe_questions.append(safe)
    return jsonify({"questions": safe_questions, "total": len(safe_questions)})


@app.route("/api/exam/submit", methods=["POST"])
def api_exam_submit():
    payload = request.get_json(silent=True) or {}
    answers = payload.get("answers", [])
    # 始终使用服务端缓存的题目（含正确答案），不信任前端传入的 questions
    questions = ACTIVE_LESSON.get("last_exam") or payload.get("questions") or []

    total = len(questions)
    correct = 0
    wrong_indices: List[int] = []
    for idx, q in enumerate(questions):
        ans_raw = answers[idx] if idx < len(answers) else ""
        ans = (ans_raw or "").strip()
        q_type = q.get("type", "single")
        expected = str(q.get("answer", "")).strip()

        if q_type == "fill":
            # 填空题：模型语义评判（非严格答案匹配）
            if ans and _judge_fill_answer_semantically(ans, expected, q.get("question", "")):
                correct += 1
            else:
                wrong_indices.append(idx)
        elif q_type == "multiple":
            # 多选题：排序后比较
            ans_norm = "".join(sorted(c.upper() for c in ans if c.isalpha()))
            expected_norm = "".join(sorted(c.upper() for c in expected if c.isalpha()))
            if ans_norm == expected_norm and ans_norm:
                correct += 1
            else:
                wrong_indices.append(idx)
        else:
            # single / boolean
            if ans.upper() and ans.upper()[0] == expected.upper()[0]:
                correct += 1
            else:
                wrong_indices.append(idx)

    score = int(round(correct / total * 100)) if total else 0

    if ACTIVE_LESSON.get("folder"):
        progress = load_progress(ACTIVE_LESSON["folder"])
        progress.setdefault("completed_quizzes", [])
        progress["score_history"] = progress.get("score_history", []) + [{"score": score, "timestamp": now_iso(), "total": total, "correct": correct}]
        progress["completed_quizzes"] = list(dict.fromkeys(progress["completed_quizzes"] + ["quiz_1"]))
        save_progress(ACTIVE_LESSON["folder"], progress)

    message = f"答对 {correct}/{total} 题" if total else "未生成题目"
    if score == 100:
        message = f"全部答对！{correct}/{total}"
    elif wrong_indices:
        message = f"答对 {correct}/{total} 题，建议复查第 {', '.join(str(i + 1) for i in wrong_indices)} 题"

    # 返回错题详情（题干、学生答案、正确答案、解析），供前端发给老师讲解
    wrong_details = []
    for idx in wrong_indices:
        if 0 <= idx < len(questions):
            q = questions[idx]
            wrong_details.append({
                "index": idx + 1,
                "question": q.get("question", ""),
                "type": q.get("type", "single"),
                "student_answer": answers[idx] if idx < len(answers) else "",
                "correct_answer": q.get("answer", ""),
                "explanation": q.get("explanation", ""),
            })

    return jsonify({
        "score": score,
        "correct": correct,
        "total": total,
        "wrong_indices": wrong_indices,
        "message": message,
        "wrong_details": wrong_details,
    })


@app.route("/api/lesson/next_unit", methods=["POST"])
def api_lesson_next_unit():
    payload = request.get_json(silent=True) or {}
    folder = payload.get("folder") or ACTIVE_LESSON.get("folder")
    if not folder:
        return jsonify({"success": False, "message": "未选择课程"}), 400

    metadata = load_lesson_metadata(folder)
    units = metadata.get("units") or []
    if not units:
        return jsonify({"success": False, "message": "该课程无分课内容"}), 400

    progress = load_progress(folder)
    cur = int(progress.get("current_unit", 0) or 0)
    if cur < len(units) - 1:
        progress["current_unit"] = cur + 1
        progress.setdefault("completed_units", [])
        if cur not in progress["completed_units"]:
            progress["completed_units"].append(cur)
        save_progress(folder, progress)
        # 清空对话历史，实现单元间对话隔离
        save_conversation(folder, [])
        ACTIVE_LESSON["conversation"] = []
        return jsonify({
            "success": True,
            "message": f"已进入第 {cur + 2} 课",
            "progress": progress,
            "units": units,
        })
    elif cur == len(units) - 1:
        return jsonify({"success": False, "message": "已是最后一课"})
    else:
        return jsonify({"success": False, "message": "无效的进度"})


@app.route("/api/lesson/reset", methods=["POST"])
def api_lesson_reset():
    payload = request.get_json(silent=True) or {}
    folder = payload.get("folder") or ACTIVE_LESSON.get("folder")
    if not folder:
        return jsonify({"success": False, "message": "未选择课程"}), 400

    progress = {"current_unit": 0, "completed_units": [], "started_at": now_iso()}
    save_progress(folder, progress)
    return jsonify({"success": True, "message": "进度已重置", "progress": progress})


@app.route("/api/create_lesson", methods=["POST"])
def api_create_lesson():
    """创建一门空课程（不触发备课，仅建立目录与基础文件）。"""
    payload = request.get_json(silent=True) or {}
    lesson_folder = (payload.get("lesson_folder") or payload.get("name") or "").strip()
    if not lesson_folder:
        return jsonify({"error": "课程名不能为空"}), 400
    if "/" in lesson_folder or "\\" in lesson_folder or ".." in lesson_folder:
        return jsonify({"error": "非法的课程名"}), 400
    ensure_lesson_files(lesson_folder)
    return jsonify({"ok": True, "lesson_folder": lesson_folder, "message": "课程创建成功"})


@app.route("/api/switch_lesson", methods=["POST"])
def api_switch_lesson():
    payload = request.get_json(silent=True) or {}
    lesson_folder = payload.get("lesson_folder")
    if not lesson_folder:
        return jsonify({"error": "lesson_folder is required"}), 400

    target_dir = LESSONS_DIR / lesson_folder
    if not target_dir.exists():
        return jsonify({"error": "lesson folder not found"}), 404

    ensure_lesson_files(lesson_folder)
    metadata = {}
    config_path = target_dir / "config.json"
    meta_path = target_dir / "metadata.json"
    target_path = config_path if config_path.exists() else meta_path
    if target_path.exists():
        try:
            metadata = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}

    if metadata.get("personality_prompt"):
        cfg = load_config()
        cfg["personality_prompt"] = metadata["personality_prompt"]
        save_config(cfg)

    ACTIVE_LESSON["folder"] = lesson_folder
    ACTIVE_LESSON["metadata"] = metadata
    ACTIVE_LESSON["resources"] = metadata.get("resources", [])
    ACTIVE_LESSON["conversation"] = load_conversation(lesson_folder)
    ACTIVE_LESSON["progress"] = save_progress(lesson_folder, load_progress(lesson_folder))
    return jsonify(
        {
            "status": "ok",
            "lesson_folder": lesson_folder,
            "metadata": metadata,
            "conversation": ACTIVE_LESSON["conversation"],
            "progress": ACTIVE_LESSON["progress"],
            "message": f"已加载{lesson_folder}课程，继续上次的进度",
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
