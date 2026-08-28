import ast
import base64
import io
import json
import logging
import os
import random
import secrets
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    ALLOWED_EXTENSIONS,
    convert_document_to_markdown,
    download_resource,
    ensure_lesson_dir,
    extract_pdf_toc,
    load_course_context,
    load_unit_context,
    pdf_ocr_available,
    save_metadata,
    sanitize_topic,
    set_ocr_enabled,
    unit_dir,
    _read_pdf_text,
)
from lesson_prep import (
    _fallback_quiz,
    _is_meaningful_question,
    _normalize_unit,
    _robust_json_loads,
    _strip_thinking_lead,
    generate_knowledge_graph,
    prepare_lesson,
    generate_quiz_with_model,
    generate_quiz_with_ollama,
)
# V4 自适应教学模块
from learner_model import (
    add_assessment_record,
    add_error_record,
    get_learner as lm_get_learner,
    save_learner as lm_save_learner,
    update_knowledge_state as lm_update_knowledge_state,
)
from knowledge_graph import save_knowledge_graph as kg_save_graph
from learning_evaluation import generate_insights as eval_insights
from learning_evaluation import get_gain_report as eval_gain_report
from learning_evaluation import get_progress_curve as eval_progress_curve
from pedagogical_engine import record_answer_result as ped_record_answer
from pedagogical_engine import run_diagnosis as ped_diagnosis
from pedagogical_engine import plan_next_lesson as ped_plan_next

app = Flask(__name__)

# 开发期改模板（index.html）即时生效：每次请求检查模板 mtime（一次 stat，开销可忽略）
app.config["TEMPLATES_AUTO_RELOAD"] = True

# 开启 GZip/Brotli 响应压缩（pip install flask-compress；未安装则静默跳过）
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    print("[compress] flask-compress 未安装，跳过压缩优化", flush=True)

@app.after_request
def no_cache_headers(response):
    # 仅对 API 响应禁用缓存（保证数据实时性）；
    # /static/* 等静态资源走浏览器长缓存，避免每次刷新重复下载 pl2d.js 等大文件。
    # 注意必须直接赋值（而非 setdefault）：Flask 静态文件默认会带 Cache-Control: no-cache。
    if request.path.startswith("/api/"):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    else:
        response.headers['Cache-Control'] = 'public, max-age=604800'
    return response


# ============================
# 全局请求/错误日志
# ============================
# 日志脱敏：把 body 中的敏感字段值替换为 ***，避免明文密钥写入日志
# 覆盖典型命名（兼容 snake_case / camelCase）+ 任何"看似密钥"的长串值
_SENSITIVE_KEYS = (
    "api_key", "apikey", "api-key",
    "token", "access_token", "refresh_token", "bearer",
    "secret", "client_secret",
    "password", "passwd",
    "private_key",
)
# 密钥/令牌值脱敏正则：
# ① 常见前缀（sk-/pk-/gho_/github_pat_/xox*/AIza…）+ 后续任意字符（含 . / _ / -）
# ② JWT（eyJ... 三段式）
# ③ 通用长随机 token：≥40 位且同时含字母和数字（无前缀的 key / 哈希；不误伤纯英文单词）
# 注意：脱敏宁多勿少——即使误伤普通长字符串，也只是日志显示，不泄露信息。
_TOKEN_VALUE_RE = re.compile(
    r'(?i)'
    r'(?:\b(?:sk-|pk-|gho_|ghp_|github_pat_|xox[abp]-|AIza[0-9A-Za-z_\-]{20,})[A-Za-z0-9_\-\.]+)'
    r'|(?:\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})'
    r'|(?:\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{40,}\b)'
)

def _redact_body(raw: str) -> str:
    """对请求 body 字符串做敏感字段脱敏。原始 body 可能不是合法 JSON，做 best-effort。"""
    if not raw:
        return raw
    try:
        # 优先按 JSON 处理（结构化精确）
        obj = json.loads(raw)
        def walk(node):
            if isinstance(node, dict):
                return {k: ("***" if k.lower().replace("-", "_") in _SENSITIVE_KEYS else walk(v)) for k, v in node.items()}
            if isinstance(node, list):
                return [walk(x) for x in node]
            if isinstance(node, str):
                # 额外兜底：值本身就是 sk-xxx 形态的密钥字符串
                return _TOKEN_VALUE_RE.sub("***", node)
            return node
        return json.dumps(walk(obj), ensure_ascii=False)
    except (ValueError, json.JSONDecodeError):
        # 非 JSON（form / text）：用正则替换 key=value / "key":"value"
        redacted = raw
        for k in _SENSITIVE_KEYS:
            pattern = (
                r'(["\']?' + re.escape(k) + r'["\']?\s*[:=]\s*)'
                r'(["\'][^"\n]*["\']|[^,&\n\s]+)'
            )
            redacted = re.sub(pattern, r'\1"***"', redacted, flags=re.IGNORECASE)
        redacted = _TOKEN_VALUE_RE.sub("***", redacted)
        return redacted


@app.before_request
def _log_request():
    """每个 HTTP 请求进来时记一条（POST/PUT/DELETE 也带上 body 摘要）。"""
    try:
        method = request.method
        path = request.path
        # 只记 API + 静态资源忽略
        if not path.startswith("/api/"):
            return
        # 不记 SSE 长连接（会刷屏）
        if path.startswith("/api/chat"):
            app.logger.debug(f"📥 {method} {path}")
            return
        body_summary = ""
        if method in ("POST", "PUT", "DELETE", "PATCH"):
            try:
                raw = request.get_data(cache=True, as_text=True)[:500]
                if raw:
                    # 安全：日志中的 body 必须过滤敏感字段（API key / token / secret 等）
                    # 防止明文密钥写入 logs/app.log 后被备份/同步外泄
                    body_summary = f"  body={_redact_body(raw)}"
            except Exception:
                pass
        app.logger.info(f"📥 {method} {path}{body_summary}")
    except Exception as exc:
        app.logger.warning(f"_log_request failed: {exc}")


@app.after_request
def _log_response(response):
    """每个 HTTP 请求返回时记一条状态码。"""
    try:
        path = request.path
        if not path.startswith("/api/"):
            return response
        if path.startswith("/api/chat"):
            return response
        status = response.status_code
        if status >= 500:
            app.logger.error(f"📤 {status} {request.method} {path}")
        elif status >= 400:
            app.logger.warning(f"📤 {status} {request.method} {path}")
        else:
            app.logger.info(f"📤 {status} {request.method} {path}")
    except Exception:
        pass
    return response


@app.errorhandler(Exception)
def _handle_exception(exc):
    """捕获所有未处理异常，记录堆栈 + 返回 500。"""
    tb = traceback.format_exc()
    app.logger.error(f"❌ 未处理异常 ({request.method} {request.path}): {exc}")
    app.logger.error(tb)
    # 返回 JSON
    if request.path.startswith("/api/"):
        return jsonify({"error": f"{type(exc).__name__}: {exc}", "traceback": tb.splitlines()[-3:]}), 500
    # HTML 路由返回简单错误页
    return Response(f"<h1>500</h1><pre>{tb}</pre>", status=500, mimetype="text/html")

BASE_DIR = Path(__file__).resolve().parent
LESSONS_DIR = BASE_DIR / "lessons"
LESSONS_DIR.mkdir(exist_ok=True)
CONFIG_PATH = BASE_DIR / "config.json"

# ============================
# 结构化日志（输出到 stdout + 文件）
# ============================
LOG_DIR = BASE_DIR.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"

# 自定义 formatter：带颜色 + 时间戳 + 模块 + 行号
class _ColorFormatter(logging.Formatter):
    COLORS = {
        logging.DEBUG:    "\033[90m",   # 灰
        logging.INFO:     "\033[36m",   # 青
        logging.WARNING:  "\033[33m",   # 黄
        logging.ERROR:    "\033[31m",   # 红
        logging.CRITICAL: "\033[1;31m", # 粗红
    }
    RESET = "\033[0m"
    def format(self, record):
        color = self.COLORS.get(record.levelno, "")
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]
        msg = super().format(record)
        return f"{color}[{ts}]{self.RESET} {msg}"

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(_ColorFormatter("%(levelname)-7s %(name)s:%(lineno)d  %(message)s"))

_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d  %(message)s"
))

app.logger.handlers.clear()
app.logger.addHandler(_console_handler)
app.logger.addHandler(_file_handler)
app.logger.setLevel(logging.DEBUG)

logging.getLogger("werkzeug").handlers.clear()
logging.getLogger("werkzeug").addHandler(_console_handler)
logging.getLogger("werkzeug").addHandler(_file_handler)
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# 子模块（lesson_prep 等）的 logger 也接入同一控制台 + 日志文件
for _sub_name in ("lesson_prep", "file_utils"):
    _sub_logger = logging.getLogger(_sub_name)
    _sub_logger.handlers.clear()
    _sub_logger.addHandler(_console_handler)
    _sub_logger.addHandler(_file_handler)
    _sub_logger.setLevel(logging.DEBUG)
    _sub_logger.propagate = False

app.logger.info("=" * 60)
app.logger.info("🚀 My Teacher app started")
app.logger.info(f"📝 日志文件: {LOG_FILE}")
app.logger.info(f"📂 课程目录: {LESSONS_DIR}")
app.logger.info("=" * 60)

ACTIVE_LESSON = {
    "folder": None,
    "metadata": {},
    "resources": [],
    "prepared": {},
    "conversation": [],
    "progress": {},
    "preview_plan": None,   # 备课预览时的临时数据（用户确认后才写入磁盘）
    "preview_topic": None,  # 预览对应的课程主题
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _now_display() -> str:
    """人类可读的当前时间，注入系统提示词让模型感知"此刻"。

    用「星期X」中文写法而非「周3」，避免 4B 小模型把数字序号误读成错误的星期。
    """
    weekday_names = "一二三四五六日"
    wd = weekday_names[datetime.now().weekday()]
    return datetime.now().strftime(f"%Y年%m月%d日 %H:%M（星期{wd}）")


def default_progress() -> Dict[str, Any]:
    return {
        "last_access": "",
        "completed_quizzes": [],
        "score_history": [],
        "code_attempts": 0,
        # 分课进度：当前上到第几课（0-based），已完成的 unit 索引列表
        "current_unit": 0,
        "completed_units": [],
        # 已做过"开场讲解"的 unit 索引列表（首次进入单元时 AI 主动讲解，只讲一次）
        "welcomed_units": [],
    }


def default_config() -> Dict[str, Any]:
    return {
        # 模拟"用户刚 clone 下来"的初始状态：本地服务 URL 保留，其余用户配置字段全部清空。
        "ollama_base_url": "http://127.0.0.1:11434",  # 本地服务，无需配置
        "ollama_model": "qwen3:8b",                   # 内置默认模型（用户可改成本地已安装的其它模型）
        "ollama_num_ctx": 16384,
        "ollama_temperature": 0.7,
        "ollama_num_predict": 8192,
        "tts_base_url": "http://127.0.0.1:8000",       # 本地服务，无需配置
        "tts_voice": "",                              # 用户填自己装的 TTS 服务音色
        "tts_enabled": False,
        "tts_provider": "local",
        # 云端 TTS（硅基流动 FunAudioLLM/CosyVoice2-0.5B）
        "tts_cloud_base_url": "",
        "tts_cloud_voice": "",
        "tts_cloud_model": "FunAudioLLM/CosyVoice2-0.5B",
        "tts_cloud_response_format": "mp3",
        "enable_local_ollama": False,                 # 默认关闭，让用户主动开启
        "siliconflow_api_key": "",
        "siliconflow_model": "deepseek-ai/DeepSeek-V3",  # 内置默认（硅基流动），用户可改
        # 云端模型 - 备课用
        "cloud_provider": "",                         # 用户填（siliconflow / openai / ...）
        "cloud_base_url": "",
        "cloud_api_key": "",
        "cloud_model": "deepseek-ai/DeepSeek-V3",
        "enable_search": False,
        # 云端模型 - 对话聊天用（独立配置；未填写时回退到 cloud_*）
        "chat_provider": "auto",
        "chat_base_url": "",
        "chat_api_key": "",
        "chat_model": "deepseek-ai/DeepSeek-V3",
        "chat_enable_search": False,
        # 备课模型提供方：auto（云端优先，失败回退本地）/ cloud / ollama
        "lesson_provider": "auto",
        # 识图模型（可选）
        "vision_enabled": False,
        "vision_base_url": "",
        "vision_api_key": "",
        "vision_model": "",
        # PDF OCR（扫描版教材文字识别，本地 easyocr 可用时自动生效）
        "ocr_enabled": True,
        "ocr_engine": "easyocr",
        "ocr_langs": "ch_sim,en",
        # 实验功能：交互式备课——开始备课时先弹一个多轮诊断问答，了解学生掌握度
        # 再生成针对性教案。需要后端 /api/prep_diagnose 支持。
        "interactive_prep_enabled": False,
        "auto_play_tts": False,
        "voice_enabled": False,
        "assistant_name": "AI 老师",                  # 用户可改成自己想要的老师名
        # 内置人设 prompt —— 用户可自由改写
        "personality_prompt": "你是一位温柔、专业、耐心的 AI 学习导师。请以启发式提问方式指导学生：先解释概念，再给出生活化的例子，最后用一两个小问题确认理解。遇到学生答错时不要直接给答案，而是再换一种方式重讲一遍。保持亲切的口吻，称呼学生『你』，并适度使用 emoji 让对话更生动。\n\n【联想相似课程概念】讲到新概念时，如果学生可能已学过类似/对偶/对比的概念（比如 C++ 的 cout<< 与 Python 的 print()、Java 的 try-catch 与 Python 的 try-except），可以自然地用一句'这就像你之前学过的 X'做类比，让学生更易记忆；类比不要太频繁（每节课最多 1~2 次），且只在相似性高的地方用，不要硬凑。",
        # 备课（分课教案生成）system 提示词 —— 用户可在设置面板修改；下面是 v3 内置默认
        # （阶段一备课思考链 4 阶段升级版：课程级目标 + 每单元 3 维度设计 + 易混淆概念对比）
        "lesson_prompt": (
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
            "      \"formula\": \"标准公式（如 S = a × b；若为概念则写定义表达式）\",\n"
            "      \"variables\": \"变量说明（如 a: 长, b: 宽, S: 面积）\" }\n"
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
            '      "prerequisites": ["第N-1 课（标题）"],\n'
            '      "core_formulas": [\n'
            '        {"name": "公式名", "formula": "标准写法", "variables": "变量说明"}\n'
            '      ],\n'
            '      "gateway_questions": ["问题1", "问题2", "问题3"],\n'
            '      "contribution_to_target": "本课对 course_target 的具体贡献",\n'
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
            '        {"title": "资源标题", "url": "链接", "type": "pdf|docx|webpage|video", "platform": "video 时填 bilibili 或 netease_open_course", "description": "简短说明", "markdown_content": "可选 Markdown 正文"}\n'
            '      ]\n'
            '    }\n'
            '  ],\n'
            '  "resources": ["全局备用资源（可选，结构与 source_files 一致）"]\n'
            "}\n\n"
            "硬性要求：\n"
            "1. units 至少 8 课，最多 16 课；total_lessons 必须等于 units 数组长度。\n"
            "2. 每个 unit 的 modules 至少 3 个、最多 6 个；module 数量足够把 target 拆解到位。\n"
            "3. 每个 module 的 concept/example/anchor/interaction/action 五个字段都必须有内容，禁止空字符串或省略。\n"
            "4. 每个 unit 必须填：core_formulas（1~3 项）、gateway_questions（2~3 题）、contribution_to_target（1~2 句）、prerequisites（依赖前置课时，没有给 []）。\n"
            "5. 每个 unit 的 key_points 至少 4 个，source_files 至少 1 个真实可访问的资源链接（PDF/官方文档/网页），type 必须是 pdf/docx/webpage/video 之一；video 链接必须是 bilibili（B站，https://www.bilibili.com/video/BV...）或 netease_open_course（网易公开课，https://open.163.com/...）之一。\n"
            "6. markdown_content 字段若资源是公开网页/文本，请直接给出关键段落 Markdown 正文（不超过 2000 字）。\n"
            "7. syllabus 字段需用 ### 第N课：标题 的形式列出全部课时。\n"
            "8. key_points（全局）至少 5 个核心概念；resources（全局）至少 3 个高质量学习资源链接。\n"
            "9. 若用户随主题提供了课程资料文档（Markdown），你必须基于该文档内容拆分单元、提炼 modules 的 concept/example，不要脱离文档凭空编造课程内容。\n"
            "10. 严禁在 JSON 中输出「思考过程」「分析」等元文本；只输出最终结构化教案。\n"
            "11. 本课若有常见的易混淆概念对（如变量vs常量、赋值vs比较、整除vs除法等），必须填入 unit 的 contrasts 字段；本课没有易混淆概念时，contrasts 填空数组 []。\n"
            "12. course_target 必须与各 unit 的 contribution_to_target 语义连贯：每个 unit 的贡献相加，应能支撑 course_target 的达成。\n"
        ),
        "default_topic": "",
        "default_voice": "",
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
        # 云端 TTS 独立开关（与 tts_enabled 分离）：tts_provider=cloud 时这个才生效
        "tts_cloud_enabled": False,
        # 全局正文字号（px），应用在 .chat-sidebar 与 .menu-screen 上；范围 12-20，默认 14
        "font_size": 14,
        # 情绪映射（Open-LLM-VTuber 协议）：emotion 名 → 模型 expression index
        # 在前端设置面板可调整；留空则用默认映射
        "emotion_map": {},
        # 回复分段（Galgame 逐段展示）：
        # segment_marker 为纯文本分段符，模型在回复中用它把内容切成多段；
        # 它【不是】Markdown 代码块语言标记，提示词中已强制要求模型不要误用。
        "segment_enabled": False,
        "segment_marker": "\\c",
        "segment_max_lines": 6,
        # 右侧聊天侧栏宽度（百分比 25-60，CSS 用 flex-basis 应用）
        "sidebar_width": 36,
        # 自定义 Live2D 模型（上传后更新此 URL；空则用内置默认模型）
        "live2d_model_url": "/static/models/my_teacher/female_01Arkit_6.model3.json",
        # 自定义动作关键字：{ "关键字": "动作名" }，前端会合并进动作映射。
        # 动作名可以是模型动作组（speak/think/listen/idle/hello）或内置自定义动作（wave）。
        "custom_actions": {},
    }


def load_config() -> Dict[str, Any]:
    # 记录磁盘上实际存在的字段名（用于旧字段名迁移判断，见 _env_override）
    disk_keys: set = set()
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
            disk_keys = set(data.keys())

    # 环境变量覆盖（优先级高于 config.json）
    _env_override(cfg, disk_keys)
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


def _env_override(cfg: Dict[str, Any], disk_keys: Optional[set] = None) -> None:
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
    # siliconflow_api_key 别名兼容旧配置：
    # 仅当磁盘上根本没有 cloud_api_key 字段（旧版 config.json 只有 siliconflow_api_key）时，
    # 才从 siliconflow_api_key 迁移到 cloud_api_key（反之亦然）。
    # 注意：不能用 "cfg.get(x) 为空" 判断 —— 用户显式把 key 清空成 "" 时，
    # 该字段在磁盘上已存在，不应再从旧字段回填，否则用户永远无法真正清除 key。
    if disk_keys is not None:
        if "cloud_api_key" not in disk_keys and cfg.get("siliconflow_api_key"):
            cfg["cloud_api_key"] = cfg["siliconflow_api_key"]
        if "siliconflow_api_key" not in disk_keys and cfg.get("cloud_api_key"):
            cfg["siliconflow_api_key"] = cfg["cloud_api_key"]


def save_config(data: Dict[str, Any]) -> Dict[str, Any]:
    """合并保存配置：在现有配置基础上覆盖传入字段，不丢失已有配置。"""
    current = load_config()
    current.update(data or {})
    _atomic_write_json(CONFIG_PATH, current)
    return current


def build_lesson_folder_name(topic: str) -> str:
    """生成唯一课程目录名 = 日期_主题_时间戳_随机。

    即使主题完全相同，每次创建也会得到不同的目录名（毫秒级 + 随机后缀），
    避免同名课程相互覆盖。返回的目录名也直接作为 lesson_id 使用。
    """
    slug = sanitize_topic(topic)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rand4 = "".join(random.choices("0123456789abcdef", k=4))
    return f"{stamp}_{slug}_{rand4}"


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
                    # 课程唯一 ID = 目录名本身（每次 build_lesson_folder_name 都带毫秒级时间戳+随机后缀，已保证唯一）
                    "lesson_id": lesson_folder,
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
                    "lesson_id": lesson_folder,
                    "course_name": lesson_folder,
                    "avatar_url": "",
                    "bg_theme": "warm",
                    "bg_url": "",
                }, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            config_path.write_text(json.dumps({
                "lesson_id": lesson_folder,
                "course_name": lesson_folder,
                "avatar_url": "",
                "bg_theme": "warm",
                "bg_url": "",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        # 数据迁移：旧 config 缺 lesson_id 时补上（用目录名兜底）
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            if not cfg.get("lesson_id"):
                cfg["lesson_id"] = lesson_folder
                config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
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
    _atomic_write_json(lesson_path(lesson_folder, "conversation.json"), conversation)


def _archive_conversation(lesson_folder: str, unit_index: int, conversation: List[Dict[str, str]]) -> None:
    """把指定单元的对话归档到 history/unit_<N>.json，供历史回顾与导出。"""
    if not lesson_folder or not conversation:
        return
    history_dir = LESSONS_DIR / lesson_folder / "history"
    try:
        history_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(history_dir / f"unit_{unit_index}.json", conversation)
    except Exception as exc:
        print(f"[history] 归档第 {unit_index + 1} 单元对话失败: {exc}", flush=True)


def _load_archived_conversations(lesson_folder: str) -> Dict[str, List[Dict[str, str]]]:
    """读取该课程全部已归档单元对话：{unit_index_str: [messages]}。"""
    out: Dict[str, List[Dict[str, str]]] = {}
    history_dir = LESSONS_DIR / lesson_folder / "history"
    if not history_dir.exists():
        return out
    for p in sorted(history_dir.glob("unit_*.json"), key=lambda x: x.name):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                out[p.stem] = data
        except Exception:
            continue
    return out


def _conversation_to_markdown(conversation: List[Dict[str, str]]) -> str:
    """把对话记录转成 Markdown 文本（供导出）。"""
    lines: List[str] = []
    for m in conversation:
        role = m.get("role")
        content = str(m.get("content") or "").strip()
        if not content:
            continue
        if content.startswith("[终端执行记录]"):
            continue
        ts = m.get("timestamp", "")
        ts_str = f"（{ts}）" if ts else ""
        if role == "user":
            lines.append(f"## 👤 学生{ts_str}\n\n{content}\n")
        elif role == "assistant":
            lines.append(f"## 👩‍🏫 老师{ts_str}\n\n{content}\n")
    return "\n".join(lines)


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
    with _META_IO_LOCK:
        ensure_lesson_files(lesson_folder)
        merged = {**default_progress(), **load_progress(lesson_folder), **progress_data}
        merged["last_access"] = now_iso()
        _atomic_write_json(lesson_path(lesson_folder, "progress.json"), merged)
    return merged


# ============================
# 数据迁移 / 元数据读写的并发保护
# ============================
# Flask 默认多线程处理请求，load_lesson_metadata 会在读取时"读-改-写"迁移旧数据
# （剥 unit 标题前缀 / 派生 syllabus.json），并发请求同时读写同一文件会互相覆盖。
# 用进程级锁串行化"读-改-写"临界区，并用临时文件 + os.replace 原子落盘。
_META_IO_LOCK = threading.Lock()


def _atomic_write_json(path: Path, data: Any) -> None:
    """原子写 JSON：先写临时文件再 os.replace，避免并发半写/覆盖。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


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
    # 读-改-写 全程加锁 + 原子落盘，防止并发请求互相覆盖。
    with _META_IO_LOCK:
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}

        # 数据迁移：剥掉 unit.title 里硬编码的「第N课：xxx」等序号前缀（AI / 用户手动留下），
        # 否则 AI 会把「第 N 课」当作当前课序号，导致开场白与单元名错位。迁移完成后写回磁盘。
        try:
            units = data.get("units")
            if isinstance(units, list):
                changed = False
                for u in units:
                    if isinstance(u, dict) and isinstance(u.get("title"), str):
                        new_title = _strip_unit_prefix_for_metadata(u["title"])
                        if new_title != u["title"]:
                            u["title"] = new_title
                            changed = True
                if changed:
                    _atomic_write_json(target, data)
        except Exception as exc:
            print(f"[lesson_meta] 迁移 units 标题前缀失败: {exc}", flush=True)

        # 数据迁移：缺 syllabus.json 时，根据 units[].target/modules 自动派生一份。
        # 旧课程只有 key_points 也能落出可用的"教案骨架"，保证阶段二讲课有材料可循。
        try:
            lesson_dir = LESSONS_DIR / lesson_folder
            syllabus_path = lesson_dir / "syllabus.json"
            if not syllabus_path.exists():
                payload = _build_syllabus_payload(data, data.get("units") or [])
                _atomic_write_json(syllabus_path, payload)
        except Exception as exc:
            print(f"[lesson_meta] 派生 syllabus.json 失败: {exc}", flush=True)
    return data if isinstance(data, dict) else {}


def _build_syllabus_payload(plan: Dict[str, Any], units: List[Dict[str, Any]]) -> Dict[str, Any]:
    """把备课产物抽成"教案骨架"用于讲课阶段：topic + 顶层 target + units[].target/modules。

    兼容旧数据：units[i] 缺 target/modules 时从 key_points 派生。
    """
    units_out: List[Dict[str, Any]] = []
    for idx, u in enumerate(units or []):
        if not isinstance(u, dict):
            continue
        title = str(u.get("title") or f"第 {idx + 1} 课").strip()
        summary = str(u.get("summary") or "").strip()
        key_points = u.get("key_points") or []
        if not isinstance(key_points, list):
            key_points = []
        key_points = [str(k).strip() for k in key_points if str(k).strip()]
        target = str(u.get("target") or summary or f"理解并应用「{title}」的核心概念").strip()
        raw_modules = u.get("modules")
        modules: List[Dict[str, Any]] = []
        if isinstance(raw_modules, list) and raw_modules:
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
        if not modules:
            # 兜底：用 key_points 派生最简 module 骨架
            kp_src = key_points or [summary or title]
            for mi, kp in enumerate(kp_src[:5]):
                modules.append({
                    "id": f"M{mi + 1}",
                    "title": f"理解「{kp}」",
                    "concept": kp,
                    "example": "用一个贴近生活的类比或最小例子说明。",
                    "anchor": f"一句话：{kp} 是本课的关键要点之一。",
                    "interaction": "提问：你对这个概念熟悉吗？",
                    "action": "指向黑板",
                })
        # 易混淆概念对比：透传备课生成的 contrasts（没有则空数组）
        contrasts: List[Dict[str, str]] = []
        raw_contrasts = u.get("contrasts")
        if isinstance(raw_contrasts, list):
            for c in raw_contrasts:
                if isinstance(c, dict) and (c.get("a") or c.get("b")):
                    contrasts.append({
                        "a": str(c.get("a") or "").strip(),
                        "b": str(c.get("b") or "").strip(),
                        "difference": str(c.get("difference") or "").strip(),
                    })
        # 升级版备课思考链：透传 prerequisites / core_formulas / gateway_questions / contribution_to_target
        prerequisites: List[str] = []
        raw_prereq = u.get("prerequisites")
        if isinstance(raw_prereq, list):
            for p in raw_prereq:
                if p is None:
                    continue
                ps = str(p).strip()
                if ps:
                    prerequisites.append(ps)
        core_formulas: List[Dict[str, str]] = []
        raw_cf = u.get("core_formulas")
        if isinstance(raw_cf, list):
            for cf in raw_cf[:3]:
                if not isinstance(cf, dict):
                    continue
                cn = str(cf.get("name") or "").strip()
                cfo = str(cf.get("formula") or "").strip()
                cv = str(cf.get("variables") or "").strip()
                if cn or cfo:
                    core_formulas.append({"name": cn, "formula": cfo, "variables": cv})
        gateway_questions: List[str] = []
        raw_gq = u.get("gateway_questions")
        if isinstance(raw_gq, list):
            for q in raw_gq[:3]:
                if q is None:
                    continue
                qs = str(q).strip()
                if qs:
                    gateway_questions.append(qs)
        contribution_to_target = str(u.get("contribution_to_target") or "").strip()
        units_out.append({
            "index": idx,
            "title": title,
            "summary": summary,
            "target": target,
            "key_points": key_points,
            "modules": modules,
            "contrasts": contrasts,
            "prerequisites": prerequisites,
            "core_formulas": core_formulas,
            "gateway_questions": gateway_questions,
            "contribution_to_target": contribution_to_target,
        })
    # 升级版备课思考链：顶层课程级字段
    course_target = str((plan or {}).get("course_target") or "").strip()
    acceptance_criteria = str((plan or {}).get("acceptance_criteria") or "").strip()
    total_lessons_raw = (plan or {}).get("total_lessons")
    try:
        total_lessons = int(total_lessons_raw) if total_lessons_raw is not None else None
    except (TypeError, ValueError):
        total_lessons = None
    return {
        "topic": str((plan or {}).get("topic") or "").strip(),
        "target": str((plan or {}).get("target") or "").strip() or course_target or f"系统掌握本课程的核心概念",
        "course_target": course_target,
        "acceptance_criteria": acceptance_criteria,
        "total_lessons": total_lessons if total_lessons else len(units_out),
        "units": units_out,
    }


def _strip_unit_prefix_for_metadata(t: str) -> str:
    """剥掉 unit 标题中的「第 N 课 / Lesson N / Unit N」序号前缀，迁移旧数据。"""
    s = str(t or "").strip()
    for _ in range(3):
        new_s = re.sub(
            r"^\s*(?:第\s*\d+\s*课|lesson\s*\d+|unit\s*\d+|第\s*\d+\s*讲)\s*[:：\-—、\.\s]+",
            "", s, flags=re.IGNORECASE,
        )
        if new_s == s:
            break
        s = new_s.strip()
    return s or str(t or "").strip()


def _load_syllabus(lesson_folder: str | None) -> Dict[str, Any]:
    """读取 lessons/{course}/syllabus.json，失败/缺失时返回空 dict。"""
    if not lesson_folder:
        return {}
    p = LESSONS_DIR / lesson_folder / "syllabus.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ai_tool_rules() -> List[str]:
    """AI 参数化工具（终端/图片/黑板）调用规则，注入系统提示词。"""
    return [
        "- 【数值计算必须用终端（强制）】凡是涉及数值计算/运算（公式代入求解、加减乘除、百分比、单位换算、成绩统计等），你必须在回复中输出 `[TOOL:{\"type\":\"show_terminal\",\"language\":\"python\",\"code\":\"<一行可运行的 python 代码，用 print 输出结果>\"}]` 来实际运行计算，严禁心算或估算。正文引用的每个数值必须与终端运行输出完全一致；若正文先写了数值而终端结果不同，以终端结果为准并修正正文。",
        "- 【用户明确要求终端时必须用工具标记】当用户说「打开终端 / 把终端拉起来 / 开一下终端」等要求时，你【必须】在回复中输出 `[TOOL:{\"type\":\"show_terminal\",\"language\":\"python\",\"code\":\"<要演示或执行的 python 代码>\"}]` 来弹出终端；严禁只把代码写在 markdown 代码块（```python）里冒充「打开终端」——代码块只是排版，不会弹出终端窗口。若只是想让终端窗口出现、暂不运行代码，可输出 code 为空字符串的 show_terminal 工具。",
        "- 终端代码示例：`[TOOL:{\"type\":\"show_terminal\",\"language\":\"python\",\"code\":\"a=8; b=3; print('a*b =', a*b)\"}]`；若只想展示不执行加 `\"noRun\":true`。支持语言：python / javascript / shell / powershell。执行结果会记录进对话，之后你可以基于实际输出继续讲解或纠错。",
        "- 需要展示代码/演示运行结果（不一定是数值计算）时，同样用 `[TOOL:show_terminal]` 弹出终端并自动执行。",
        "- 黑板与终端可以同时使用：先输出 `[TOOL:{\"type\":\"show_board\",\"content\":\"...\"}]` 写公式，再输出 `[TOOL:{\"type\":\"show_terminal\",\"language\":\"python\",\"code\":\"...\"}]` 运行计算；一个回复最多 2 个工具标记，每个标记各自独占一行。",
        "- 需要展示图片时，可在回复末尾输出 `[TOOL:{\"type\":\"show_image\",\"index\":1}]` 或 `[TOOL:{\"type\":\"show_image\",\"filename\":\"xxx.png\"}]` 弹出图片面板。index 为当前单元图片库序号（从 0 开始，省略则显示第一张）；filename 为图片文件名，会从当前单元图片中按文件名匹配（匹配不到则显示第一张）。",
        "- 需要板书推导或画图时，可在回复末尾输出 `[TOOL:{\"type\":\"show_board\",\"content\":\"推导：\\\\n$$E=mc^2$$\\\\n{graph:y=x^2,x:-2..2}\"}]` 弹出黑板。content 支持：普通文本（打字机逐字显示）；`$$...$$` 数学公式（LaTeX，displayMode 自动居中）；`{graph:y=表达式,x:最小值..最大值}` 函数曲线；`{line:x1,y1-x2,y2}` 线段（坐标 0-100）。",
        "- 【重要】板书中的所有数学公式、带下标/上标的变量、化学式或数学符号（如 `x_1`、`y_0`、`a_n`），**必须整体写在 `$$...$$` 之内**（如 `$$x = 3 \\times 2 = 6$$`），严禁拆成单字符一行一个（`x\\n =\\n 3`）！这是板书输出最常见的错误，会让公式无法渲染。普通中文/英文文本段落（标题、步骤说明、结论句）放在 `$$` 之外。",
        "- 公式里需要换行/对齐时，用 `\\\\\\\\` 换行 + `&` 对齐，例如：$$ \\\\begin{aligned} a &= 3 + 2 \\\\\\\\ b &= a \\times 4 \\end{aligned} $$",
        "- 【触发时机（从紧，禁止乱弹黑板）】只有在内容确实需要「板书书写」时才通过一次 `[TOOL:{\"type\":\"show_board\",\"content\":\"...\"}]` 弹出黑板——如公式推导、函数图像、几何示意图、需要画图演示的解题过程。纯文字讲解、知识点罗列、概念解释、口头总结、日常对话等【严禁】弹出黑板（学生会觉得莫名其妙）；一条回复最多输出 1 个 show_board 标记。",
        "- 每个工具标记必须整体独占一行；`[TOOL:{...}]` 内部不要换行（换行用 \\n 表示），工具标记之间用空行或正文隔开。",
    ]


def _ai_action_rules() -> List[str]:
    """肢体动作标记（[ACTION:xxx]）使用规则，注入系统提示词。

    动作分两类：基础教学动作（播放模型预设 motion 文件）与语义动作
    （前端用 ARKit 参数时间轴直接驱动，不依赖 motion3 文件）。
    """
    return [
        "- 需要肢体动作配合教学时，可在回复末尾单独输出动作标记（每段回复最多 1-2 个）：",
        "- 基础教学动作：`[ACTION:point]`（指向）、`[ACTION:blackboard]`（拉黑板）、`[ACTION:think]`（思考）、`[ACTION:listen]`（倾听）、`[ACTION:speak]`（说话）、`[ACTION:hello]`（打招呼）。",
        "- 语义动作（按语境使用）：`[ACTION:nod]`（点头，同意/肯定学生）、`[ACTION:agree]`（赞许点头，表扬学生答对）、`[ACTION:shake]`（摇头，否定/纠正）、`[ACTION:tilt]`（歪头，疑惑/好奇）、`[ACTION:gasp]`（惊讶）、`[ACTION:cheer]`（雀跃，学生答对或值得庆祝时）、`[ACTION:sigh]`（叹气，遗憾/无奈）、`[ACTION:bow]`（鞠躬，开场问好/下课道别）。",
        "- 高级精确控制（仅在语义动作不够用时）：可输出 `[PARAM:ParamAngleX=-15 ParamAngleY=5]`（参数名=数值，空格分隔）直接调整头部/身体角度或五官（如 ParamAngleX/Y/Z 头部、ParamBodyAngleX/Y/Z 身体、ParamEyeLOpen 睁眼、ParamJawOpen 张嘴、ParamMouthSmile 微笑），数值会被安全钳制，且会自动恢复。优先使用上面列出的语义动作标记，避免滥用参数直调。",
        "- 动作标记不显示给学生，仅触发教师角色的动画。严禁在正文中用文字描述动作过程（如“我拿出黑板”“老师指向”“转过身”等），动作只能通过标记触发，正文只讲课程内容。",
        "- 工具标记（终端/图片/黑板）触发后的内容**不要在正文中复述**：不要写“黑板上出现分步推导公式”“终端会执行以下代码”“图片显示了…”这类元描述。学生看到的只是工具标记→弹窗。",
    ]


def _ai_emotion_rules() -> List[str]:
    """情绪标签（Open-LLM-VTuber 协议）使用规则，注入系统提示词。

    采用 Open-LLM-VTuber 官方 live2d_expression_prompt 风格：
    允许标签内嵌在句子任意位置，AI 输出文本即驱动 Live2D 表情，无需手动摆动作。
    """
    return [
        "【表情控制规则（Open-LLM-VTuber 协议）】",
        "- 在回复中使用下面的表情关键词来表达表情或动作，请经常使用它们：",
        "- 可用关键词（仅限这些，必须带方括号）：`[joy]` `[sadness]` `[anger]` `[surprise]` `[fear]` `[disgust]` `[neutral]` `[smirk]`（同时兼容 `[EMOTION:happy]` 等长格式）。",
        "- 关键词可以内嵌在句子中的任意位置（句首 / 句中 / 句末），不必独占一行：",
        "  例 1：\"好的！[joy] 这个知识点特别重要，我们来看一看。\"",
        "  例 2：\"[think] 让我想想……嗯，是这样。\"",
        "  例 3：\"太棒了，你答对了！[joy] 继续保持！\"",
        "- 标签不会显示给学生，仅用于控制 Live2D 表情；严禁在正文中用文字描述表情过程。",
        "- 语义参考：joy/happy=开心鼓励；sadness/sad=遗憾安慰；anger/angry=生气纠正；surprise/surprised=惊讶；think=思考；neutral=平静；smirk=俏皮；fear/disgust=极少用。",
        "- 一段回复内一般 1-3 个标签就够，不要刷屏。",
    ]


def _ai_grounding_rules() -> List[str]:
    """事实锚定与防幻觉规则：强制基于备课教案与课程资料作答，数值计算必须走工具。

    本地/云端对话链路共用 build_system_prompt，因此此处约束对两条链路同时生效，
    用于最大程度抑制模型脱离资料编造内容、心算错误或虚构出处。
    """
    return [
        "【事实锚定与防幻觉（最高优先级）】",
        "- 公式核对规则（必须执行）：凡涉及公式、定理、定律或常量（如勾股定理 a²+b²=c²、π≈3.14159、e≈2.71828）时，必须先在本课教案的 modules[].concept 与 key_points 中检索出对应公式，逐项核对系数、变量与适用条件后再回答；教案中没有的公式，必须明确说「教案中没有这个公式，我不能确认」，严禁仅凭记忆写出公式、擅自补系数，或直接认可学生给出的公式。",
        "- 概念辨析规则（必须执行）：当学生提出一个说法让你判断对错时，先核对定义——该说法中的每个术语是否用对（例如把「质数」说成「所有奇数」，就是把质数与奇数混淆）。发现相似概念被混用时（变量vs常量、赋值vs比较、整数vs字符串、整除vs除法等），先分别给出两个概念的精确定义，再指出说法错在哪里，最后用一个反例说明二者的区别。绝不能在未核对定义的情况下直接附和学生的说法。",
        "- 所有讲解内容必须严格依据【本课教案】【本课详细资料】【全课程目录与要点总览】中的概念、公式、数据与示例，不得脱离资料自创或编造。",
        "- 资料中未包含的内容：若属于公认常识，可简短补充并说明这是常识；若无法确认，请直接说「这部分我手头资料没有，建议查证一下」，严禁编造公式、数据、人名、日期、参考文献或引文。",
        "- 严禁虚构出处（如「资料里提到」「书上说」「PPT 里写的」）；资料里没有就是没有。",
        "- 术语定义必须严谨准确（例如：整除用 // 运算符、`10 / 4` 是除法得 2.5、`10 // 4` 是整除得 2）。定义不确定时，用口语举例解释代替下定义，绝不给出错误定义。",
        "- 凡是涉及数值计算（公式代入求解、数学运算、成绩统计、比例换算、单位转换等），必须在回复末尾输出 `[TOOL:{\"type\":\"show_terminal\",\"language\":\"python\",\"code\":\"...\"}]` 用真实代码计算出结果后再给出结论，严禁心算或估算；回复中引用的具体数值必须与工具实际运行输出一致，不得修改。",
        "- 给出例题/数据时优先使用资料中的原始数据；确需自拟数据时，仅用于示意且数值必须合理自洽，并明确标注为示例。",
        "- 演示代码必须可运行（必须执行）：给学生的示例代码（终端演示、代码块、show_terminal 的 code）必须先在心里完整跑通，确保语法正确、变量已定义、类型匹配、不会报错（如 `\"10\" + 5` 这类 str+int 会报 TypeError，必须写 `int(\"10\") + 5`）；除非教学意图就是「故意演示某类报错并让学生观察」，否则严禁给出会报错的示例代码。写完代码后自己逐行检查一遍变量名、引号、缩进是否一致。",
        "- 若学生在某个概念上反复出错，允许回看教案对应 module 的 concept/example 重新讲解，但讲解内容本身仍必须锚定教案与资料。",
    ]


def _build_student_profile(lesson_folder: str, units: List[Dict[str, Any]], current_unit: int) -> str:
    """跨章"蒸馏"：从本课程已完成的对话历史中提炼学生画像，注入系统提示词。

    目的：进入下一章时模型不再"失忆"——延续一致的称呼、语气、节奏与教学默契，
    让人格/教学风格跨单元不突变。提炼内容：
    - 已学单元 → 当前单元；
    - 对话轮数与答对/需重讲迹象统计；
    - 最近几条有效互动摘录。
    """
    try:
        conv = load_conversation(lesson_folder)
    except Exception:
        return ""
    if not conv:
        return ""
    meaningful: List[str] = []
    for m in conv:
        if m.get("role") != "user":
            continue
        s = str(m.get("content") or "").strip()
        # 过滤系统注入与终端执行记录等非真实学生发言
        if not s or s.startswith("（") or s.startswith("[终端执行记录]") or s.startswith("["):
            continue
        meaningful.append(s)
    if not meaningful:
        return ""
    total_turns = len(meaningful)
    praises = mistakes = 0
    for m in conv:
        if m.get("role") != "assistant":
            continue
        c = str(m.get("content") or "")
        if any(k in c for k in ("答对", "没错", "完全正确", "对的", "很好", "很棒", "掌握", "答得对")):
            praises += 1
        if any(k in c for k in ("不太对", "有点不太对", "不对哦", "错了", "再讲一遍", "重新讲", "纠正", "没太对")):
            mistakes += 1
    lines = ["【学生画像（跨章蒸馏记忆，用于保持教学连贯）】"]
    if units and 0 <= current_unit < len(units):
        learned = [str(u.get("title") or f"第{i + 1}课") for i, u in enumerate(units) if i < current_unit]
        cur_title = str(units[current_unit].get("title") or f"第{current_unit + 1}课")
        if learned:
            lines.append(f"- 已学内容：{' → '.join(learned)}；现在进入「{cur_title}」。")
        else:
            lines.append(f"- 当前是本章第一课「{cur_title}」。")
    lines.append(f"- 对话概况：本课程累计 {total_turns} 轮师生互动。")
    if praises or mistakes:
        lines.append(f"- 从历史看：学生答对/掌握迹象约 {praises} 次，理解偏差/需重讲迹象约 {mistakes} 次。")
    recent = meaningful[-3:]
    if recent:
        lines.append("- 最近互动摘录：")
        lines.extend(f"  - {u[:50]}" for u in recent)
    lines.append("- 要求：延续之前一致的称呼、语气、节奏与教学默契；不要像第一次见面一样重新自我介绍或突然更换风格。")
    return "\n".join(lines)


# ==================== 单元级学生画像蒸馏（可迭代） ====================
# 每一课/单元学习结束后，从本单元对话蒸馏学生的性格/爱好/喜欢的授课方式等画像，
# 持久化到 student_profile.json；下一单元系统提示词注入最新画像（增量更新，可迭代）。

_PROFILE_FIELDS = ["性格特点", "兴趣爱好", "喜欢的授课方式", "知识薄弱点", "学习节奏", "沟通偏好"]


def _student_profile_path(lesson_folder: str) -> Path:
    return lesson_path(lesson_folder, "student_profile.json")


def _load_student_profile(lesson_folder: str) -> Dict[str, Any]:
    try:
        p = _student_profile_path(lesson_folder)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"units": {}, "merged": {}}


def _save_student_profile(lesson_folder: str, data: Dict[str, Any]) -> None:
    _atomic_write_json(_student_profile_path(lesson_folder), data)


def _distill_llm_call(system: str, user: str) -> str:
    """画像蒸馏专用 LLM 调用：本地 Ollama 优先，失败/关闭回退云端（大输出配额）。"""
    if not user.strip():
        return ""
    cfg = load_config()
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    # 1) 本地 Ollama
    if cfg.get("enable_local_ollama", True):
        base_url = (cfg.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
        model = (cfg.get("ollama_model") or "qwen2.5:7b").strip()
        try:
            resp = requests.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": 0.3,
                        "num_ctx": int(cfg.get("ollama_num_ctx", 16384) or 16384),
                        "num_predict": 2000,
                    },
                },
                timeout=120,
            )
            if resp.ok:
                content = (resp.json().get("message", {}).get("content") or "").strip()
                if content:
                    return content
            print(f"[profile] ollama HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
        except Exception as exc:
            print(f"[profile] ollama 异常: {exc}", flush=True)
    # 2) 云端 OpenAI 兼容 API（chat_* 优先，回退 cloud_*）
    chat_key = (cfg.get("chat_api_key") or "").strip()
    chat_model = (cfg.get("chat_model") or "").strip()
    chat_base = (cfg.get("chat_base_url") or "").rstrip("/").strip()
    if chat_key and chat_model and chat_base:
        key, model, base_url = chat_key, chat_model, chat_base
    else:
        key = (cfg.get("cloud_api_key") or cfg.get("siliconflow_api_key") or "").strip()
        if not key:
            return ""
        model = (cfg.get("cloud_model") or cfg.get("siliconflow_model") or "deepseek-ai/DeepSeek-V3").strip()
        base_url = (cfg.get("cloud_base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1500,
    }
    if bool(cfg.get("enable_search", False)):
        payload["enable_search"] = True
    try:
        print(f"[AI-REQUEST] 画像蒸馏 → 云端: {url} | model: {model}", flush=True)
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if resp.ok:
            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content") or ""
            return str(content).strip()
        print(f"[profile] 云端 HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
    except Exception as exc:
        print(f"[profile] 云端异常: {exc}", flush=True)
    return ""


def _distill_student_profile(lesson_folder: str, unit_index: int, conv: Optional[List[Dict[str, str]]] = None) -> bool:
    """对指定单元蒸馏学生画像（增量更新），成功返回 True。

    把本单元有意义的学生发言（+已有画像）交给 LLM，输出合并后的结构化画像，
    写回 student_profile.json 的 units[unit_index] 与 merged，并清空 prompt 缓存。
    """
    if conv is None:
        try:
            conv = load_conversation(lesson_folder)
        except Exception:
            conv = []
    meaningful: List[str] = []
    for m in conv:
        if m.get("role") != "user":
            continue
        s = str(m.get("content") or "").strip()
        # 过滤系统注入与终端执行记录等非真实学生发言
        if not s or s.startswith("（") or s.startswith("[终端执行记录]") or s.startswith("["):
            continue
        meaningful.append(s)
    if not meaningful:
        return False

    profile_data = _load_student_profile(lesson_folder)
    units_profile = profile_data.get("units") or {}
    if not isinstance(units_profile, dict):
        units_profile = {}
    unit_info = units_profile.get(str(unit_index)) or {}
    old_profile = unit_info.get("profile") or profile_data.get("merged") or {}
    if not isinstance(old_profile, dict):
        old_profile = {}

    transcript = "\n".join(f"- {s[:200]}" for s in meaningful[-15:])
    system = (
        "你是一名教学观察分析师，从师生对话中提炼学生画像，帮助老师个性化教学。"
        "只输出一个 JSON 对象，不要 Markdown 代码围栏，不要任何额外解释。"
    )
    user = (
        "请根据以下对话记录提炼学生的画像，字段包含且仅包含："
        f"「{'、'.join(_PROFILE_FIELDS)}」。\n"
        f"输出格式：{{\"性格特点\": [\"...\"], \"兴趣爱好\": [\"...\"], \"喜欢的授课方式\": [\"...\"], "
        f"\"知识薄弱点\": [\"...\"], \"学习节奏\": \"...\", \"沟通偏好\": \"...\"}}\n"
        "若对话中无明显信息，对应字段填空数组/空字符串。\n\n"
    )
    if old_profile:
        user += (
            "已有画像（请在此基础上增量更新：保留仍然成立的信息、修正过时信息、补充新发现，"
            "不要重复收集已确认的信息）：\n"
            f"{json.dumps(old_profile, ensure_ascii=False, indent=2)}\n\n"
        )
    user += f"本次对话记录：\n{transcript}"

    text = _distill_llm_call(system, user)
    if not text:
        print("[profile] 蒸馏调用无返回，跳过", flush=True)
        return False
    try:
        new_profile = _robust_json_loads(text)
    except Exception as exc:
        print(f"[profile] 画像解析失败: {exc}", flush=True)
        return False
    if not isinstance(new_profile, dict):
        return False
    # 合并：新画像字段为空时保留旧值（增量更新）
    merged: Dict[str, Any] = dict(old_profile)
    for k, v in new_profile.items():
        if v in (None, "", [], {}):
            continue
        merged[k] = v

    metadata = load_lesson_metadata(lesson_folder)
    units = metadata.get("units") or []
    title = ""
    if 0 <= unit_index < len(units):
        title = str(units[unit_index].get("title") or f"第{unit_index + 1}课")
    units_profile[str(unit_index)] = {
        "title": title,
        "profile": merged,
        "distilled_at": now_iso(),
        "turns": len(meaningful),
    }
    profile_data["units"] = units_profile
    profile_data["merged"] = merged
    profile_data["updated_at"] = now_iso()
    _save_student_profile(lesson_folder, profile_data)
    _PROMPT_CACHE.clear()  # 画像已更新，立即让下次对话生效
    print(f"[profile] 第 {unit_index + 1} 单元画像蒸馏完成，字段: {list(merged.keys())}", flush=True)
    return True


def _format_distilled_profile(profile: Dict[str, Any], units: List[Dict[str, Any]], current_unit: int) -> str:
    """把已蒸馏画像格式化为系统提示词注入段落。"""
    lines = ["【学生画像（个性化教学参考，源自历史对话蒸馏，随学习迭代更新）】"]
    for k in _PROFILE_FIELDS:
        v = profile.get(k)
        if not v:
            continue
        if isinstance(v, list):
            v = "、".join(str(x) for x in v if str(x).strip())
        v = str(v).strip()
        if v:
            lines.append(f"- {k}：{v}")
    if len(lines) == 1:
        return ""
    lines.append("- 要求：根据画像提供个性化教学，延续学生偏好的授课方式与沟通风格；发现新特点时自然融入，不要机械套用、不要主动向学生复述画像内容。")
    return "\n".join(lines)


# build_system_prompt 结果缓存：同一课程 60s 内复用，避免每条消息都全量读盘拼接 prompt
# （切单元/改配置后最多 60s 延迟生效，换取每条消息 50-200ms 的 IO/CPU 节省）
_PROMPT_CACHE: Dict[str, tuple] = {}
_PROMPT_CACHE_TTL = 60.0


def build_system_prompt(lesson_folder: str | None = None) -> str:
    key = lesson_folder or ""
    now = time.time()
    hit = _PROMPT_CACHE.get(key)
    if hit and (now - hit[0]) < _PROMPT_CACHE_TTL:
        return hit[1]
    result = _build_system_prompt_impl(lesson_folder)
    _PROMPT_CACHE[key] = (time.time(), result)
    return result


def _build_system_prompt_impl(lesson_folder: str | None = None) -> str:
    metadata = load_lesson_metadata(lesson_folder)
    cfg = load_config()
    assistant_name = (
        metadata.get("assistant_name")
        or cfg.get("assistant_name")
        or "艾琳老师"
    ).strip()
    personality = (
        cfg.get("personality_prompt")
        or metadata.get("personality_prompt")
        or "你是一名耐心的学习教练。"
    ).strip()

    header = (
        f"【基本身份】\n"
        f"你的名字是：{assistant_name}。请你在对话中始终以「{assistant_name}」自居，"
        f"不要声称自己是其他品牌的 AI 助手或来自其他公司。\n\n"
        f"【当前时间（内部参考，禁止主动提及）】\n"
        f"当前真实时间是 {_now_display()}。\n"
        f"规则：只有当学生主动询问时间、日期或星期时，你才照搬以上数字回答；"
        f"其他任何情况下严禁主动提及时间或日期（尤其不要在开场白、欢迎语、讲解开头报时间）。\n\n"
        f"【角色设定】\n{personality}\n\n"
        f"【输出规范】\n"
        f"- 直接回答用户问题，不要输出思考过程、内心独白或复述用户需求"
        f"（禁止出现「首先，用户请求…」「我需要…」这类开场白）。\n"
        f"- 口语化、简洁、像真人老师面对面授课。\n"
        f"- 【绝对禁止报时间】除非学生直接问「现在几点/今天几号/星期几」，否则你的任何回复（尤其开场白、欢迎语、讲解开头）都不得出现时间、日期、星期等字样，一次都不行。\n"
        f"- 【Markdown 规范】只允许使用：标题(#)、列表(-/1.)、粗体(**x**)、代码块(```)和表格；"
        f"严禁使用 ~~删除线~~ 语法和独立成行的 --- 水平线（会被渲染成奇怪的划线/横线）。\n"
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
        ]
        tool_lines.extend(_ai_action_rules())
        tool_lines.extend(_ai_tool_rules())
        tool_lines.extend(_ai_emotion_rules())
        tool_lines.extend(_ai_grounding_rules())
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

    # 修复：单元标题常被用户/AI 误填为「第3课：xxx」这种带序号前缀的形式，
    # 直接拿给 AI 会让模型把「第 3 课」当作当前课序号，导致输出"欢迎来到第 3 课"等错位文案。
    # 这里在注入 prompt 前统一剥掉「第N课」「Lesson N」「Unit N」等前缀。
    def _strip_unit_prefix(t: str) -> str:
        s = str(t or "").strip()
        # 反复剥：支持「第3课：xxx」「Lesson 3: xxx」「Unit 3 - xxx」
        for _ in range(3):
            new_s = re.sub(r"^\s*(?:第\s*\d+\s*课|lesson\s*\d+|unit\s*\d+|第\s*\d+\s*讲)\s*[:：\-—、\.\s]+", "", s, flags=re.IGNORECASE)
            if new_s == s:
                break
            s = new_s.strip()
        return s or str(t or "").strip()

    unit_title = _strip_unit_prefix(unit.get("title")) or f"第 {current_unit + 1} 课"
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
        u_title = _strip_unit_prefix(u.get("title")) or f"第 {i + 1} 课"
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
    # 学生画像注入：优先使用已蒸馏的结构化画像（单元级、可迭代）；无画像时回退统计式跨章画像
    try:
        _distilled = _load_student_profile(lesson_folder).get("merged") or {}
        _profile = _format_distilled_profile(_distilled, units, current_unit) if _distilled else ""
        if not _profile:
            _profile = _build_student_profile(lesson_folder, units, current_unit)
        if _profile:
            parts.append(_profile)
    except Exception:
        pass
    parts.append(f"【全课程目录与要点总览】\n以下是本课程所有单元的结构与核心要点，请据此把握课程的整体脉络：\n{course_overview}")
    parts.append(
        f"【当前进度】\n现在上到第 {current_unit + 1} 课 / 共 {total_units} 课：{unit_title}。"
        f"（重要）单元标题仅作为本课主题名，不要把标题里若有的「第N课」「Lesson N」等序号字样当成课程序号引用——"
        f"请始终以系统告知的「第 {current_unit + 1} 课」为准。"
    )
    if unit_summary:
        parts.append(f"【本课概述】\n{unit_summary}")

    # 教案注入：从 lessons/{course}/syllabus.json 读出当前 unit 的 target + modules，
    # 作为"阶段二讲课思考链"的强引导（备课与讲课解耦的关键衔接点）。
    syllabus = _load_syllabus(lesson_folder)
    current_teach_plan = None
    if syllabus and isinstance(syllabus.get("units"), list):
        for su in syllabus["units"]:
            if isinstance(su, dict) and int(su.get("index", -1)) == current_unit:
                current_teach_plan = su
                break
    if current_teach_plan:
        target_line = current_teach_plan.get("target") or unit_summary or "理解本课核心"
        modules = current_teach_plan.get("modules") or []
        # 升级版备课思考链：读取课程级字段（顶层 course_target / acceptance_criteria / total_lessons）
        course_target = (syllabus or {}).get("course_target") if syllabus else ""
        acceptance_criteria = (syllabus or {}).get("acceptance_criteria") if syllabus else ""
        total_lessons = (syllabus or {}).get("total_lessons") if syllabus else None
        course_header_parts: List[str] = []
        if course_target:
            course_header_parts.append(f"课程目标（course_target）：{course_target}")
        if acceptance_criteria:
            course_header_parts.append(f"验收标准（acceptance_criteria）：{acceptance_criteria}")
        if total_lessons:
            course_header_parts.append(f"总课时（total_lessons）：{total_lessons} 课；当前为第 {current_unit + 1} 课")
        plan_lines = ["【本课教案（来自备课阶段，请按此执行）】"]
        if course_header_parts:
            plan_lines.extend(course_header_parts)
        plan_lines.append(f"本课学习目标（target）：{target_line}")
        plan_lines.append("讲解模块序列（modules）：")
        for mi, m in enumerate(modules):
            mid = m.get("id") or f"M{mi + 1}"
            mtitle = m.get("title") or ""
            concept = m.get("concept") or ""
            example = m.get("example") or ""
            anchor = m.get("anchor") or ""
            interaction = m.get("interaction") or ""
            action = m.get("action") or ""
            plan_lines.append(
                f"  {mid} {mtitle}".rstrip()
            )
            if concept:
                plan_lines.append(f"     - 概念：{concept}")
            if example:
                plan_lines.append(f"     - 例子：{example}")
            if anchor:
                plan_lines.append(f"     - 锚点：{anchor}")
            if interaction:
                plan_lines.append(f"     - 节奏点（交互）：{interaction}")
            if action:
                plan_lines.append(f"     - 动作提示：{action}")
        # 易混淆概念辨析（来自备课阶段 contrasts 字段）：学生混用概念时必须先给定义再纠正
        # 升级版备课思考链：注入本课的核心公式 / 单元通关问题 / 对课程目标的贡献 / 前置依赖
        core_formulas = current_teach_plan.get("core_formulas") or []
        valid_formulas = [c for c in core_formulas if isinstance(c, dict) and (c.get("name") or c.get("formula"))]
        if valid_formulas:
            plan_lines.append("")
            plan_lines.append("本课核心公式/概念（讲课时涉及这些公式必须与教案一致；学生给出不同公式时必须先核对教案再决定是否纠正）：")
            for f in valid_formulas:
                name = str(f.get("name") or "").strip()
                formula = str(f.get("formula") or "").strip()
                variables = str(f.get("variables") or "").strip()
                line = f"  - {name}" if name else "  -"
                if formula:
                    line += f"：{formula}"
                if variables:
                    line += f"（{variables}）"
                plan_lines.append(line)
        gateway_questions = current_teach_plan.get("gateway_questions") or []
        valid_questions = [q for q in gateway_questions if isinstance(q, str) and q.strip()]
        if valid_questions:
            plan_lines.append("")
            plan_lines.append("本课通关问题（讲完本课后向学生提问，作为理解验证）：")
            for q in valid_questions:
                plan_lines.append(f"  - {q}")
        contribution_to_target = str(current_teach_plan.get("contribution_to_target") or "").strip()
        if contribution_to_target:
            plan_lines.append("")
            plan_lines.append(f"本课对课程目标的贡献：{contribution_to_target}")
        prerequisites = current_teach_plan.get("prerequisites") or []
        valid_prereq = [p for p in prerequisites if isinstance(p, str) and p.strip()]
        if valid_prereq:
            plan_lines.append("")
            plan_lines.append(f"前置依赖（学生应已掌握）：{'; '.join(valid_prereq)}")
        contrasts = current_teach_plan.get("contrasts") or []
        valid_contrasts = [c for c in contrasts if isinstance(c, dict) and (c.get("a") or c.get("b"))]
        if valid_contrasts:
            plan_lines.append("")
            plan_lines.append("易混淆概念辨析（当学生混淆下列概念时，必须先分别给出两个概念的精确定义，再指出说法错在哪，最后给反例）：")
            for c in valid_contrasts:
                a = str(c.get("a") or "").strip()
                b = str(c.get("b") or "").strip()
                diff = str(c.get("difference") or "").strip()
                pair = f"{a} vs {b}" if a and b else (a or b)
                plan_lines.append(f"  - {pair}：{diff}" if diff else f"  - {pair}")
        # 阶段二讲课思考链：要求 AI 按教案执行、不另起炉灶
        plan_lines.append("")
        plan_lines.append(
            "【阶段二：讲课思考链（必须遵守）】\n"
            "  1)【对照教案】— 当前应推进到哪个 module？以本课教案为准，不要临时换内容主题。\n"
            "  2)【判断学生状态】— 学生在不在听？有没有说『懂』/『不懂』？沉默则主动问『刚才这个点，需要换种说法吗？』；答对则推进下一 module；答错则原地换一种方式再讲一遍同一个 module，不要跳。\n"
            "  2.5)【学生消息意图判断（必须执行）】— 先判断学生这条消息是『回答你上一轮提出的问题』还是『向你提问』：\n"
            "     - 是回答：立即判断对错。答对 → 明确表扬并继续推进；答错 → 必须先用一句话明确指出正确答案（如『带引号的\"2025-06\"是字符串，不是整数哦』），再只围绕学生答错的那一个点重讲，严禁跑去讲与错误点无关的其他概念。\n"
            "     - 是提问：按正常提问流程回答。\n"
            "     - 学生说『懂了/我会了/明白了/知道了』：不要使用『好像有点不太对哦，我换一种说法』这类犯错重讲句式，直接肯定并推进到下一模块。\n"
            "  3)【按模块执行】— 每个 module 内按 concept → example → anchor 顺序讲；讲完输出 modules[].interaction 给出的交互（『提问：…』『小测验：…』等）。\n"
            "  4)【动作执行】— 讲 module 时输出 modules[].action 对应的 Live2D 动作标记（如 [ACTION:bow] / [ACTION:point]），用动作辅助表达。\n"
            "  5)【决定下一步】— 当前 module 全部交互完成 → 进入下一个 module；所有 module 完成 → 总结 → 在回复末尾输出 `[TOOL:start_exam]` 触发测验。\n"
            "  严禁脱离教案自创新模块；如学生主动越界问其它内容，可简短回应但立即拉回当前 module。"
        )
        parts.append("\n".join(plan_lines))
    elif unit_key_points:
        # 旧课程没有 syllabus.json（数据迁移失败时的兜底）
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
    tool_lines.extend(_ai_action_rules())
    tool_lines.extend(_ai_tool_rules())
    tool_lines.extend(_ai_emotion_rules())
    parts.append("\n".join(tool_lines))
    parts.append("\n".join(_ai_grounding_rules()))

    # 教学行为指引：开课时先系统讲解知识点
    # 分段标记从配置读取（纯文本分段符，非代码块语言标记）
    segment_enabled = cfg.get("segment_enabled", True)
    segment_marker = (cfg.get("segment_marker") or "\\c").replace("\\n", "\n")
    segment_max_lines = int(cfg.get("segment_max_lines", 6) or 6)

    teach_guide = [
        "【教学行为指引】",
        "- 当学生请求「开始上课」「讲下一课」或这是新单元的首次对话时，你必须先系统性地讲解本课的全部主要知识点。",
        "- 讲解时按要点逐条展开，每个要点配合简短示例说明，确保学生能理解。",
        "- 讲解时可结合之前课程的知识做回顾，也可简要预告后续课程内容以建立知识框架。",
        "- 全部知识点讲完后，询问学生是否有疑问；若学生表示理解，再输出 `[TOOL:start_exam]` 触发测验。",
        "- 不要跳跃式教学，不要只抛问题不给答案，先讲透知识再互动。",
        "- 当学生问的问题超出当前课时范围时，可适当涉及相关的前后知识点，但要保持以当前课时为重点。",
    ]
    if segment_enabled:
        teach_guide.extend([
            f"- 【分段输出（重要）】你必须使用纯文本分段标记 {segment_marker} 将回复分成多个小段，每段只讲一个知识点或一个完整意思。",
            "  规则：",
            f"  1. 每讲完一个知识点、一个公式、或一个示例后，必须插入 {segment_marker}。",
            f"  2. 每段控制在 1~{segment_max_lines} 行以内，不要一次性输出大段文字。",
            f"  3. {segment_marker} 必须独占一行（前后有换行），不要嵌在句子中间。",
            f"  4. LaTeX 公式必须完整地在同一段内输出，绝不能在公式中间插入 {segment_marker}。",
            f"  5. 代码块（```...```）、表格（|...|）、HTML 标签必须完整闭合后才能插入 {segment_marker}。",
            f"  6. 列表项必须整组完成后才能插入 {segment_marker}，不要在列表中间插入。",
            f"  7. 插入 {segment_marker} 前请自检：当前是否有未闭合的 `$`、`$$`、`\\(`、`\\[`、``` 、`|` 等标记？如果有，必须先闭合再插入 {segment_marker}。",
            f"  8. 最后一段末尾不需要 {segment_marker}。",
            "  ⚠️【易错警告 - 必须遵守】",
            f"  分段标记 {segment_marker} 是【纯文本分隔符】（由两个字符组成：反斜杠 \\ 和字母 c），【不是】 Markdown 代码块的语言标记！",
            "  - 严禁输出 ```c、```、```python 等反引号围栏（```）作为分段符，那会破坏前端 Markdown 渲染。",
            "  - 代码示例请正常使用成对的反引号围栏（```语言 ... ```），但分段时【只能】用 \\c 单独成行。",
            f"  示例：",
            f"  第一段：讲解概念A{segment_marker}",
            f"  第二段：给出公式和示例{segment_marker}",
            f"  第三段：总结并提问",
        ])
    parts.append("\n".join(teach_guide))

    # 注入标准试卷（若课程已上传）：教学讲解覆盖试卷知识点，课程目标导向"答对试卷"
    try:
        _exam_paper = load_exam_paper(lesson_folder)
        _exam_qs = _exam_paper.get("questions") or []
        if _exam_qs:
            _type_cn = {"single": "单选", "multiple": "多选", "boolean": "判断", "fill": "填空", "essay": "简答"}
            _paper_lines = [
                "【期末标准试卷（课程结束时将以此卷测验）】",
                "以下题目是期末测验的原题，请把它们的知识点融入教学讲解、示例与练习，学生最终必须能答对这份试卷。",
            ]
            for i, q in enumerate(_exam_qs):
                _paper_lines.append(f"{i + 1}. [{_type_cn.get(str(q.get('type')), '题')}] {str(q.get('question') or '')}（标准答案：{str(q.get('answer') or '')}）")
            parts.append("\n".join(_paper_lines))
    except Exception:
        pass

    return "\n\n".join(parts)


_CONTEXT_HISTORY_BUDGET = 12000   # 对话历史总预算（字符），超出则丢弃更旧消息
_CONTEXT_MSG_MAX = 2500           # 单条历史消息上限（字符），超出则截断并标注
_CONTEXT_LAST_N = 10              # 最多保留的对话轮数


def _timestamp_label(entry: Dict[str, Any]) -> str:
    """从会话条目提取时间戳（2026-08-19T22:35:20+08:00 → 22:35）。

    给喂给模型的历史消息补上时间线信息，让模型感知每轮对话发生在什么时候，
    修复模型"读取时间"错误（不知道此刻/上一轮是几点）。
    """
    ts = entry.get("timestamp") or ""
    m = re.search(r"\d{4}-\d{2}-\d{2}[T ](\d{2}:\d{2})", str(ts))
    return m.group(1) if m else ""


def _compact_history(history, budget=_CONTEXT_HISTORY_BUDGET, per_msg=_CONTEXT_MSG_MAX, last_n=_CONTEXT_LAST_N):
    """上下文压缩：避免对话历史撑爆模型上下文窗口。

    - 只保留最近 last_n 轮；
    - 单条消息超过 per_msg 字符时截断（保留开头，附压缩说明）；
    - 从最新往旧累积，总长超过 budget 后丢弃更旧消息（至少保留最新一条）。
    - 注意：不再给历史消息补时间戳前缀。此前补的「[时间 xx:xx]」会被模型
      当作格式复述进正文，造成"回复里出现时间戳"的 bug；
      当前时间已由系统提示词注入，模型无需从历史里读取时间线。
    系统提示词（含教案+资料）本身较大，本地小模型（如 qwen3:4b）上下文有限，
    此预算保证 messages 总量可控，避免超限导致的超时/截断/报错。
    """
    if not history:
        return []
    selected: List[Dict[str, str]] = []
    total = 0
    for entry in reversed(history[-last_n:]):
        role = entry.get("role")
        content = entry.get("content")
        if role not in {"user", "assistant"} or not content:
            continue
        content = str(content)
        orig_len = len(content)
        if orig_len > per_msg:
            content = content[:per_msg] + f"\n（本条原文 {orig_len} 字过长，已压缩至 {per_msg} 字）"
        total += len(content)
        if total > budget and selected:
            break
        selected.append({"role": role, "content": content})
    selected.reverse()
    return selected


def _build_chat_messages(
    prompt: str, lesson_folder: str | None, history: List[Dict[str, str]] | None
) -> List[Dict[str, str]]:
    """Build OpenAI-compatible messages array.

    提示词链路：
    1. messages[0] 必定是 role=system，内容来自 build_system_prompt（身份+角色+课程资料）
    2. 附加最近 N 轮 user/assistant 对话历史（经 _compact_history 压缩 + 补时间戳，避免超上下文）
    3. 末尾追加本次用户输入（去除重复：若 history 最后一条就是本次 prompt 则跳过）
    """
    system_prompt = build_system_prompt(lesson_folder)
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    # 去重判断用原始 history（未加时间戳前缀），避免时间戳破坏 content 相等比较
    if history and str(history[-1].get("content", "")).strip() == prompt:
        history = history[:-1]
    for entry in _compact_history(history):
        messages.append({"role": entry.get("role"), "content": entry.get("content")})
    messages.append({"role": "user", "content": prompt})
    return messages


def _strip_thinking_residue(text: str) -> str:
    """剥离模型思考残留（qwen3 等即使 think=false 也可能输出 <|thinking|> / </think> / [thinking] 块）。

    保留正式回答，避免思考内容被当作正文朗读/展示。
    """
    if not text:
        return text
    # 常见思考包裹：<|thinking|>...</|thinking|>、<thinking>...</thinking>、[thinking]...[/thinking]、
    # [think]...[/think]，以及 qwen3 think=false 时的 </think> 单独闭合残留
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
    # 剥离模型复述的时间戳前缀（[时间 22:35]）——历史消息曾带该前缀注入，
    # 模型偶尔会原样复述，导致正文出现时间戳
    text = re.sub(r"(?:^|\n)\s*\[时间\s+\d{1,2}:\d{2}\]\s*", "\n", text)
    return text.strip()


def local_ollama_reply(prompt: str, lesson_folder: str | None = None, history: List[Dict[str, str]] | None = None, long_mode: bool = False) -> str:
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
                    # 依次尝试：配置名 → qwen3 → qwen2.5 → qwen2.5vl → qwen2.5-coder
                    # 前缀匹配（"qwen2.5" 要能命中 "qwen2.5:7b"），且带 ":" 避免误吞 qwen2.5vl/coder
                    for fallback in ["qwen3", "qwen2.5", "qwen2.5vl", "qwen2.5-coder"]:
                        match = next(
                            (n for key, n in normalized.items() if key.startswith(fallback + ":")),
                            None,
                        )
                        if match:
                            print(f"[ollama] 模型 '{model}' 不存在，自动改用 '{match}'", flush=True)
                            model = match
                            break
                else:
                    model = normalized[model.lower()]  # 统一为实际大小写
    except Exception as exc:
        print(f"[ollama] 获取模型列表失败: {exc}", flush=True)

    messages = _build_chat_messages(prompt, lesson_folder, history)

    # 统一从配置读取生成参数（避免魔法数；分课后 system prompt 较长，默认 16384）
    # 长文本模式（写作文/长代码）提升输出上限，防止生成被截断
    base_predict = int(cfg.get("ollama_num_predict", 600) or 600)
    options: Dict[str, Any] = {
        "temperature": float(cfg.get("ollama_temperature", 0.7) or 0.7),
        "num_ctx": int(cfg.get("ollama_num_ctx", 16384) or 16384),
        "num_predict": 2500 if long_mode else base_predict,
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
                # qwen3 系列默认开启 thinking：4B 小模型思考会消耗大量时间，
                # 且 ollama 在 think 模式下返回的 content 可能为空（实测 11s 空转）。
                # 显式关闭思考，保证能拿到可用的正文回复。
                "think": False,
                "options": options,
            },
            timeout=120,
        )
        if response.ok:
            data = response.json()
            content = (data.get("message", {}).get("content") or data.get("response", "")).strip()
            # 清理模型思考残留（think=false 时部分 qwen3 仍会输出 <|thinking|> / </think>）
            # 与开头独白（"首先，用户要求我…"），避免学生看到 AI 的内心独白
            content = _strip_thinking_residue(content)
            content = _strip_thinking_lead(content)
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
                "think": False,
                "options": options,
            },
            timeout=120,
        )
        if response.ok:
            data = response.json()
            return _strip_thinking_lead(_strip_thinking_residue((data.get("response") or "").strip()))
        print(f"[ollama] /api/generate HTTP {response.status_code}: {response.text[:200]}", flush=True)
    except Exception as exc:
        print(f"[ollama] /api/generate 异常: {exc}", flush=True)

    # 两条路径都失败：返回空串，让 api_chat 自动回退到云端 LLM
    return ""


def cloud_llm_reply(prompt: str, lesson_folder: str | None = None, history: List[Dict[str, str]] | None = None, long_mode: bool = False) -> str:
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
        # 对话链路用独立的 chat_enable_search（默认关），不复用备课的 enable_search——
        # 联网搜索会显著拖慢对话响应（备课实测 240s+，对话 120s 超时导致"老师不回复"）
        enable_search = bool(cfg.get("chat_enable_search", False))

    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    messages = _build_chat_messages(prompt, lesson_folder, history)
    # 长文本模式（写作文/长代码）提升输出上限，防止生成被截断
    base_tokens = int(cfg.get("chat_max_tokens", 600) or 600)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        # 控制回复长度：对话回复保持精炼（备课/测验另有独立配置）
        "max_tokens": 3000 if long_mode else base_tokens,
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


def cloud_llm_reply_stream(prompt: str, lesson_folder: str | None = None, history: List[Dict[str, str]] | None = None, long_mode: bool = False):
    """流式版本：yield 每个 token（供 api_chat 真流式预览）。

    配置选择逻辑与 cloud_llm_reply 保持一致（chat_* 优先，回退 cloud_*）。
    失败/未配置时 yield 空（上层会回退到备用通道或兜底文案）。
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
            return
        model = (cfg.get("cloud_model") or cfg.get("siliconflow_model") or "deepseek-ai/DeepSeek-V3").strip()
        base_url = (cfg.get("cloud_base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
        enable_search = bool(cfg.get("chat_enable_search", False))

    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    messages = _build_chat_messages(prompt, lesson_folder, history)
    base_tokens = int(cfg.get("chat_max_tokens", 600) or 600)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 3000 if long_mode else base_tokens,
    }
    if enable_search:
        payload["enable_search"] = True

    try:
        print(f"[AI-REQUEST] 对话请求(流式) → 云端: {url} | model: {model}", flush=True)
        with requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={**payload, "stream": True},
            timeout=120,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            # 强制 UTF-8：部分 API 响应头无 charset 时 requests 默认按 ISO-8859-1
            # 解码，UTF-8 中文会变成 mojibake（如“看起来”→ççèµ·æ¥）
            resp.encoding = "utf-8"
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data:"):
                    continue
                data = raw[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    j = json.loads(data)
                    delta = (j.get("choices") or [{}])[0].get("delta") or {}
                    chunk = delta.get("content") or ""
                    if chunk:
                        yield chunk
                except (ValueError, json.JSONDecodeError, IndexError):
                    continue
    except Exception as exc:
        print(f"[STREAM] 云端流式异常: {type(exc).__name__}: {exc}", flush=True)


def local_ollama_reply_stream(prompt: str, lesson_folder: str | None = None, history: List[Dict[str, str]] | None = None, long_mode: bool = False):
    """流式版本：yield 每个 token（Ollama /api/chat NDJSON 格式）。

    配置选择/模型名容错逻辑与 local_ollama_reply 保持一致。
    失败/未配置时 yield 空（上层回退备用通道）。
    """
    cfg = load_config()
    base_url = (cfg.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
    model = (cfg.get("ollama_model") or "qwen2.5:7b").strip()
    if not cfg.get("enable_local_ollama", True):
        return

    # 模型名容错：自动匹配 Ollama 实际可用模型（与 local_ollama_reply 相同）
    try:
        tags_resp = requests.get(f"{base_url}/api/tags", timeout=8)
        if tags_resp.ok:
            available = [m.get("name", "") for m in (tags_resp.json().get("models") or [])]
            if available:
                normalized = {n.lower(): n for n in available}
                if model.lower() not in normalized:
                    for fallback in ["qwen3", "qwen2.5", "qwen2.5vl", "qwen2.5-coder"]:
                        match = next(
                            (n for key, n in normalized.items() if key.startswith(fallback + ":")),
                            None,
                        )
                        if match:
                            print(f"[ollama] 模型 '{model}' 不存在，自动改用 '{match}'", flush=True)
                            model = match
                            break
                else:
                    model = normalized[model.lower()]
    except Exception as exc:
        print(f"[ollama] 获取模型列表失败: {exc}", flush=True)

    messages = _build_chat_messages(prompt, lesson_folder, history)
    base_predict = int(cfg.get("ollama_num_predict", 600) or 600)
    options: Dict[str, Any] = {
        "temperature": float(cfg.get("ollama_temperature", 0.7) or 0.7),
        "num_ctx": int(cfg.get("ollama_num_ctx", 16384) or 16384),
        "num_predict": 2500 if long_mode else base_predict,
    }

    try:
        print(f"[AI-REQUEST] 对话请求(流式) → Ollama: {base_url}/api/chat | model: {model}", flush=True)
        with requests.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "think": False,
                "options": options,
            },
            timeout=120,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            # 强制 UTF-8：Ollama 响应可能无 charset，requests 会默认按 ISO-8859-1 解码导致中文乱码
            resp.encoding = "utf-8"
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    j = json.loads(line)
                    chunk = (j.get("message") or {}).get("content") or j.get("response") or ""
                    if chunk:
                        yield chunk
                    if j.get("done"):
                        break
                except (ValueError, json.JSONDecodeError):
                    continue
    except Exception as exc:
        print(f"[ollama] 流式异常: {exc}", flush=True)


def direct_llm_reply(message: str, system: str = "") -> str:
    """直接与 LLM 对话（不注入课程上下文），供前端 /ask 斜杠命令使用。

    优先本地 Ollama，失败/关闭时回退到云端 OpenAI 兼容 API。
    """
    if not message:
        return ""
    cfg = load_config()
    sys_content = (system or "你是一个乐于助人的 AI 助手。").strip()
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": sys_content},
        {"role": "user", "content": message},
    ]

    # 1) 本地 Ollama
    if cfg.get("enable_local_ollama", True):
        base_url = (cfg.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
        model = (cfg.get("ollama_model") or "qwen2.5:7b").strip()
        try:
            resp = requests.post(
                f"{base_url}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": float(cfg.get("ollama_temperature", 0.7) or 0.7),
                        "num_ctx": int(cfg.get("ollama_num_ctx", 16384) or 16384),
                        "num_predict": int(cfg.get("ollama_num_predict", 1024) or 1024),
                    },
                },
                timeout=120,
            )
            if resp.ok:
                content = (resp.json().get("message", {}).get("content") or "").strip()
                if content:
                    return content
            print(f"[ask] ollama HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
        except Exception as exc:
            print(f"[ask] ollama 异常: {exc}", flush=True)

    # 2) 云端 OpenAI 兼容 API（chat_* 优先，回退 cloud_*）
    chat_key = (cfg.get("chat_api_key") or "").strip()
    chat_model = (cfg.get("chat_model") or "").strip()
    chat_base = (cfg.get("chat_base_url") or "").rstrip("/").strip()
    if chat_key and chat_model and chat_base:
        key, model, base_url = chat_key, chat_model, chat_base
    else:
        key = (cfg.get("cloud_api_key") or cfg.get("siliconflow_api_key") or "").strip()
        if not key:
            return ""
        model = (cfg.get("cloud_model") or cfg.get("siliconflow_model") or "deepseek-ai/DeepSeek-V3").strip()
        base_url = (cfg.get("cloud_base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
    url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": int(cfg.get("chat_max_tokens", 600) or 600),
    }
    if bool(cfg.get("enable_search", False)):
        payload["enable_search"] = True
    try:
        print(f"[AI-REQUEST] 直接对话 → 云端: {url} | model: {model}", flush=True)
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if resp.ok:
            content = resp.json().get("choices", [{}])[0].get("message", {}).get("content") or ""
            return str(content).strip()
        print(f"[ask] 云端 HTTP {resp.status_code}: {resp.text[:200]}", flush=True)
    except Exception as exc:
        print(f"[ask] 云端异常: {exc}", flush=True)
    return ""


@app.route("/api/llm/chat", methods=["POST"])
def api_llm_direct_chat():
    """直接与 LLM 对话（不注入课程上下文），供前端 /ask 命令使用。"""
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    system = (payload.get("system") or "").strip()
    if not message:
        return jsonify({"error": "message 不能为空"}), 400
    content = direct_llm_reply(message, system)
    if not content:
        return jsonify({"error": "LLM 不可用（本地 Ollama 与云端均请求失败）"}), 502
    return jsonify({"content": content})


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


# ============== 聊天附件上传 + 识图模型（可选） ==============
CHAT_UPLOAD_DIR = BASE_DIR / "static" / "uploads" / "chat"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
TEXT_EXT = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".csv", ".log",
    ".html", ".htm", ".css", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".conf",
    ".sh", ".bat", ".ps1", ".c", ".cpp", ".h", ".java", ".go", ".rs", ".sql",
    ".ipynb", ".toml", ".env", ".gitignore",
}
_MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
}


def _guess_mime_by_ext(ext: str) -> str:
    return _MIME_BY_EXT.get((ext or "").lower(), "image/jpeg")


def _load_image_b64(image_url: str) -> str | None:
    """读取图片（本地 /static/ 或远程 URL）→ 压缩到最长边 1024 并转 JPEG base64。

    压缩避免超大分辨率图片（如 4K 贴图）超出模型显存/内存。
    返回 None 表示读取失败。
    """
    mime = "image/jpeg"
    raw: bytes | None = None
    if image_url.startswith("/static/"):
        rel = image_url.split("?", 1)[0][len("/static/"):]
        local_path = BASE_DIR / "static" / rel
        if not local_path.exists():
            print(f"[vision] 本地图片不存在: {local_path}", flush=True)
            return None
        mime = _guess_mime_by_ext(local_path.suffix)
        raw = local_path.read_bytes()
    elif image_url.startswith("data:"):
        # 已是 data URL，直接透传
        return image_url.split(",", 1)[1] if "," in image_url else None
    else:
        try:
            resp = requests.get(image_url, timeout=60)
            if not resp.ok:
                print(f"[vision] 远程图片获取失败 HTTP {resp.status_code}", flush=True)
                return None
            raw = resp.content
        except Exception as exc:
            print(f"[vision] 远程图片获取异常: {type(exc).__name__}: {exc}", flush=True)
            return None
    try:
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(raw))
        img.thumbnail((1024, 1024), Image.LANCZOS)
        buf = _io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except ImportError:
        # 无 PIL 时原样 base64
        return base64.b64encode(raw).decode("ascii")
    except Exception as exc:
        print(f"[vision] 图片处理失败: {type(exc).__name__}: {exc}", flush=True)
        try:
            return base64.b64encode(raw).decode("ascii")
        except Exception:
            return None


def vision_describe(image_url: str, name: str = "") -> str:
    """用识图模型描述图片内容（可选功能）。

    - 本地 Ollama（base_url 含 127.0.0.1/localhost）→ 走原生 /api/chat（images 数组）
    - 其他 OpenAI 兼容端点 → /chat/completions（image_url data URL）
    图片统一压缩到最长边 1024，避免超大图超出模型内存。
    未启用或配置不完整时返回空字符串（调用方按“无法识别”处理）。
    """
    cfg = load_config()
    if not cfg.get("vision_enabled", False):
        return ""
    key = (cfg.get("vision_api_key") or "").strip()
    base_url = (cfg.get("vision_base_url") or "").rstrip("/").strip()
    model = (cfg.get("vision_model") or "").strip()
    if not key or not base_url or not model:
        print("[vision] 识图模型未配置完整（需 api_key + base_url + model），跳过识图", flush=True)
        return ""
    img_b64 = _load_image_b64(image_url)
    if not img_b64:
        return ""
    is_ollama = "11434" in base_url or "127.0.0.1" in base_url or "localhost" in base_url
    try:
        if is_ollama:
            # Ollama 原生多模态格式
            api_url = base_url
            if api_url.endswith("/v1"):
                api_url = api_url[:-3]
            print(f"[vision] 识图请求(Ollama) → {api_url}/api/chat | model: {model}", flush=True)
            response = requests.post(
                f"{api_url}/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {"role": "user", "content": "请用中文详细描述这张图片的内容，包括主体、文字、图表等关键信息。", "images": [img_b64]}
                    ],
                    "stream": False,
                },
                timeout=180,
            )
            if not response.ok:
                print(f"[vision] 识图失败(Ollama) HTTP {response.status_code}: {response.text[:300]}", flush=True)
                return ""
            content = (response.json().get("message") or {}).get("content") or ""
            return str(content).strip() or ""
        # OpenAI 兼容格式
        data_url = f"data:image/jpeg;base64,{img_b64}"
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        print(f"[vision] 识图请求 → {url} | model: {model}", flush=True)
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "请用中文详细描述这张图片的内容，包括主体、文字、图表等关键信息。"},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    }
                ],
                "max_tokens": 800,
                "temperature": 0.2,
            },
            timeout=90,
        )
        if not response.ok:
            print(f"[vision] 识图失败 HTTP {response.status_code}: {response.text[:300]}", flush=True)
            return ""
        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content") or ""
        return str(content).strip() or ""
    except Exception as exc:
        print(f"[vision] 识图异常: {type(exc).__name__}: {exc}", flush=True)
        return ""


@app.route("/api/upload_file", methods=["POST"])
def api_upload_file():
    """聊天附件上传：保存到 /static/uploads/chat/。

    返回 {ok, url, name, type, size, content}：
      - 图片 → type=image
      - 文本类（py/md/txt/json 等）→ type=file 且附带前 50KB 文本内容
      - 其他 → type=file
    """
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "message": "未收到文件"}), 400
    safe = secure_filename(f.filename)
    if not safe:
        return jsonify({"ok": False, "message": "非法文件名"}), 400
    ext = Path(safe).suffix.lower()
    data = f.read(MAX_UPLOAD_SIZE + 1)
    if len(data) > MAX_UPLOAD_SIZE:
        return jsonify({"ok": False, "message": f"文件过大，最大支持 {MAX_UPLOAD_SIZE // 1024 // 1024}MB"}), 400
    CHAT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_name = f"{int(time.time() * 1000)}_{safe}"
    file_path = CHAT_UPLOAD_DIR / file_name
    file_path.write_bytes(data)
    att_type = "image" if ext in IMAGE_EXT else "file"
    content = ""
    if ext in TEXT_EXT:
        try:
            content = data[:50 * 1024].decode("utf-8", errors="replace")
        except Exception:
            content = ""
    elif ext == ".pdf":
        # PDF 附件：提取前 20 页文本随消息发给模型（扫描版自动走 OCR 兜底）
        try:
            pdf_text = _read_pdf_text(str(file_path), 1, 20)
            content = pdf_text[:50 * 1024]
        except Exception:
            content = ""
    print(f"[upload_file] 已保存 {safe} → {file_path.name} ({len(data)} bytes, type={att_type})", flush=True)
    return jsonify({
        "ok": True,
        "url": f"/static/uploads/chat/{file_name}",
        "name": safe,
        "type": att_type,
        "size": len(data),
        "content": content,
    })


def local_tts_audio(text: str) -> str | None:
    cfg = load_config()
    # 语音总开关：关闭后自动/手动朗读均不发声
    if not cfg.get("voice_enabled", True):
        return None
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


@app.route("/api/tts/speak", methods=["POST"])
def api_tts_speak():
    """手动朗读：前端 🔊 按钮调用，走与自动朗读完全一致的 TTS 链路。

    修复"前端配置空 TTS API 仍能朗读"：旧实现直接调用浏览器 speechSynthesis，
    完全不经过后端、不校验任何配置。现在统一走后端，未配置任何语音服务时返回错误。
    """
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    cfg = load_config()
    if not cfg.get("voice_enabled", True):
        return jsonify({"error": "语音朗读已关闭，请在设置中开启"}), 400
    audio_url = local_tts_audio(_tts_safe_text(text)[:500])
    if not audio_url:
        return jsonify({"error": "未配置可用的语音服务（云端 API Key 为空且本地 TTS 未启用）"}), 400
    return jsonify({"ok": True, "audio_url": audio_url})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    return "", 204


@app.route("/api/config", methods=["GET"])
def api_config_get():
    cfg = load_config()
    cfg["ocr_available"] = pdf_ocr_available()
    return jsonify(cfg)


# ============================
# 配置校验：类型/范围/必填联动，返回中文错误提示
# ============================
# 数值字段范围约束（字段名 -> (最小值, 最大值, 中文名)）
_NUMERIC_BOUNDS = {
    "font_size": (12, 20, "全局正文字号"),
    "sidebar_width": (25, 60, "聊天侧栏宽度"),
    "portrait_pos_x": (0, 100, "立绘水平位置"),
    "portrait_pos_y": (0, 100, "立绘垂直位置"),
    "portrait_scale": (0.1, 0.8, "立绘缩放"),
    "portrait_float_amplitude": (0, 40, "立绘浮动幅度"),
    "ollama_num_ctx": (512, 131072, "上下文窗口"),
    "ollama_temperature": (0, 2.0, "温度"),
    "ollama_num_predict": (1, 100000, "最大生成长度"),
    "segment_max_lines": (1, 30, "分段最大行数"),
}
# 布尔字段（前端可能传字符串，统一兜底）
_BOOL_FIELDS = {
    "enable_local_ollama", "auto_play_tts", "voice_enabled",
    "tts_enabled", "tts_cloud_enabled", "vision_enabled",
    "enable_search", "chat_enable_search", "segment_enabled",
    "portrait_float_enabled", "ocr_enabled", "interactive_prep_enabled",
}


def _validate_config_payload(payload: Dict[str, Any]) -> List[str]:
    """校验配置提交，返回错误列表（空 = 校验通过）。不修改 payload。"""
    errors: List[str] = []
    if not isinstance(payload, dict):
        return ["配置数据格式错误：应为 JSON 对象"]
    p = payload

    # 1) 数值字段：类型 + 范围
    for key, (lo, hi, label) in _NUMERIC_BOUNDS.items():
        if key not in p or p[key] is None or p[key] == "":
            continue
        try:
            val = float(p[key])
        except (TypeError, ValueError):
            errors.append(f"{label}（{key}）必须是数字，当前为：{p[key]!r}")
            continue
        if val < lo or val > hi:
            errors.append(f"{label}（{key}）超出范围 {lo} ~ {hi}，当前为：{p[key]!r}")

    # 2) 布尔字段：宽容接受 true/false/1/0/字符串
    for key in _BOOL_FIELDS:
        if key not in p or p[key] is None:
            continue
        v = p[key]
        if isinstance(v, str):
            if v.strip().lower() in ("true", "1", "yes", "on"):
                p[key] = True
            elif v.strip().lower() in ("false", "0", "no", "off"):
                p[key] = False
            else:
                errors.append(f"字段 {key} 应为 true/false，当前为：{v!r}")

    # 3) 必填联动：只拦截"真的无法工作"的组合。
    #    云端/对话/识图的 model 后端都有默认回退（如 deepseek-ai/DeepSeek-V3），
    #    填了 Key 缺 Model 仍可正常使用，因此不阻断保存，避免误报。
    if p.get("enable_local_ollama") and not str(p.get("ollama_model") or "").strip():
        errors.append("已开启本地 Ollama，请填写本地模型名")
    if p.get("tts_cloud_enabled") and not str(p.get("tts_cloud_base_url") or "").strip():
        errors.append("已开启云端 TTS，请填写云端 TTS 接口地址")
    if p.get("tts_provider") == "cloud" and not str(p.get("tts_cloud_base_url") or "").strip():
        errors.append("TTS 提供方为云端，但未填写云端 TTS 接口地址")
    return errors


@app.route("/api/config", methods=["POST"])
def api_config_set():
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"status": "error", "errors": ["配置数据格式错误：应为 JSON 对象"]}), 400
    errors = _validate_config_payload(payload)
    if errors:
        # 校验失败不落盘，返回中文错误（前端自动保存时展示到状态条）
        return jsonify({"status": "error", "errors": errors}), 400
    config = save_config(payload)
    # 同步 OCR 总开关（运行时生效，无需重启）
    set_ocr_enabled(bool(config.get("ocr_enabled", True)))
    return jsonify({"status": "ok", "config": config})


# 默认 Live2D 模型 URL（与前端 templates/index.html 中的硬编码保持一致）
_DEFAULT_LIVE2D_MODEL_URL = "/static/models/my_teacher/female_01Arkit_6.model3.json"


def _resolve_live2d_model_path(model_url: str) -> Path | None:
    """把 URL 形式的模型路径解析到磁盘路径。"""
    if not model_url:
        model_url = _DEFAULT_LIVE2D_MODEL_URL
    # 去掉 query string
    clean = model_url.split("?", 1)[0]
    if not clean.startswith("/static/"):
        return None
    rel = clean[len("/static/"):]
    return (BASE_DIR / "static" / rel).resolve()


@app.route("/api/live2d/model_info", methods=["GET"])
def api_live2d_model_info():
    """返回当前 Live2D 模型可用的 expressions 和 motions，供前端 emotionMap 配置用。"""
    model_url = (request.args.get("url") or _DEFAULT_LIVE2D_MODEL_URL).strip()
    model_path = _resolve_live2d_model_path(model_url)
    if not model_path or not model_path.exists():
        return jsonify({"error": "model not found", "url": model_url}), 404
    try:
        data = json.loads(model_path.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"error": f"parse failed: {e}"}), 500
    fr = data.get("FileReferences", {}) or {}
    expressions: list[dict] = []
    for idx, e in enumerate(fr.get("Expressions", []) or []):
        expressions.append({
            "index": idx,
            "name": e.get("Name") or f"expression{idx}",
            "file": e.get("File") or "",
        })
    motions: dict[str, list[dict]] = {}
    motions_raw = fr.get("Motions", {}) or {}
    for group, items in motions_raw.items():
        motions[group] = [
            {"index": i, "file": (it.get("File") or "")} for i, it in enumerate(items or [])
        ]
    hit_areas: list[dict] = []
    for h in fr.get("HitAreas", []) or []:
        hit_areas.append({"name": h.get("Name", ""), "id": h.get("Id", "")})
    return jsonify({
        "url": model_url,
        "expressions": expressions,
        "motions": motions,
        "hit_areas": hit_areas,
        # Open-LLM-VTuber 默认情绪映射（与文档一致）
        "default_emotion_map": {
            "neutral": 0, "happy": 3, "sad": 1, "angry": 2, "surprised": 3,
            "fear": 1, "disgust": 2, "smirk": 3, "think": 0,
        },
    })


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

# 模型上传大小限制：100MB（Live2D 模型含贴图/动作资源）
MAX_MODEL_UPLOAD_SIZE = 100 * 1024 * 1024
# 自定义模型保存目录
UPLOAD_MODELS_DIR = BASE_DIR / "static" / "models" / "uploads"


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


# ============== 单元图片资源（每个单元文件夹下的图片） ==============

@app.route("/api/lesson/<path:lesson_folder>/unit-images", methods=["GET"])
def api_lesson_unit_images(lesson_folder: str):
    """列出当前激活单元对应的图片资源。
    图片存放位置：
      - 优先：lessons/<folder>/units/<unit_index>/images/
      - 兜底：lessons/<folder>/images/  （旧格式）
    """
    if not lesson_folder or "/" in lesson_folder or "\\" in lesson_folder or ".." in lesson_folder:
        return jsonify({"ok": False, "message": "非法的课程名"}), 400

    lesson_dir = LESSONS_DIR / lesson_folder
    config_path = lesson_dir / "config.json"
    cur_idx = 0
    cur_title = ""
    if config_path.exists():
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            units = cfg.get("units") or []
            if units:
                progress_path = lesson_dir / "progress.json"
                if progress_path.exists():
                    try:
                        prog = json.loads(progress_path.read_text(encoding="utf-8"))
                        cur_idx = int(prog.get("current_unit", 0) or 0)
                    except Exception:
                        cur_idx = 0
                if 0 <= cur_idx < len(units):
                    cur_title = units[cur_idx].get("title", "")
        except Exception:
            pass

    # 候选目录
    candidates = [
        lesson_dir / "units" / f"unit_{cur_idx + 1}" / "images",
        lesson_dir / "units" / str(cur_idx) / "images",
        lesson_dir / "images",  # 兜底
    ]
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

    items = []
    seen = set()
    for d in candidates:
        if not d.exists() or not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if not f.is_file():
                continue
            if f.suffix.lower() not in image_exts:
                continue
            if f.name in seen:
                continue
            seen.add(f.name)
            url = f"/api/lesson/{lesson_folder}/asset/{f.name}"
            items.append({
                "filename": f.name,
                "url": url,
                "title": f.stem,
                "folder": str(d.relative_to(lesson_dir)),
            })

    return jsonify({
        "ok": True,
        "lesson_folder": lesson_folder,
        "unit_index": cur_idx,
        "unit_title": cur_title,
        "images": items,
        "total": len(items),
    })


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


# ============== 自定义 Live2D 模型上传 ==============

# 浏览器 WebGL 通用最大纹理尺寸（移动端 4096，桌面 8192）；
# 取较小端做安全阈值，避免某些低端显卡加载失败。
_LIVE2D_TEX_MAX_DIM = 4096

def _downscale_oversized_textures(folder: Path) -> None:
    """递归扫描模型目录，把 >4096px 的 PNG/JPG 缩放到 4096 边长，防止浏览器加载失败导致模型发黑。"""
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
    except ImportError:
        return  # 未安装 Pillow 跳过（不阻塞上传）
    target = _LIVE2D_TEX_MAX_DIM
    n_done = 0
    for f in folder.rglob("*"):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in (".png", ".jpg", ".jpeg"):
            continue
        try:
            with Image.open(f) as img:
                w, h = img.size
            if max(w, h) <= target:
                continue
            with Image.open(f) as img:
                mode = img.mode if img.mode in ("RGB", "RGBA") else ("RGBA" if ext == ".png" else "RGB")
                if img.mode != mode:
                    img = img.convert(mode)
                img = img.resize((target, target), Image.LANCZOS)
                save_kwargs = {"optimize": True}
                if ext in (".jpg", ".jpeg"):
                    save_kwargs["quality"] = 88
                img.save(f, **save_kwargs)
                n_done += 1
        except Exception as e:
            print(f"[upload_model] 纹理缩放跳过 {f.name}: {e}")
    if n_done:
        print(f"[upload_model] 已缩放 {n_done} 张超大纹理到 {target}px")

@app.route("/api/upload_model", methods=["POST"])
def api_upload_model():
    """上传自定义 Live2D 模型。

    支持两种格式：
    - .zip：内含 .model3.json 及其贴图/动作资源，自动解压。
    - .model3.json：单个模型配置文件（不含资源，一般配合完整目录使用）。
    成功后更新配置 live2d_model_url，前端刷新即加载新模型。
    """
    if "model" not in request.files:
        return jsonify({"ok": False, "message": "未选择文件"}), 400
    file = request.files["model"]
    if not file or not file.filename:
        return jsonify({"ok": False, "message": "文件名为空"}), 400

    file.seek(0, 2)
    file_size = file.tell()
    file.seek(0)
    if file_size > MAX_MODEL_UPLOAD_SIZE:
        return jsonify({
            "ok": False,
            "message": f"文件过大（{file_size // 1024 // 1024}MB），最大支持 {MAX_MODEL_UPLOAD_SIZE // 1024 // 1024}MB",
        }), 400

    raw_name = os.path.basename(file.filename)
    safe_base = re.sub(r"[^A-Za-z0-9_.-]", "_", raw_name)
    ext = os.path.splitext(safe_base)[1].lower()
    # 支持：zip 模型包 / Cubism3+ 的 .model3.json / Cubism2 的 model.json / .moc3 / .moc
    if ext not in (".zip", ".json", ".moc3", ".moc"):
        return jsonify({"ok": False, "message": "仅支持 .zip（模型包）或 .model3.json / .moc3（Cubism 3/4/5）、model.json / .moc（Cubism 2.1）"}), 400

    UPLOAD_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    folder = UPLOAD_MODELS_DIR / f"{int(time.time())}_{safe_base.split('.')[0]}"
    folder.mkdir(parents=True, exist_ok=True)

    try:
        if ext == ".zip":
            zip_path = folder / "model.zip"
            file.save(zip_path)
            with zipfile.ZipFile(zip_path) as zf:
                members = []
                for m in zf.namelist():
                    norm = m.replace("\\", "/")
                    if norm.startswith("__MACOSX") or norm.endswith("/"):
                        continue
                    if norm.startswith("/") or norm.split("/").count("..") or ".." in norm.split("/"):
                        continue  # 跳过绝对路径 / 路径穿越成员
                    members.append(m)
                # Cubism 3/4/5：*.model3.json；Cubism 2.1：model.json / *.model.json
                model3_candidates = [m for m in members if m.lower().endswith(".model3.json")]
                model2_candidates = [m for m in members
                                     if m.lower().endswith("model.json") and not m.lower().endswith("model3.json")]
                if not model3_candidates and not model2_candidates:
                    return jsonify({"ok": False, "message": "zip 内未找到 .model3.json（Cubism 3/4/5）或 model.json（Cubism 2.1）"}), 400
                for m in members:
                    dest = folder / m.replace("\\", "/")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(m) as src, open(dest, "wb") as out:
                        out.write(src.read())
            zip_path.unlink(missing_ok=True)
            # 大纹理（>4096）自动缩放到 4096：避免浏览器 WebGL max texture size 限制导致贴图加载失败 → 模型发黑
            _downscale_oversized_textures(folder)
            rel_path = (model3_candidates or model2_candidates)[0].replace("\\", "/")
            model_url = f"/static/models/uploads/{folder.name}/{rel_path}"
        else:
            # 单文件：按原文件名保存（model3.json / model.json / .moc3 / .moc）
            # 注意：moc3/moc 只是资源，一般需配合完整模型包；此处原样保存以便同目录资源可用
            dest = folder / safe_base
            file.save(dest)
            model_url = f"/static/models/uploads/{folder.name}/{safe_base}"
            # 单文件大概率缺少贴图/动作，明确警告
            warning = ("⚠️ 单独上传的模型文件无法独立显示（缺少贴图/动作资源）。"
                       "请把整个模型文件夹压缩成 .zip（包含 model3.json、moc3、贴图目录）再上传。")
            save_config({"live2d_model_url": model_url})
            return jsonify({"ok": True, "url": model_url, "warning": warning})
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        return jsonify({"ok": False, "message": f"模型处理失败: {exc}"}), 500

    save_config({"live2d_model_url": model_url})
    return jsonify({"ok": True, "url": model_url})


@app.route("/api/reset_model", methods=["POST"])
def api_reset_model():
    """恢复默认内置模型，并清理所有已上传的自定义模型目录。"""
    if UPLOAD_MODELS_DIR.exists():
        for d in UPLOAD_MODELS_DIR.iterdir():
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
    save_config({"live2d_model_url": _DEFAULT_LIVE2D_MODEL_URL})
    return jsonify({"ok": True, "url": _DEFAULT_LIVE2D_MODEL_URL})


# ============== 终端：代码执行与结果记录 ==============

# 语言 → 解释器命令映射（仅使用本机可用解释器；扩展语言缺失时后端会给出友好提示）
_EXEC_LANG_MAP = {
    "python": [sys.executable, "-c"],
    "python3": [sys.executable, "-c"],
    "py": [sys.executable, "-c"],
    "shell": ["cmd", "/c"],
    "sh": ["powershell", "-NoProfile", "-Command"],
    "powershell": ["powershell", "-NoProfile", "-Command"],
    "js": ["node", "-e"],
    "javascript": ["node", "-e"],
    "node": ["node", "-e"],
    # —— 扩展脚本语言（单次执行；需本机已安装对应解释器）——
    "ruby": ["ruby", "-e"],
    "rb": ["ruby", "-e"],
    "perl": ["perl", "-e"],
    "pl": ["perl", "-e"],
    "php": ["php", "-r"],
    "lua": ["lua", "-e"],
    "r": ["Rscript", "-e"],
    "rscript": ["Rscript", "-e"],
}
_EXEC_MAX_CODE = 20000      # 单次代码长度上限
_EXEC_TIMEOUT = 15          # 执行超时（秒）
_EXEC_MAX_OUTPUT = 20000    # 单次输出截断上限

# 语言别名 → 展示名（解释器缺失提示用）
_EXEC_LANG_DISPLAY = {
    "python": "Python", "python3": "Python", "py": "Python",
    "javascript": "JavaScript (Node)", "js": "JavaScript (Node)", "node": "JavaScript (Node)",
    "shell": "CMD", "sh": "CMD", "powershell": "PowerShell",
    "ruby": "Ruby", "rb": "Ruby",
    "perl": "Perl", "pl": "Perl",
    "php": "PHP", "lua": "Lua",
    "r": "R", "rscript": "R",
}


@app.route("/api/execute_code", methods=["POST"])
def api_execute_code():
    """执行用户/ AI 提供的代码片段，返回运行输出（临时目录隔离 + 超时 + 截断）。

    body: {"code": "...", "language": "python|shell|powershell|javascript"}
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        code = (data.get("code") or "").strip()
        language = (data.get("language") or "python").strip().lower()
        if not code:
            return jsonify(ok=False, error="代码为空")
        if len(code) > _EXEC_MAX_CODE:
            return jsonify(ok=False, error=f"代码过长（上限 {_EXEC_MAX_CODE} 字符）")
        cmd = _EXEC_LANG_MAP.get(language)
        if not cmd:
            return jsonify(ok=False, error=f"不支持的代码语言: {language}（支持 {', '.join(sorted(set(_EXEC_LANG_MAP)))}）")
        # 解释器可用性检测：缺失时给出友好提示（而非"不是内部或外部命令"）
        exe_name = cmd[0]
        if exe_name not in ("cmd", "powershell") and shutil.which(exe_name) is None:
            return jsonify(ok=False, error=f"未检测到 {_EXEC_LANG_DISPLAY.get(language, exe_name)} 解释器（{exe_name}），请先安装后重试")

        # 在临时目录运行，隔离代码产生的文件；编码统一 utf-8 容错
        workdir = tempfile.mkdtemp(prefix="myteacher_term_")
        try:
            result = subprocess.run(
                cmd + [code],
                capture_output=True,
                text=True,
                timeout=_EXEC_TIMEOUT,
                cwd=workdir,
                encoding="utf-8",
                errors="replace",
            )
            stdout, stderr = result.stdout or "", result.stderr or ""
            exit_code = result.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            return jsonify(ok=True, exit_code=-1, stdout=stdout, stderr=f"⏱️ 执行超时（{_EXEC_TIMEOUT} 秒上限）")
        except Exception as exc:
            return jsonify(ok=False, error=f"执行出错: {exc}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        # 输出截断，防止刷屏
        if len(stdout) > _EXEC_MAX_OUTPUT:
            stdout = stdout[:_EXEC_MAX_OUTPUT] + "\n...（输出已截断）"
        if len(stderr) > _EXEC_MAX_OUTPUT:
            stderr = stderr[:_EXEC_MAX_OUTPUT] + "\n...（输出已截断）"
        return jsonify(ok=True, exit_code=exit_code, stdout=stdout, stderr=stderr)
    except Exception as exc:
        app.logger.warning(f"execute_code failed: {exc}")
        return jsonify(ok=False, error=str(exc))


@app.route("/api/terminal_record", methods=["POST"])
def api_terminal_record():
    """把终端执行记录（源代码 + 运行输出）写入当前课程对话，供 AI 后续读取分析。

    body: {"lesson_folder": "...", "language": "...", "code": "...", "stdout": "...", "stderr": "..."}
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        lesson_folder = (data.get("lesson_folder") or "").strip()
        code = (data.get("code") or "").strip()
        language = (data.get("language") or "python").strip()
        stdout = (data.get("stdout") or "").strip()
        stderr = (data.get("stderr") or "").strip()
        if not lesson_folder or not code:
            return jsonify(ok=False, error="缺少课程或代码")

        output_part = stdout + ("\n" + stderr if stderr else "")
        output_part = output_part.strip() or "（无输出）"
        content = (
            f"[终端执行记录] 语言: {language}\n"
            f"用户输入的源代码:\n```{language}\n{code}\n```\n"
            f"运行输出:\n{output_part}"
        )
        conv = load_conversation(lesson_folder)
        conv.append({"role": "user", "content": content})
        # 只保留最近 60 条，避免无限膨胀
        if len(conv) > 60:
            conv = conv[-60:]
        save_conversation(lesson_folder, conv)
        return jsonify(ok=True)
    except Exception as exc:
        app.logger.warning(f"terminal_record failed: {exc}")
        return jsonify(ok=False, error=str(exc))


# REPL 垃圾/试探性输入：? 、\\n / \\c 字面量、>>> / ... REPL 提示符、help/exit/quit 等。
# 用户终端卡死时常输入这类内容试探，不能让其继续污染 buffer。
_TERM_JUNK_RE = re.compile(
    r"^\s*(?:\?+|\\[a-z]+|>>>+|\.{3,}|help|h\?|quit|exit|q|cls|clear|reset)\s*$",
    re.IGNORECASE,
)


class _PySessionRepl:
    """进程内 Python REPL 模拟：累积源码 → compile → exec，捕获 stdout/stderr。

    不依赖 python 交互进程（pyrepl 在管道下行为不可靠），命名空间持久保存，
    支持多行语句、缩进块、变量跨命令保留。
    """

    def __init__(self, cwd: Path):
        self.ns: Dict[str, Any] = {"__name__": "__main__", "__builtins__": __builtins__}
        self.cwd = Path(cwd)
        self.buffer = ""

    # 行尾是这些运算符/分隔符 = 表达式还没写完，REPL 应继续等下一行
    _TRAILING_CHARS = "+-*/%=<>!&|^~,.[({:"
    _CONT_KEYWORDS = {
        "if", "elif", "else", "for", "while", "def", "class", "try", "except",
        "finally", "with", "lambda", "return", "yield", "raise", "import",
        "from", "as", "not", "and", "or", "is", "in", "global", "nonlocal",
        "assert", "del",
    }

    def _trailing_open(self, buffer: str) -> bool:
        """行尾以运算符/逗号/冒号或续行关键字结尾 → 表达式未完成。"""
        last = ""
        for ln in reversed(buffer.splitlines()):
            if ln.strip():
                last = ln.strip()
                break
        if not last:
            return False
        if last[-1] in self._TRAILING_CHARS:
            return True
        return last.split()[-1] in self._CONT_KEYWORDS

    def _is_incomplete(self, exc: SyntaxError, buffer: str) -> bool:
        """判断 compile 的 SyntaxError 是"还没写完等输入"还是"真实语法错误"。

        关键：把这两类区分开，否则一旦输入过冒号行（如 `for i in range(7):`），
        缓冲区会永远被当成续行吞掉后续所有命令（REPL 卡死）。
        """
        msg = getattr(exc, "msg", "") or ""
        # 括号/字符串未闭合、旧版 EOF → 确实还需要更多行
        if any(k in msg for k in (
            "was never closed",             # '(' / '[' / '{' 未闭合
            "unterminated string literal",  # 字符串未闭合
            "unexpected EOF while parsing",
            "invalid or incomplete statement",
        )):
            return True
        if msg.startswith("expected an indented block"):
            # 以冒号行结尾且是最后一行 → 块头在等主体（未完成）；
            # 否则（如冒号行后面接了顶格语句/空行）→ 真实错误，缓冲恢复
            lines = buffer.splitlines()
            if lines and not lines[-1].strip():
                return False  # 尾随空行 = 用户按空 Enter 结束块（CPython 行为）
            last = lines[-1].strip() if lines else ""
            return bool(last and last.endswith(":"))
        if msg == "invalid syntax":
            # 行尾运算符/关键字（如 `1 +`、`x =`、`elif:`）→ 未完成；`?` 之类 → 真实错误
            return self._trailing_open(buffer)
        return False

    def run(self, code: str):
        """执行一条命令。返回 (output, continuation)。

        内置三类防御，避免 REPL 因历史残留未闭合内容而"卡死"（后续命令全部被吞无输出）：
        1) 空行：正在等续行时视为"结束未完成块"，清空缓冲并提示；
        2) 垃圾输入（? / \\n / \\c 字面量 / REPL 提示符等）：正在等续行时先重置缓冲，恢复 REPL；
        3) 卡死恢复：正在等续行、但新命令本身可独立编译通过 → 判定旧缓冲为卡死残留，丢弃后执行新命令。
        """
        stripped = code.strip()
        # 1) 空行：结束未完成块（模拟 CPython 空 Enter 行为），避免块永远挂起
        if not stripped:
            if self.buffer.strip():
                self.buffer = ""
                return "（已结束上次未完成的输入）\n", False
            return "", False
        # 2) 垃圾/试探性输入：不污染缓冲；等续行时顺便恢复 REPL
        if _TERM_JUNK_RE.match(stripped):
            if self.buffer.strip():
                self.buffer = ""
                return "（已重置上次未完成的输入）\n", False
            return "", False
        # 3) 卡死恢复：等续行时若新命令是完整的独立语句，丢弃旧残留直接执行
        if self.buffer.strip():
            try:
                compile(code, "<stdin>", "exec")
                self.buffer = ""  # 旧残留作废
            except SyntaxError:
                pass  # 新命令本身也不完整 → 视为续行，继续累积
        self.buffer += code + "\n"
        if len(self.buffer) > 2000:
            # 防御：续行无限制累积（如字符串/括号一直不闭合）时自动恢复
            self.buffer = ""
            return "⚠️ 输入块过长，已自动重置\n", False
        try:
            compiled = compile(self.buffer, "<stdin>", "exec")
        except SyntaxError as exc:
            if self._is_incomplete(exc, self.buffer):
                return "", True  # 块未结束，继续累积
            self.buffer = ""     # 真实语法错误：清空缓冲，恢复正常 REPL
            return (str(exc) + "\n"), False
        except Exception as exc:
            self.buffer = ""
            return (f"内部错误: {exc}\n"), False
        source = self.buffer
        self.buffer = ""
        # 在课程工作目录下执行
        prev_cwd = os.getcwd()
        buf = io.StringIO()
        old_out, old_err = sys.stdout, sys.stderr
        try:
            if self.cwd.exists():
                os.chdir(str(self.cwd))
            sys.stdout = sys.stderr = buf
            tree = ast.parse(source, "<stdin>", "exec")
            if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr):
                # 单个表达式语句（如 `a`、`1+2`）：eval 并回显其值，模拟 REPL 的 >>> a → 5
                value = eval(compile(source, "<stdin>", "eval"), self.ns)
                if value is not None:
                    buf.write(repr(value) + "\n")
            else:
                exec(compiled, self.ns)
        except Exception:
            # 过滤掉指向 app.py 内部（exec/eval 调用）的帧，只保留用户代码帧
            tb_lines = traceback.format_exc().splitlines()
            clean = []
            i = 0
            while i < len(tb_lines):
                if "app.py" in tb_lines[i] and i + 1 < len(tb_lines):
                    i += 2
                    continue
                clean.append(tb_lines[i])
                i += 1
            buf.write("\n".join(clean))
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            try: os.chdir(prev_cwd)
            except Exception: pass
        return buf.getvalue(), False


# 危险命令拦截：课程终端只允许无害操作，杜绝删库/格式化/关机等破坏性命令
_DANGEROUS_PATTERNS = [
    # 删除类：rm/del/erase/unlink + 递归/强制/静默 标志
    re.compile(r"(^|[;&|]\s*)(rm|del|erase|unlink|rd|rmdir)(\s|$).*(-rf|-r|-f|/s|/q|/f)", re.I),
    # 纯删除命令（无标志但有明确的目标，如 `del *.py`、`rm -rf` 已覆盖，此处兜底 `rmdir 目录`）
    re.compile(r"(^|[;&|]\s*)(rm|del|erase|unlink)(\s|$).*(\.\*|\*|/s\b|/q\b|-rf\b|-r\b|-f\b)", re.I),
    # 格式化 / 分区 / 磁盘操作
    re.compile(r"(^|[;&|]\s*)format\s+[a-zA-Z]:", re.I),
    re.compile(r"(^|[;&|]\s*)(mkfs|fdisk|diskpart|format|low level format)(\b|\s|$)", re.I),
    # 关机 / 重启
    re.compile(r"(^|[;&|]\s*)(shutdown|reboot|init\s+0|init\s+6)(\s|$)", re.I),
    # 系统级破坏：注册表删除、启动项、dd 覆盖磁盘、清空回收站等
    re.compile(r"(^|[;&|]\s*)reg\s+(delete|del)\b", re.I),
    re.compile(r"(^|[;&|]\s*)dd\s+if=.*of=\s*[a-zA-Z]:", re.I),
    re.compile(r"(^|[;&|]\s*)clear\s+recyclebin\b", re.I),
    re.compile(r"(^|[;&|]\s*)Remove-Item\b.*(-Recurse|-Force)", re.I),
    # 危险的 sudo 包裹（sudo rm / sudo mkfs 等）
    re.compile(r"(^|[;&|]\s*)sudo\s+(rm|del|mkfs|fdisk|dd|shutdown|reboot|format)(\s|$)", re.I),
]

# 幂等拦截：命中危险模式时返回的提示信息
_DANGEROUS_MSG = "⛔ 已拦截危险命令（课程终端禁止删除/格式化/关机等破坏性操作）"


class TerminalSession:
    """交互式终端会话：持久工作目录 + 持久解释器状态。

    - python：进程内 REPL 模拟（变量/多行块状态连续，可靠）。
    - javascript：持久 node REPL 进程。
    - shell / powershell：每次单条执行，但会话内维护持久工作目录（支持 cd）。
    """

    def __init__(self, sid: str, cwd: Path):
        self.sid = sid
        # 沙箱根目录：终端不允许跳出课程目录（防"越狱"）
        self.root = Path(cwd).resolve()
        self.cwd = self.root
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.py: Optional[_PySessionRepl] = None
        self.node_proc: Optional[subprocess.Popen] = None
        self.node_in_cont = False

    def _inside_root(self, target: Path) -> bool:
        """判断路径是否在沙箱根目录（含子目录）之内。Windows 路径比较大小写不敏感。"""
        try:
            target = target.resolve()
        except Exception:
            return False
        return target == self.root or self.root in target.parents

    def _ensure_py(self) -> _PySessionRepl:
        if self.py is None:
            self.py = _PySessionRepl(self.cwd)
        return self.py

    def _read_node_prompt(self, timeout: int = 15):
        out, buf = "", ""
        # 防御：node_proc 进程已退出（poll() != None）或 stdout 已被关闭 → 返回空
        if self.node_proc is None or self.node_proc.poll() is not None or self.node_proc.stdout is None:
            return out, ""
        start = time.time()
        while time.time() - start < timeout:
            try:
                ch = self.node_proc.stdout.read(1)
            except (ValueError, OSError):
                break
            if not ch:
                break
            out += ch
            buf = (buf + ch)[-5:]
            if any(buf.endswith(t) for t in ("> ", "... ")):
                break
        matched = ""
        for t in ("> ", "... "):
            if out.endswith(t):
                matched = t
                out = out[:-len(t)]
                break
        return out, matched

    def _start_node(self):
        env = dict(os.environ)
        proc = subprocess.Popen(
            ["node", "-i"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1, cwd=str(self.cwd), env=env,
        )
        # 先把 proc 挂到 self.node_proc，再读 prompt —— 否则上一轮已退出进程会留下 None stdout
        self.node_proc = proc
        self.node_in_cont = False
        # Node REPL 启动后会先打印欢迎语 "Welcome to Node.js v..."，稍后会再打印提示符 "> "
        self._read_node_prompt()
        # 启动后等一拍，丢弃首批（欢迎语 + 首行提示符），让交互状态干净
        try:
            proc.stdin.write("\n")
            proc.stdin.flush()
            self._read_node_prompt()
        except Exception:
            pass

    def _run_node(self, cmd: str) -> str:
        if self.node_proc is None or self.node_proc.poll() is not None:
            self._start_node()
        try:
            need_blank = self.node_in_cont or ("\n" in cmd)
            self.node_proc.stdin.write(cmd + ("\n\n" if need_blank else "\n"))
            self.node_proc.stdin.flush()
        except Exception as exc:
            return f"会话中断: {exc}\n"
        out, prompt = self._read_node_prompt()
        self.node_in_cont = (prompt == "... ")
        return out

    def run(self, lang: str, cmd: str):
        """执行一条命令。返回 (output, continuation)。"""
        cmd = cmd.rstrip("\n")
        if lang == "python":
            return self._ensure_py().run(cmd)
        if lang == "javascript":
            return self._run_node(cmd), False
        # 其他脚本语言（ruby / perl / php / lua / r）：单次执行，沿用会话 cwd
        if lang not in ("shell", "sh", "powershell"):
            entry = _EXEC_LANG_MAP.get(lang)
            if not entry:
                return f"不支持的语言: {lang}\n", False
            exe_name = entry[0]
            if shutil.which(exe_name) is None:
                return f"⛔ 未检测到 {_EXEC_LANG_DISPLAY.get(lang, exe_name)} 解释器（{exe_name}），请先安装后重试\n", False
            try:
                result = subprocess.run(
                    entry + [cmd],
                    capture_output=True, text=True, timeout=_EXEC_TIMEOUT,
                    cwd=str(self.cwd), encoding="utf-8", errors="replace",
                )
                return (result.stdout or "") + (result.stderr or ""), False
            except subprocess.TimeoutExpired:
                return f"执行超时（{_EXEC_TIMEOUT} 秒）\n", False
        # shell / powershell：单次执行，会话内维护 cwd（且不允许跳出课程目录）
        stripped = cmd.strip()
        # 危险命令拦截：命中即拒绝执行（不交给 shell）
        for pat in _DANGEROUS_PATTERNS:
            if pat.search(stripped):
                return f"{_DANGEROUS_MSG}：{stripped[:80]}\n", False
        if stripped.lower().startswith("cd"):
            cd_arg = stripped[2:].strip()
            # cmd 的 `cd /d X` 写法
            if lang == "shell" and cd_arg[:3].lower() == "/d ":
                cd_arg = cd_arg[3:].strip()
            if not cd_arg:
                return "", False  # 单独 `cd`：无输出（当前目录由后续命令可见）
            if re.search(r"[;&|<>\$()]", cd_arg):
                # 带复合命令的 cd（如 `cd C:\ ; pwd`）：不拦截，交给 shell 执行，
                # 若真的切出沙箱，由下方的末尾 cwd 检测重置
                pass
            else:
                new_dir = cd_arg.strip('"')
                target = Path(new_dir)
                if not target.is_absolute():
                    target = self.cwd / target
                target = target.resolve()
                if not self._inside_root(target):
                    return f"⛔ 已阻止：不能切换到课程目录之外（{new_dir}）\n", False
                if target.exists() and target.is_dir():
                    self.cwd = target
                    return "", False
                return f"目录不存在: {new_dir}\n", False
        exe = "cmd" if lang == "shell" else "powershell"
        # 强制子进程输出 UTF-8（中文 Windows 默认 GBK，会导致乱码），统一按 utf-8 解码
        # 末尾附带 cwd 标记：复合命令（如 `cd C:\ & dir`）偷改目录会被检测并重置回课程目录
        m_start, m_end = "__CWD_START__", "__CWD_END__"
        if lang == "shell":
            real_cmd = f"chcp 65001 >nul & {cmd} & echo {m_start} & cd & echo {m_end}"
            args = ["/c", real_cmd]
        else:
            real_cmd = (
                f"[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                f"[Console]::InputEncoding=[System.Text.Encoding]::UTF8; "
                f"{cmd}; Write-Output '{m_start}'; (Get-Location).Path; Write-Output '{m_end}'"
            )
            args = ["-NoProfile", "-Command", real_cmd]
        try:
            result = subprocess.run(
                [exe] + args,
                capture_output=True, text=True, timeout=15,
                cwd=str(self.cwd), encoding="utf-8", errors="replace",
            )
            out = (result.stdout or "") + (result.stderr or "")
        except subprocess.TimeoutExpired:
            return "执行超时（15 秒）\n", False
        # 解析 cwd 标记 → 若复合命令把工作目录切到了沙箱外，重置回根并提示
        lines = out.splitlines()
        idx = next((i for i, ln in enumerate(lines) if ln.strip() == m_start), -1)
        cwd_line = ""
        if idx >= 0 and idx + 1 < len(lines):
            cwd_line = lines[idx + 1].strip()
            del lines[idx:idx + 3]  # 去掉「标记 + cwd + 结束标记」三行
            out = "\n".join(lines)
        if cwd_line:
            try:
                current = Path(cwd_line)
                if current.is_absolute():
                    if self._inside_root(current):
                        self.cwd = current  # 复合命令在沙箱内切换目录，会话同步跟踪
                    else:
                        self.cwd = self.root
                        out += "\n⛔ 已阻止：命令试图跳出课程目录，工作目录已重置回课程根目录\n"
            except Exception:
                pass
        return out, False

    def close(self):
        if self.node_proc is not None:
            try: self.node_proc.terminate()
            except Exception: pass
            self.node_proc = None


# 终端会话缓存：key = (lesson_folder, lang)
_TERMINAL_SESSIONS: Dict[tuple, TerminalSession] = {}


@app.route("/api/terminal/command", methods=["POST"])
def api_terminal_command():
    """交互式终端：向持久会话发送一条命令/代码，返回执行输出。

    body: {"lesson_folder": "...", "language": "python|shell|powershell|javascript", "cmd": "..."}
    """
    try:
        data = request.get_json(force=True, silent=True) or {}
        lesson_folder = (data.get("lesson_folder") or "").strip()
        language = (data.get("language") or "python").strip().lower()
        cmd = (data.get("cmd") or "")
        # python 允许空行：用于"结束当前未完成的块"（如冒号行后按空 Enter）
        if language != "python" and not cmd.strip():
            return jsonify(ok=False, error="命令为空")
        # 保留前导空格（多行代码/缩进续行需要），仅去除结尾换行
        cmd = cmd.rstrip("\r\n")
        if language not in _EXEC_LANG_MAP:
            return jsonify(ok=False, error=f"不支持的语言: {language}")

        # 会话工作目录：优先当前课程目录，无课程则用全局 workspace
        if lesson_folder:
            cwd = (LESSONS_DIR / lesson_folder)
            cwd.mkdir(parents=True, exist_ok=True)
        else:
            cwd = Path(BASE_DIR) / "terminal_workspace"

        key = (lesson_folder or "", language)
        session = _TERMINAL_SESSIONS.get(key)
        if session is None:
            session = TerminalSession(f"{key[0]}|{key[1]}", cwd)
            _TERMINAL_SESSIONS[key] = session
        # 清理过期的会话（重启/陈旧进程）
        if len(_TERMINAL_SESSIONS) > 32:
            for k, s in list(_TERMINAL_SESSIONS.items()):
                if k != key:
                    s.close()
                    _TERMINAL_SESSIONS.pop(k, None)
        output, continuation = session.run(language, cmd)
        return jsonify(ok=True, output=output, continuation=bool(continuation))
    except Exception as exc:
        app.logger.warning(f"terminal_command failed: {exc}")
        return jsonify(ok=False, error=str(exc))


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
            total_units = len(units)
            current_unit = int(progress.get("current_unit", 0) or 0)
            completed_units = progress.get("completed_units") or []
            if total_units > 0:
                progress_pct = min(100, round(100 * len(completed_units) / total_units))
            else:
                progress_pct = 0
            items.append(
                {
                    "name": child.name,
                    "lesson_id": metadata.get("lesson_id") or child.name,
                    "topic": metadata.get("topic") or child.name,
                    "assistant_name": metadata.get("assistant_name") or "",
                    "created_at": datetime.fromtimestamp(child.stat().st_ctime).astimezone().isoformat(timespec="seconds"),
                    "last_access": progress.get("last_access") or "",
                    "units_count": total_units,
                    "current_unit": current_unit,
                    "completed_units": completed_units,
                    "progress_pct": progress_pct,
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


@app.route("/api/lessons/<path:lesson_folder>/rename", methods=["POST"])
def api_lesson_rename(lesson_folder: str):
    """重命名课程目录（不允许修改 topic 元数据，避免历史数据错位）。"""
    if not lesson_folder or "/" in lesson_folder or "\\" in lesson_folder or ".." in lesson_folder:
        return jsonify({"error": "非法的课程名"}), 400
    target = LESSONS_DIR / lesson_folder
    try:
        resolved = target.resolve()
        base_resolved = LESSONS_DIR.resolve()
        if not str(resolved).startswith(str(base_resolved)):
            return jsonify({"error": "目标路径不在课程目录内"}), 400
    except Exception:
        return jsonify({"error": "路径解析失败"}), 400
    if not target.exists() or not target.is_dir():
        return jsonify({"error": "课程不存在"}), 404

    payload = request.get_json(silent=True) or {}
    new_name = (payload.get("new_name") or "").strip()
    if not new_name:
        return jsonify({"error": "new_name 必填"}), 400
    if "/" in new_name or "\\" in new_name or ".." in new_name:
        return jsonify({"error": "新名称非法"}), 400
    # 名称规整：去除非法字符
    new_name = sanitize_topic(new_name)
    if not new_name:
        return jsonify({"error": "新名称为空"}), 400
    # 若同名，仅加数字后缀
    final = new_name
    suffix = 1
    while (LESSONS_DIR / final).exists() and final != lesson_folder:
        suffix += 1
        final = f"{new_name}_{suffix}"
    if final == lesson_folder:
        return jsonify({"status": "ok", "renamed": lesson_folder, "new_name": lesson_folder})
    new_path = LESSONS_DIR / final
    try:
        target.rename(new_path)
    except Exception as exc:
        return jsonify({"error": f"重命名失败：{exc}"}), 500

    # 若重命名当前激活课程，同步更新 ACTIVE_LESSON.folder
    if ACTIVE_LESSON.get("folder") == lesson_folder:
        ACTIVE_LESSON["folder"] = final

    return jsonify({"status": "ok", "renamed": lesson_folder, "new_name": final})


def _validate_lesson_folder(lesson_folder: str) -> Optional[Path]:
    """校验课程目录名合法性，返回解析后的路径；非法返回 None。"""
    if not lesson_folder or "/" in lesson_folder or "\\" in lesson_folder or ".." in lesson_folder:
        return None
    target = LESSONS_DIR / lesson_folder
    try:
        resolved = target.resolve()
        if not str(resolved).startswith(str(LESSONS_DIR.resolve())):
            return None
    except Exception:
        return None
    return resolved


@app.route("/api/lessons/<path:lesson_folder>/export", methods=["GET"])
def api_lesson_export(lesson_folder: str):
    """导出课程为 zip 备份（含教案、对话、进度、画像、资料等全部文件）。"""
    target = _validate_lesson_folder(lesson_folder)
    if target is None:
        return jsonify({"error": "非法的课程名"}), 400
    if not target.exists() or not target.is_dir():
        return jsonify({"error": "课程不存在"}), 404

    # 排除运行时临时产物：node_modules（若课件里误装了依赖）、__pycache__
    EXCLUDE_SUBDIRS = {"node_modules", "__pycache__", ".venv"}
    # 顶层目录名：用课程文件夹名包裹，保证导入时能识别唯一目录
    top = target.name
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(target.rglob("*")):
            if file_path.is_dir():
                continue
            if any(part in EXCLUDE_SUBDIRS for part in file_path.relative_to(target).parts):
                continue
            try:
                zf.write(file_path, f"{top}/{file_path.relative_to(target).as_posix()}")
            except OSError:
                continue
    buffer.seek(0)
    safe_name = sanitize_topic(lesson_folder) or "lesson"
    filename = urllib.parse.quote(f"{safe_name}_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip")
    return Response(
        buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@app.route("/api/lessons/import", methods=["POST"])
def api_lesson_import():
    """从 zip 备份导入课程。校验路径穿越，自动处理目录名冲突。"""
    if "file" not in request.files:
        return jsonify({"error": "缺少备份文件（file 字段）"}), 400
    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "文件为空"}), 400
    if not (file.filename.lower().endswith(".zip")):
        return jsonify({"error": "仅支持 .zip 备份文件"}), 400
    try:
        zf = zipfile.ZipFile(file.stream)
    except zipfile.BadZipFile:
        return jsonify({"error": "不是有效的 zip 文件"}), 400
    except Exception as exc:
        return jsonify({"error": f"读取失败：{exc}"}), 400

    # 顶层目录名（备份 zip 内第一条目所属目录），用于冲突时改名
    names = zf.namelist()
    if not names:
        return jsonify({"error": "备份文件为空"}), 400
    top_level = names[0].split("/", 1)[0]
    if not top_level or any(c in top_level for c in "/\\"):
        top_level = f"imported_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # 安全解压：拒绝绝对路径 / .. 穿越
    base = LESSONS_DIR.resolve()
    dest = LESSONS_DIR / top_level
    suffix = 1
    while dest.exists():
        dest = LESSONS_DIR / f"{top_level}_{suffix}"
        suffix += 1
    imported = 0
    try:
        for info in zf.infolist():
            fname = info.filename
            if fname.startswith("/") or ".." in fname.split("/"):
                return jsonify({"error": f"备份包含非法路径：{fname}"}), 400
            rel = Path(*fname.split("/"))
            out_path = (dest / rel).resolve()
            if not str(out_path).startswith(str(base)) or out_path == dest:
                continue
            if info.is_dir():
                out_path.mkdir(parents=True, exist_ok=True)
                continue
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            imported += 1
    except Exception as exc:
        shutil.rmtree(dest, ignore_errors=True)
        return jsonify({"error": f"解压失败：{exc}"}), 500
    finally:
        zf.close()
    return jsonify({"status": "ok", "folder": dest.name, "imported_files": imported})


# ============================
# 交互式备课诊断（实验功能）：
# 1) /api/prep_diagnose/open      —— 根据主题生成 4-6 个诊断问题（首轮），返回 questions
# 2) /api/prep_diagnose/answer    —— 学生回答一轮问题，更新已掌握/未掌握清单，返回下一轮或 done
# 3) /api/prep_diagnose/finish    —— 合并最终诊断结论 known_points / unknown_points
# ============================
_DIAGNOSIS_STATE: Dict[str, Dict[str, Any]] = {}


def _diagnosis_get_lesson_provider_config() -> Dict[str, Any]:
    """从当前 config 读取用于诊断问答的 provider / 模型信息，复用云端/对话配置。"""
    cfg = load_config()
    # 诊断对话语义与备课接近，优先用 lesson_provider；其没有时回退 chat_provider
    provider = (cfg.get("lesson_provider") or cfg.get("chat_provider") or "auto").strip().lower()
    if provider == "ollama":
        base_url = (cfg.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
        return {
            "provider": "ollama",
            "base_url": base_url,
            "model": (cfg.get("ollama_model") or "qwen3:8b").strip(),
            "api_key": "",
        }
    # 云端 / auto 默认走 cloud_*
    base_url = (cfg.get("cloud_base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
    return {
        "provider": "cloud",
        "base_url": base_url,
        "model": (cfg.get("cloud_model") or "deepseek-ai/DeepSeek-V3").strip(),
        "api_key": (cfg.get("cloud_api_key") or cfg.get("siliconflow_api_key") or "").strip(),
    }


def _diagnosis_get_fallback_config() -> Dict[str, Any]:
    """fallback 配置：当主 provider 不可用时切换到这里。"""
    cfg = load_config()
    base_url = (cfg.get("cloud_base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
    api_key = (cfg.get("cloud_api_key") or cfg.get("siliconflow_api_key") or "").strip()
    return {
        "provider": "cloud",
        "base_url": base_url,
        "model": (cfg.get("cloud_model") or "deepseek-ai/DeepSeek-V3").strip(),
        "api_key": api_key,
    }


def _diagnosis_call_llm(messages: List[Dict[str, str]], max_tokens: int = 1024, temperature: float = 0.5) -> Optional[str]:
    """调用当前 provider 跑一次诊断问答，返回模型回复文本，失败返回 None。
    主 provider 不可用（如本地 ollama 未启动）时，自动 fallback 到云端。
    """
    pc = _diagnosis_get_lesson_provider_config()
    primary_err: Optional[str] = None
    if pc["provider"] == "ollama":
        try:
            payload = {
                "model": pc["model"],
                "messages": messages,
                "stream": False,
                "think": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            r = requests.post(f"{pc['base_url']}/api/chat", json=payload, timeout=20)
            if r.ok:
                text = (r.json().get("message", {}).get("content") or "").strip()
                if text:
                    return text
                primary_err = "Ollama 返回空内容"
            else:
                primary_err = f"Ollama HTTP {r.status_code}: {r.text[:200]}"
        except Exception as exc:
            primary_err = f"{type(exc).__name__}: {exc}"
        # 失败：fallback 到云端
        app.logger.warning("诊断·主路径 Ollama 不可用，自动 fallback 到云端 (%s)", primary_err)
        pc = _diagnosis_get_fallback_config()
    # 云端（OpenAI-compatible chat completions）
    try:
        if not pc.get("api_key"):
            app.logger.error("诊断·云端 API Key 未配置 (primary_err=%s)", primary_err)
            return None
        url = f"{pc['base_url']}/chat/completions"
        if pc["base_url"].endswith("/chat/completions"):
            url = pc["base_url"]
        payload = {
            "model": pc["model"],
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {pc['api_key']}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if not r.ok:
            app.logger.error("诊断·云端 HTTP %s: %s (primary_err=%s)", r.status_code, r.text[:300], primary_err)
            return None
        return (r.json()["choices"][0]["message"]["content"] or "").strip() or None
    except Exception as exc:
        app.logger.error("诊断·LLM 调用异常: %s: %s", type(exc).__name__, exc)
        return None


_DIAGNOSIS_OPEN_PROMPT = (
    "你是一位经验丰富的老师，正在以对话的方式给学生做课前摸底。\n"
    "针对【主题】，给出第一个开场问题：问该主题最基础、最关键的一个前置概念。\n"
    "要求：问题简短自然（10~30 字），像老师课堂上随口发问，学生能用一两句话回答。\n\n"
    "严格按 JSON 输出，禁止解释：\n"
    '{"question": "第一个问题", "concept": "被考察的概念名"}'
)


_DIAGNOSIS_CONVO_PROMPT = (
    "你是一位经验丰富的老师，正在以对话的方式给学生做课前摸底。\n"
    "你要像真人老师一样自然交流：先回应学生上一轮的回答，再顺着上下文提出下一个问题，"
    "一步步摸清学生对【主题】各核心方面的掌握情况。\n\n"
    "【主题】\n{topic}\n\n"
    "【对话记录】（老师与学生的历史问答）\n{history}\n\n"
    "【本轮的最后一个学生回答】\n{answer}\n\n"
    "现在请完成：\n"
    "1. status：判断学生这轮回答的掌握程度（known=已掌握 / partial=部分掌握 / unknown=未掌握 / skip=跳过）\n"
    "2. reply：一句简短的自然回应（≤30 字），只针对学生这轮的回答给出肯定、纠正或简单补充；"
    "【绝对禁止】在 reply 中夹带任何问题或追问——所有追问一律只放在 next_question 字段里。\n"
    "3. 决定下一步：\n"
    "   - 若还需了解其他方面 → 给出 next_question（承接上下文的下一个问题，≤35 字）与 next_concept（该问题考察的概念名）\n"
    "   - 若已了解足够（已覆盖该主题 4~6 个核心方面，或信息已充分）→ done=true，并给 summary（1~2 句总结学生整体水平）\n"
    "4. known/partial/unknown：根据整个对话，把已确认的概念分别列出（只有明确确认才列入，不要重复）\n\n"
    "严格按 JSON 输出，禁止多余文字：\n"
    "{\n"
    '  "status": "known|partial|unknown|skip",\n'
    '  "reply": "老师对这次回答的回应",\n'
    '  "next_question": "下一个问题（done 时为空）",\n'
    '  "next_concept": "下一问概念（done 时为空）",\n'
    '  "done": false,\n'
    '  "summary": "总评（done 时填写，否则为空）",\n'
    '  "known": ["概念"],\n'
    '  "partial": ["概念"],\n'
    '  "unknown": ["概念"]\n'
    "}"
)


@app.route("/api/prep_diagnose/open", methods=["POST"])
def api_prep_diagnose_open():
    """开启诊断：生成对话式摸底的第一问。"""
    payload = request.get_json(silent=True) or {}
    topic = str(payload.get("topic") or "").strip()
    if not topic:
        return jsonify({"error": "topic 必填"}), 400
    messages = [
        {"role": "system", "content": _DIAGNOSIS_OPEN_PROMPT},
        {"role": "user", "content": f"【主题】{topic}"},
    ]
    text = _diagnosis_call_llm(messages, max_tokens=300)
    if not text:
        return jsonify({"error": "诊断生成失败，请重试"}), 500
    data = _robust_json_loads(text) if "_robust_json_loads" in globals() else json.loads(text)
    if not isinstance(data, dict):
        return jsonify({"error": "诊断问题格式错误", "raw": text[:500]}), 500
    question = str(data.get("question") or "").strip()
    concept = str(data.get("concept") or "").strip()
    if not question:
        return jsonify({"error": "诊断问题为空", "raw": text[:500]}), 500
    # 分配 session id，状态保存到内存（一次诊断会话）
    sid = secrets.token_urlsafe(16)
    _DIAGNOSIS_STATE[sid] = {
        "topic": topic,
        "conversation": [],       # 每轮: {"q", "a", "status", "reply"}
        "known": [],              # list of concept
        "partial": [],
        "unknown": [],
        "rounds": 0,              # 已答轮数
        "max_rounds": 6,          # 对话轮数上限，防止无限对话
        "last_question": question,
        "last_concept": concept,
    }
    return jsonify({"session_id": sid, "question": question, "concept": concept, "topic": topic})


@app.route("/api/prep_diagnose/answer", methods=["POST"])
def api_prep_diagnose_answer():
    """对话式诊断：学生回答一轮，AI 基于完整对话历史生成回应 + 下一问（或结束）。"""
    payload = request.get_json(silent=True) or {}
    sid = str(payload.get("session_id") or "").strip()
    answer = str(payload.get("answer") or "").strip()
    state = _DIAGNOSIS_STATE.get(sid)
    if not state:
        return jsonify({"error": "session 已失效，请重新开启诊断"}), 400
    if not answer:
        return jsonify({"error": "answer 必填"}), 400
    # 组装对话历史（老师的问题 + 学生的回答 + 历史评估）
    history_lines = []
    for turn in state["conversation"]:
        history_lines.append(f"老师：{turn['q']}")
        status_map = {"known": "已掌握", "partial": "部分掌握", "unknown": "未掌握", "skip": "跳过"}
        history_lines.append(f"学生：{turn['a']}（我的判断：{status_map.get(turn['status'], turn['status'])})")
    history_text = "\n".join(history_lines) or "（暂无，这是第一轮）"
    sys_prompt = (_DIAGNOSIS_CONVO_PROMPT
                  .replace("{topic}", state["topic"])
                  .replace("{history}", history_text)
                  .replace("{answer}", answer))
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": answer},
    ]
    text = _diagnosis_call_llm(messages, max_tokens=700)
    if not text:
        return jsonify({"error": "评估失败，请重试"}), 500
    try:
        result = _robust_json_loads(text)
    except Exception:
        result = {}
    if not isinstance(result, dict):
        result = {}
    status = str(result.get("status") or "unknown").strip().lower()
    if status not in ("known", "partial", "unknown", "skip"):
        status = "unknown"
    reply = str(result.get("reply") or "").strip()
    summary = str(result.get("summary") or "").strip()
    next_q = str(result.get("next_question") or "").strip()
    next_concept = str(result.get("next_concept") or "").strip()
    done = bool(result.get("done") is True) or bool(str(result.get("done")).lower() == "true")
    # 记录本轮
    state["conversation"].append({
        "q": state.get("last_question") or "",
        "a": answer,
        "status": status,
        "reply": reply,
    })
    state["rounds"] += 1
    # 合并概念清单（去重）
    for bucket, items in (("known", result.get("known")), ("partial", result.get("partial")), ("unknown", result.get("unknown"))):
        if isinstance(items, list):
            for c in items:
                c = str(c).strip()
                if not c:
                    continue
                for b in (state["known"], state["partial"], state["unknown"]):
                    if c in b:
                        b.remove(c)
                if c not in state[bucket]:
                    state[bucket].append(c)
    # 轮数硬限制：到上限强制结束
    if not done and state["rounds"] >= state.get("max_rounds", 6):
        done = True
        if not summary:
            summary = "对话已覆盖足够多的问题，摸底到此为止。"
    # 下一问
    if done:
        next_q = ""
        next_concept = ""
    else:
        state["last_question"] = next_q
        state["last_concept"] = next_concept
    return jsonify({
        "status": status,
        "reply": reply,
        "next_question": next_q,
        "next_concept": next_concept,
        "done": done,
        "summary": summary,
        "known": state["known"],
        "partial": state["partial"],
        "unknown": state["unknown"],
        "rounds": state["rounds"],
        "max_rounds": state.get("max_rounds", 6),
    })


@app.route("/api/prep_diagnose/finish", methods=["POST"])
def api_prep_diagnose_finish():
    """结束诊断：合并所有轮的评估，返回结构化结论（known/partial/unknown），并清理 session。"""
    payload = request.get_json(silent=True) or {}
    sid = str(payload.get("session_id") or "").strip()
    state = _DIAGNOSIS_STATE.pop(sid, None) if sid else None
    if not state:
        return jsonify({"error": "session 不存在或已结束"}), 400
    return jsonify({
        "topic": state["topic"],
        "known": state["known"],
        "partial": state["partial"],
        "unknown": state["unknown"],
        "total": state.get("rounds", 0),
        "answered": state.get("rounds", 0),
    })


@app.route("/api/prepare_lesson", methods=["POST"])
def api_prepare_lesson():
    """AI 备课预览（不保存到磁盘，用户确认后再保存）。

    支持两种请求：
      - JSON: {"topic": "..."}
      - multipart/form-data: topic 字段 + 可选 files（word/pdf/ppt/txt/md 等课程资料，可多文件）
    上传的文档会转成 Markdown 作为备课素材，供 AI 拆分单元、并附上相关视频链接。
    """
    doc_markdown = ""
    payload = {}
    if request.content_type and request.content_type.startswith("multipart/form-data"):
        topic = (request.form.get("topic") or "").strip()
        if not topic:
            return jsonify({"error": "topic is required"}), 400
        md_parts: List[str] = []
        pdf_files: List[Dict[str, Any]] = []   # 暂存上传的整本 PDF：只提目录，正文按需
        for f in request.files.getlist("files"):
            if not f or not f.filename:
                continue
            safe = secure_filename(f.filename) or f"file_{int(time.time() * 1000)}"
            ext = Path(safe).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                md_parts.append(f"## 文件 {safe}\n\n（不支持的文件类型 {ext or '未知'}，已跳过）")
                continue
            tmp_path = Path(tempfile.gettempdir()) / f"lesson_prep_{int(time.time() * 1000)}_{safe}"
            try:
                f.save(str(tmp_path))
                if ext == ".pdf":
                    # 整本教材 PDF：只提取目录（快），正文等课程应用时按单元按需提取对应章节
                    toc = extract_pdf_toc(tmp_path)
                    pdf_files.append({"name": safe, "path": str(tmp_path), "toc": toc})
                    if toc:
                        toc_text = "\n".join(
                            f"{'  ' * (it.get('level') or 0)}{it.get('title')}（第 {it.get('page')} 页）"
                            for it in toc
                        )
                    else:
                        toc_text = "（该 PDF 无书签目录，课程应用时将按页顺序分段读取）"
                    md_parts.append(
                        f"# 课程资料（整本教材）：{safe}\n\n"
                        f"## 本书目录\n{toc_text}\n\n"
                        "（完整正文将在课程应用时按单元按需提取对应章节）"
                    )
                else:
                    md = convert_document_to_markdown(tmp_path)
                    if md and "无法自动提取" not in md and "无法直接读取" not in md:
                        md_parts.append(f"# 课程资料：{safe}\n\n{md}")
                    else:
                        md_parts.append(f"## 文件 {safe}\n\n{md}")
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception as exc:
                md_parts.append(f"## 文件 {safe}\n\n读取失败：{exc}")
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
        if md_parts:
            doc_markdown = "\n\n---\n\n".join(md_parts)
        ACTIVE_LESSON["preview_pdf_files"] = pdf_files
    else:
        payload = request.get_json(silent=True) or {}
        topic = (payload.get("topic") or "").strip()
        if not topic:
            return jsonify({"error": "topic is required"}), 400
        doc_markdown = (payload.get("document_markdown") or "").strip()

    cfg = load_config()
    if payload.get("cloud_model"):
        cfg["cloud_model"] = payload["cloud_model"]
    if payload.get("tts_cloud_voice"):
        cfg["tts_cloud_voice"] = payload["tts_cloud_voice"]

    # 整本教材 PDF 的目录（若有），用于指导 AI 按目录章节数生成单元
    doc_toc = []
    if ACTIVE_LESSON.get("preview_pdf_files"):
        doc_toc = ACTIVE_LESSON["preview_pdf_files"][0].get("toc") or []

    # 交互式备课诊断结论（来自 /api/prep_diagnose/finish）
    diagnosis = payload.get("diagnosis") if isinstance(payload, dict) else None
    if not isinstance(diagnosis, dict):
        diagnosis = None

    lesson_plan = prepare_lesson(topic, config=cfg, document_markdown=doc_markdown, doc_toc=doc_toc, diagnosis=diagnosis)
    # 备课来源元信息（是否兜底模板 / 降级说明 / 使用模型 / token 用量），从教案中摘出单独返回
    prep_meta = {}
    if isinstance(lesson_plan, dict):
        prep_meta = lesson_plan.pop("_meta", {}) or {}
    lesson_folder = build_lesson_folder_name(topic)

    # 预览模式：存入 ACTIVE_LESSON，用户确认后再写入磁盘
    ACTIVE_LESSON["preview_plan"] = lesson_plan
    ACTIVE_LESSON["preview_topic"] = topic
    ACTIVE_LESSON["preview_doc_markdown"] = doc_markdown
    ACTIVE_LESSON["preview_assistant_name"] = (
        payload.get("assistant_name") or cfg.get("assistant_name") or "艾琳老师"
    ).strip()
    ACTIVE_LESSON["preview_personality_prompt"] = (
        payload.get("personality_prompt") or cfg.get("personality_prompt") or "你是一位温柔、专业、耐心的 AI 学习导师。"
    ).strip()
    ACTIVE_LESSON["preview_tts_voice"] = (
        payload.get("tts_voice") or cfg.get("tts_voice") or cfg.get("default_voice") or "zh-CN-XiaoxiaoNeural"
    ).strip()
    ACTIVE_LESSON["preview_tts_cloud_voice"] = (
        payload.get("tts_cloud_voice") or cfg.get("tts_cloud_voice") or ""
    ).strip()

    return jsonify({
        "lesson_folder": lesson_folder,
        "plan": lesson_plan,
        "prepared_meta": prep_meta,
        "doc_toc": doc_toc,
        "ocr_available": pdf_ocr_available(),
    })


def _match_pdf_toc_to_units(units, toc):
    """把 PDF 目录条目与单元标题做宽松匹配，返回每个匹配单元的章节页码范围。

    返回 [{unit_index, start_page, end_page}]；end_page 为下一匹配章起始页-1，
    末章为 None（表示读到文档末页）。匹配失败返回空列表。
    匹配策略：优先顶层章节（level 0），其次任意层级；同一起始页只保留第一个单元。
    """

    def norm(s):
        s = re.sub(
            r"^(第\s*\d+\s*[课讲节章]|lesson\s*\d+|unit\s*\d+|chapter\s*\d+|chapter\s*[ivxlcdm]+)\s*[:：、.\-\s]*",
            "", str(s or ""), flags=re.I,
        )
        s = re.sub(r"[^\w\u4e00-\u9fa5]+", "", s)
        return s.lower()

    if not units or not toc:
        return []
    norm_toc = [
        {"norm": norm(it.get("title")), "page": int(it.get("page") or 0),
         "level": int(it.get("level") or 0)}
        for it in toc if it.get("page")
    ]
    if not norm_toc:
        return []

    def _match_unit(utitle):
        """返回匹配到的目录条目（优先顶层章节），无匹配返回 None。"""
        best, best_level = None, None
        for t in norm_toc:
            if not t["norm"]:
                continue
            hit = t["norm"] == utitle or t["norm"] in utitle or utitle in t["norm"]
            if not hit:
                continue
            # 顶层章节（level 0）优先；同层级取更长的标题（更精确）
            if best is None or t["level"] < best_level or (t["level"] == best_level and len(t["norm"]) > len(best["norm"])):
                best, best_level = t, t["level"]
        return best

    matched = []
    seen_pages = set()
    for ui, u in enumerate(units):
        utitle = norm(u.get("title", ""))
        if not utitle:
            continue
        best = _match_unit(utitle)
        if best and best["page"] not in seen_pages:
            seen_pages.add(best["page"])
            matched.append({"unit_index": ui, "start_page": best["page"]})
    if not matched:
        return []
    matched.sort(key=lambda r: r["start_page"])
    for i, r in enumerate(matched):
        nxt = matched[i + 1]["start_page"] if i + 1 < len(matched) else None
        r["end_page"] = (nxt - 1) if nxt else None
    return matched


@app.route("/api/apply_lesson", methods=["POST"])
def api_apply_lesson():
    """将预览中的课程数据（可能已编辑）保存到磁盘并进入课程。"""
    payload = request.get_json(silent=True) or {}
    edited_plan = payload.get("plan")   # 前端可能已编辑过 units/key_points 等

    preview_plan = ACTIVE_LESSON.get("preview_plan")
    preview_topic = ACTIVE_LESSON.get("preview_topic")
    # topic 多级回退：前端显式传值 > 预览主题 > 教案内 topic；
    # 并防御"主题被写成了目录名"（YYYYMMDD_HHMMSS_xxx_xxxx 形态）的脏数据
    plan_topic = ""
    if isinstance(preview_plan, dict):
        plan_topic = str(preview_plan.get("topic") or "").strip()
    topic = str(payload.get("topic") or preview_topic or plan_topic or "").strip()
    if not topic or re.match(r"^\d{8}_\d{6}_\S+_\w{4}$", topic):
        topic = plan_topic or preview_topic or ""
        topic = str(topic or "").strip()

    if not preview_plan or not topic:
        return jsonify({"error": "没有可应用的课程预览，请重新备课"}), 400

    # 用前端编辑后的 plan 覆盖（若有）
    plan_to_save = edited_plan if isinstance(edited_plan, dict) else preview_plan

    cfg = load_config()
    lesson_folder = build_lesson_folder_name(topic)
    ensure_lesson_files(lesson_folder)
    lesson_dir = ensure_lesson_dir(lesson_folder)
    # 若备课时有上传课程资料，保存为 source_document.md（进入课程后作为背景资料被 AI 读取）
    src_doc = (ACTIVE_LESSON.get("preview_doc_markdown") or "").strip()
    if src_doc:
        try:
            (lesson_dir / "source_document.md").write_text(src_doc, encoding="utf-8")
            print(f"[apply_lesson] 已保存课程资料 source_document.md ({len(src_doc)} 字符)", flush=True)
        except Exception as exc:
            print(f"[apply_lesson] 保存 source_document.md 失败: {exc}", flush=True)
    units = plan_to_save.get("units", [])

    # V4.1：整本教材 PDF —— 保存原文件 + 目录，按单元匹配章节"按需提取"正文到 section_N
    #（备课预览阶段只提取了目录，正文在这里才按章节落地，避免整本解析等待）
    try:
        pdf_files = ACTIVE_LESSON.get("preview_pdf_files") or []
        if pdf_files and units:
            pdf_info = pdf_files[0]
            pdf_src = Path(pdf_info.get("path") or "")
            if pdf_src.exists() and pdf_src.is_file():
                pdf_name = pdf_info.get("name") or "source_document.pdf"
                pdf_target = lesson_dir / pdf_name
                try:
                    pdf_target.write_bytes(pdf_src.read_bytes())
                except Exception:
                    pdf_target = lesson_dir / "source_document.pdf"
                    pdf_target.write_bytes(pdf_src.read_bytes())
                toc = pdf_info.get("toc") or []
                try:
                    (lesson_dir / "pdf_toc.json").write_text(
                        json.dumps({"source": pdf_name, "toc": toc}, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                print(f"[apply_lesson] 教材已保存: {pdf_name}（目录 {len(toc)} 条），按单元按需提取章节…", flush=True)
                matches = _match_pdf_toc_to_units(units, toc)
                if matches:
                    for m in matches:
                        try:
                            sec_dir = unit_dir(lesson_folder, m["unit_index"])
                            sec_dir.mkdir(parents=True, exist_ok=True)
                            chap_text = convert_document_to_markdown(
                                str(pdf_target), start_page=m["start_page"], end_page=m["end_page"]
                            )
                            if chap_text.strip():
                                page_desc = f"{m['start_page']}-{m['end_page']}" if m["end_page"] else f"{m['start_page']}-末"
                                (sec_dir / "source_document.md").write_text(
                                    f"# 第 {m['unit_index'] + 1} 课（教材第 {page_desc} 页）\n\n{chap_text}",
                                    encoding="utf-8",
                                )
                                print(f"[apply_lesson] 单元{m['unit_index'] + 1} 已提取教材页 {page_desc}（{len(chap_text)} 字符）", flush=True)
                        except Exception as exc:
                            print(f"[apply_lesson] 单元{m['unit_index'] + 1} 章节提取失败: {exc}", flush=True)
                else:
                    print("[apply_lesson] PDF 目录与单元标题无匹配，正文按需提取跳过（讲解将回退课程级 source_document.md）", flush=True)
                try:
                    pdf_src.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception as exc:
        print(f"[apply_lesson] 整本教材 PDF 处理失败: {exc}", flush=True)

    # 删除/编辑单元后，旧 current_unit / completed_units / welcomed_units 等索引需要重新映射到新 units
    old_units = (ACTIVE_LESSON.get("metadata") or {}).get("units") or []
    old_progress = load_progress(lesson_folder)
    initial_progress = default_progress()
    if old_units and units:
        def _title(u):
            return str((u or {}).get("title") or "").strip()
        old_titles = [_title(u) for u in old_units]
        new_titles = [_title(u) for u in units]
        # 同一标题按出现顺序一一对应；同名重复则按索引顺序
        def _remap_index(old_idx: int) -> int:
            t = old_titles[old_idx] if 0 <= old_idx < len(old_titles) else ""
            if t and t in new_titles:
                return new_titles.index(t)
            # 找不到：按"已删除索引之前的最近存活标题"映射
            # 从 old_idx 向左/右找最近的标题，再取其在 new_titles 的位置
            for delta in range(1, max(len(old_titles), len(new_titles)) + 1):
                for sign in (-1, 1):
                    j = old_idx + sign * delta
                    if 0 <= j < len(old_titles):
                        tj = old_titles[j]
                        if tj and tj in new_titles:
                            return new_titles.index(tj)
            return -1  # 已无对应，标记丢弃

        def _remap_list(lst):
            seen = set()
            out = []
            for x in (lst or []):
                try:
                    oi = int(x)
                except Exception:
                    continue
                ni = _remap_index(oi)
                if ni >= 0 and ni not in seen:
                    seen.add(ni)
                    out.append(ni)
            return out

        cur_old = int(old_progress.get("current_unit", 0) or 0)
        cur_new = _remap_index(cur_old) if 0 <= cur_old < len(old_units) else -1
        if cur_new < 0:
            cur_new = max(0, min(cur_old, len(units) - 1))
        initial_progress["current_unit"] = cur_new
        initial_progress["completed_units"] = _remap_list(old_progress.get("completed_units"))
        initial_progress["welcomed_units"] = _remap_list(old_progress.get("welcomed_units"))
        # 保留已做题/得分历史（与单元对齐无关）
        for k in ("completed_quizzes", "score_history", "code_attempts", "last_access"):
            if k in old_progress:
                initial_progress[k] = old_progress[k]
    save_metadata(
        lesson_dir,
        {
            "lesson_id": lesson_folder,   # 唯一 ID = 目录名（毫秒级时间戳+随机后缀保证唯一）
            "course_name": lesson_folder,
            "topic": topic,
            "assistant_name": ACTIVE_LESSON.get("preview_assistant_name", "艾琳老师"),
            "personality_prompt": ACTIVE_LESSON.get("preview_personality_prompt", ""),
            "tts_voice": ACTIVE_LESSON.get("preview_tts_voice", "zh-CN-XiaoxiaoNeural"),
            "tts_cloud_voice": ACTIVE_LESSON.get("preview_tts_cloud_voice", ""),
            "voice_config": {"voice": ACTIVE_LESSON.get("preview_tts_voice", ""), "enabled": bool(cfg.get("tts_enabled", False))},
            "syllabus": plan_to_save.get("syllabus", ""),
            "key_points": plan_to_save.get("key_points", []),
            "resources": plan_to_save.get("resources", []),
            "quiz_preset": plan_to_save.get("quiz_preset", []),
            "units": units,
            "has_units": bool(units),
        },
    )

    # 把"教案骨架"（每个 unit 的 target + modules）抽出来单独存为 syllabus.json，
    # 讲课阶段 app.py 的 _lesson_system_prompt 会读这份 JSON 作为强引导。
    # 即便 plan_to_save 里 units 已有 target/modules，这里也做一次规范化（含 fallback）。
    try:
        syllabus_payload = _build_syllabus_payload(plan_to_save, units)
        (lesson_dir / "syllabus.json").write_text(
            json.dumps(syllabus_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        print(f"[apply_lesson] 写入 syllabus.json 失败: {exc}", flush=True)

    # V4：备课同时生成知识图谱（写入 lessons/{course}/knowledge_graph.json）
    try:
        kg_graph = generate_knowledge_graph(plan_to_save)
        if kg_graph.get("nodes"):
            kg_save_graph(lesson_folder, kg_graph)
            print(f"[apply_lesson] 知识图谱已生成: {len(kg_graph['nodes'])} 节点 / {len(kg_graph['edges'])} 边", flush=True)
    except Exception as exc:
        print(f"[apply_lesson] 生成知识图谱失败: {exc}", flush=True)

    # 写入进度文件
    save_progress(lesson_folder, initial_progress)

    # 清除预览状态
    ACTIVE_LESSON["preview_plan"] = None
    ACTIVE_LESSON["preview_topic"] = None
    ACTIVE_LESSON["preview_doc_markdown"] = None
    ACTIVE_LESSON["preview_pdf_files"] = None
    ACTIVE_LESSON["preview_assistant_name"] = None
    ACTIVE_LESSON["preview_personality_prompt"] = None
    ACTIVE_LESSON["preview_tts_voice"] = None
    ACTIVE_LESSON["preview_tts_cloud_voice"] = None

    # 切换到新课程
    ACTIVE_LESSON["folder"] = lesson_folder
    ACTIVE_LESSON["resources"] = plan_to_save.get("resources", [])
    ACTIVE_LESSON["prepared"] = plan_to_save
    ACTIVE_LESSON["conversation"] = load_conversation(lesson_folder)
    ACTIVE_LESSON["progress"] = load_progress(lesson_folder)

    return jsonify({"lesson_folder": lesson_folder, "plan": plan_to_save})


def _regen_doc_suffix() -> str:
    """重新备课时，若预览阶段上传了课程资料，把文档摘要附给模型参考。"""
    doc = (ACTIVE_LESSON.get("preview_doc_markdown") or "").strip()
    if not doc:
        return ""
    return "\n\n【用户上传的课程资料（摘要，前 3000 字）】\n" + doc[:3000]


def _merge_regen_plan(edited_plan: dict, ai_plan: dict) -> dict:
    """把 AI 修订结果合并回用户编辑后的 plan。

    以用户编辑后的 plan 为骨架（课时数量、标题、摘要、要点绝对以用户为准），
    只采纳 AI 补充的缺失字段（modules / core_formulas / contrasts 等），
    确保即使 AI 不遵守"数量一致"要求，用户编辑也绝不丢失。
    """
    merged = json.loads(json.dumps(edited_plan, ensure_ascii=False))
    if not isinstance(ai_plan, dict):
        return merged
    if ai_plan.get("topic"):
        merged["topic"] = ai_plan["topic"]
    if ai_plan.get("target"):
        merged["target"] = ai_plan["target"]
    if ai_plan.get("syllabus"):
        merged["syllabus"] = ai_plan["syllabus"]
    # 全局 key_points：保留用户的，追加 AI 新增的（去重）
    user_kp = list(merged.get("key_points") or [])
    ai_kp = list(ai_plan.get("key_points") or []) if isinstance(ai_plan.get("key_points"), list) else []
    merged["key_points"] = user_kp + [kp for kp in ai_kp if kp not in user_kp]
    if ai_plan.get("resources") and not merged.get("resources"):
        merged["resources"] = ai_plan["resources"]
    # 按标题匹配，用 AI 补齐用户 unit 缺失的字段；标题对不上时按索引位置回退
    ai_units = [u for u in (ai_plan.get("units") or []) if isinstance(u, dict)]
    ai_units_by_title = {str(u.get("title", "")).strip(): u for u in ai_units}
    for i, u in enumerate(merged.get("units") or []):
        if not isinstance(u, dict):
            continue
        au = ai_units_by_title.get(str(u.get("title", "")).strip())
        if au is None and i < len(ai_units):
            au = ai_units[i]  # 标题对不上时按位置回退（AI 应保持课时顺序）
        if not au:
            continue
        for f in ("modules", "core_formulas", "contrasts", "gateway_questions",
                  "contribution_to_target", "source_files"):
            if au.get(f) and not u.get(f):
                u[f] = au[f]
    return merged


def _regen_exam_suffix() -> str:
    """重新备课时注入标准试卷（若当前课程已上传）：教案讲解必须覆盖试卷知识点。"""
    try:
        paper = load_exam_paper(ACTIVE_LESSON.get("folder"))
        qs = paper.get("questions") or []
        if not qs:
            return ""
        _type_cn = {"single": "单选", "multiple": "多选", "boolean": "判断", "fill": "填空", "essay": "简答"}
        lines = ["\n6. 课程存在一份期末标准试卷（课程结束时要以此卷测验），整个教案的讲解、示例、练习都要覆盖这些题目考察的知识点，并在全局 key_points 与各单元内容中显式体现。试卷如下："]
        for i, q in enumerate(qs):
            lines.append(f"{i + 1}. [{_type_cn.get(str(q.get('type')), '题')}] {str(q.get('question') or '')}（标准答案：{str(q.get('answer') or '')}）")
        return "\n".join(lines)
    except Exception:
        return ""


@app.route("/api/regenerate_lesson", methods=["POST"])
def api_regenerate_lesson():
    """重新备课：把用户编辑后的 plan 发给云端模型重新生成（保留编辑意图）。"""
    payload = request.get_json(silent=True) or {}
    edited_plan = payload.get("plan")
    topic = ACTIVE_LESSON.get("preview_topic") or (edited_plan or {}).get("topic", "课程")

    cfg = load_config()
    api_key = (cfg.get("cloud_api_key") or cfg.get("siliconflow_api_key") or "").strip()
    if not api_key:
        return jsonify({"error": "未配置云端 API Key，无法重新备课"}), 400

    base_url = (cfg.get("cloud_base_url") or "https://api.siliconflow.cn/v1").rstrip("/")
    if not base_url.endswith("/chat/completions"):
        url = f"{base_url}/chat/completions"
    else:
        url = base_url
    model = (cfg.get("cloud_model") or cfg.get("siliconflow_model") or "deepseek-ai/DeepSeek-V3").strip()
    enable_search = bool(cfg.get("enable_search", True))

    # 从前端传来的编辑后 plan 提取用户编辑结果（完整保留用户的删除/标题/摘要/要点）
    edited_units = edited_plan.get("units", []) if isinstance(edited_plan, dict) else []
    edited_kp = (edited_plan.get("key_points") or []) if isinstance(edited_plan, dict) else []

    # 对比原始 plan，找出用户删除的课时（原始有、当前没有），明确告知 AI 不要重新生成
    deleted_titles = []
    original_plan = payload.get("original_plan")
    if isinstance(original_plan, dict):
        orig_units = original_plan.get("units") or []
        current_titles = {str(u.get("title", "")).strip() for u in edited_units}
        deleted_titles = [
            str(u.get("title", "")).strip()
            for u in orig_units
            if str(u.get("title", "")).strip() and str(u.get("title", "")).strip() not in current_titles
        ]

    # 把用户已确认的教案精简为传给 AI 的骨架（保留用户可编辑字段 + 已有结构）
    def _compact_plan_for_regen(plan: dict) -> dict:
        compact = {
            "topic": plan.get("topic", ""),
            "target": plan.get("target", ""),
            "key_points": plan.get("key_points") or [],
            "units": [],
        }
        for u in plan.get("units") or []:
            compact["units"].append({
                "title": u.get("title", ""),
                "target": u.get("target", ""),
                "summary": u.get("summary", ""),
                "key_points": u.get("key_points") or [],
                "core_formulas": u.get("core_formulas") or [],
                "contrasts": u.get("contrasts") or [],
                "source_files": u.get("source_files") or [],
            })
        return compact

    compact_plan = _compact_plan_for_regen(edited_plan) if isinstance(edited_plan, dict) else {}
    plan_json = json.dumps(compact_plan, ensure_ascii=False, indent=1)

    # 让云端模型以用户教案为骨架完成修订（保留编辑意图，补齐缺失字段）
    regen_payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一位顶级学科专家与课程设计师。用户已对课程教案做了修改（删除/新增课时、"
                    "改写标题、摘要、核心概念等），你的任务是【在用户已确认的教案基础上完成修订】，"
                    "而不是推倒重来。请严格以 JSON 格式返回完整教案：\n"
                    "{\n"
                    '  "topic": "主题",\n'
                    '  "target": "整体课程目标",\n'
                    '  "syllabus": "整体章节大纲（Markdown，### 标记每课）",\n'
                    '  "key_points": ["全局核心概念1", ...],\n'
                    '  "units": [{"title":"第1课标题","target":"本课目标","summary":"...",'
                    '"modules":[{"id":"m1","title":"...","concept":"...","example":"...","anchor":"...",'
                    '"interaction":"...","action":"..."}],"key_points":[...],'
                    '"core_formulas":[{"name":"...","formula":"...","note":"..."}],'
                    '"contrasts":[{"a":"...","b":"...","difference":"..."}],'
                    '"gateway_questions":["...","..."],"contribution_to_target":"...",'
                    '"source_files":[{"title":"...","url":"...","type":"...","platform":"..."}]}],\n'
                    '  "resources": []\n'
                    "}\n"
                    "硬性要求：\n"
                    "1. 课时数量必须等于用户教案中的课时数量，不得凭空增删。\n"
                    "2. 用户已填写的 title / summary / key_points / target 必须原样保留，"
                    "只在此基础上扩充缺失字段（modules / core_formulas / contrasts / gateway_questions 等）。\n"
                    "3. 用户明确删除的课时（见用户消息中的清单）绝对不要重新生成。\n"
                    "4. 每个 unit 的 modules 3-6 个，每个含 concept/example/anchor/interaction/action；"
                    "key_points 至少 3 个；source_files 尽量给真实链接（找不到真实链接不要编造）。\n"
                    "5. 全局 key_points 至少 4 个；resources 至少 3 个高质量链接。"
                    + _regen_exam_suffix()
                ),
            },
            {
                "role": "user",
                "content": (
                    f"请为【{topic}】修订教案。这是用户当前已确认的教案"
                    "（包含用户的删除、标题/摘要/核心概念修改）：\n"
                    f"{plan_json}\n"
                    + (("用户删除了以下课时，请勿重新生成：" + "、".join(deleted_titles) + "\n") if deleted_titles else "")
                    + "请在保留上述全部内容的基础上补齐缺失字段，输出完整教案 JSON。"
                    + _regen_doc_suffix()
                ),
            },
        ],
        "enable_search": enable_search,
        "max_tokens": 8192,
        "temperature": 0.7,
    }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = requests.post(url, headers=headers, json=regen_payload, timeout=300)
        resp.raise_for_status()
        result = resp.json()
        content = result["choices"][0]["message"]["content"]
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        raw_plan = json.loads(text.strip())
        # 合并兜底：以用户编辑后的 plan 为骨架，只采纳 AI 补充的缺失字段，保证用户编辑不丢失
        new_plan = _merge_regen_plan(edited_plan, raw_plan) if isinstance(raw_plan, dict) else edited_plan
        # 规范化 units（补齐模块骨架等字段）
        if isinstance(new_plan, dict):
            units = [_normalize_unit(u, i) for i, u in enumerate(new_plan.get("units") or [])]
            new_plan["units"] = units
    except Exception as e:
        print(f"[REGEN-ERROR] {e}", flush=True)
        return jsonify({"error": f"重新备课失败: {e}"}), 500

    # 更新预览状态
    ACTIVE_LESSON["preview_plan"] = new_plan
    lesson_folder = build_lesson_folder_name(topic)

    return jsonify({"lesson_folder": lesson_folder, "plan": new_plan})


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
        # 视频类资源（B站/网易公开课）无需下载：标记已跳过，前端可点链接打开
        if str(resource.get("type", "")).lower() == "video":
            statuses.append({
                "index": index, "title": resource.get("title", "unnamed"), "path": "",
                "status": "ok", "skipped_video": True, "url": resource.get("url", ""),
                "platform": resource.get("platform", ""), "unit_index": unit_index,
            })
            continue
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


# ==================== 标准试卷：上传 / 解析 / 读取 ====================

_EXAM_PAPER_FORMAT_HINT = (
    "推荐格式（题目与标准答案一起）：\n"
    "1. Python 中 print() 的作用是？\n"
    "A. 打印输出  B. 定义变量\n"
    "答案：A\n\n"
    "2.（多选）以下哪些是合法标识符？\n"
    "A. _abc  B. 1abc  C. x_y\n"
    "答案：A,C\n\n"
    "【判断题】3. 元组可以修改。\n"
    "答案：错\n\n"
    "【简答题】4. 简述列表与元组的区别。\n"
    "答案：列表可变，元组不可变。\n\n"
    "也支持 JSON：[\"question\":\"...\",\"answer\":\"...\",\"type\":\"single|multiple|boolean|fill|essay\",\"options\":[...]]"
)

_EXAM_QUESTION_TYPE_HINTS = {
    "单选": "single", "选择题": "single",
    "多选": "multiple",
    "判断": "boolean", "判断题": "boolean",
    "填空": "fill", "填空题": "fill",
    "简答": "essay", "问答": "essay", "简答题": "essay", "论述": "essay",
    "名词解释": "essay", "计算": "essay", "解答": "essay",
}


def _exam_type_from_label(label: str) -> Optional[str]:
    if not label:
        return None
    for k, v in _EXAM_QUESTION_TYPE_HINTS.items():
        if k in label:
            return v
    return None


def _finalize_exam_question(q: Dict[str, Any]) -> None:
    """收尾归一化：推断题型（多选/判断/简答/填空）。"""
    q["question"] = (q.get("question") or "").strip()
    q["answer"] = (q.get("answer") or "").strip()
    q["explanation"] = (q.get("explanation") or "").strip()
    q["options"] = [o for o in q.get("options", []) if (o.get("text") or "").strip() or (o.get("label") or "").strip()]
    if not q.get("type"):
        q["type"] = "single"
    ans = q["answer"].upper()
    if q["type"] == "single" and re.match(r"^[A-H](?:\s*[,，、;；]\s*[A-H])+$", ans):
        q["type"] = "multiple"
    if q["type"] == "single" and not q["options"] and re.match(r"^[对错正确错误√×TtFf]$", ans):
        q["type"] = "boolean"
    if q["type"] == "boolean" and not q["options"]:
        # 判断题补默认"对/错"选项（答案保持中文语义，供批改比对）
        q["options"] = [{"label": "A", "text": "对"}, {"label": "B", "text": "错"}]
    if q["type"] == "single" and not q["options"] and len(q["answer"]) >= 8:
        q["type"] = "essay"
    if not q["options"] and not q["answer"]:
        q["type"] = "fill"


def _exam_question_usable(q: Dict[str, Any]) -> bool:
    return bool((q.get("question") or "").strip()) and bool((q.get("answer") or "").strip())


def parse_exam_paper_text(text: str) -> List[Dict[str, Any]]:
    """从试卷文本解析题目列表（题干/选项/答案/解析）。

    支持常见结构：
      - 1. 题干 / A. 选项 / 答案：X / 解析：...
      - 【单选题】【多选题】【判断题】【填空题】【简答题】题号 题干
      - JSON 数组或 {"questions": [...]}（自动识别）
    """
    if not text or not text.strip():
        return []
    stripped = text.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            return _normalize_exam_paper_json(json.loads(stripped))
        except Exception:
            pass  # 非 JSON，继续文本解析

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    question_re = re.compile(r"^(?:第\s*)?(\d+)\s*[.、)）:：]\s*(.*)$")
    option_re = re.compile(r"^([A-H])\s*[.、)）]\s*(.*)$")
    combined_type_re = re.compile(r"^[【\[]\s*([^】\]]+)[】\]]\s*(?:第\s*)?(\d+)\s*[.、)）:：]\s*(.*)$")
    answer_re = re.compile(r"^(?:参考|标准)?答案\s*[:：]\s*(.*)$")
    explanation_re = re.compile(r"^解析\s*[:：]\s*(.*)$")

    questions: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    pending_type: Optional[str] = None

    def _start_question(stem: str, qtype: Optional[str] = None) -> Dict[str, Any]:
        nonlocal cur
        if cur is not None:
            _finalize_exam_question(cur)
            if _exam_question_usable(cur):
                questions.append(cur)
        cur = {
            "type": qtype or pending_type or "single",
            "question": stem.strip(),
            "options": [],
            "answer": "",
            "explanation": "",
        }

    for ln in lines:
        # 题型标记 + 题号同行："【多选题】2. 题干"
        m_comb = combined_type_re.match(ln)
        if m_comb:
            _start_question(m_comb.group(3), _exam_type_from_label(m_comb.group(1)) or pending_type)
            pending_type = None
            continue
        # 题型标记整行：【单选题】 / 【多选题】
        m_type = re.match(r"^[【\[]\s*([^】\]]+)[】\]]\s*$", ln)
        if m_type:
            t = _exam_type_from_label(m_type.group(1))
            if t:
                pending_type = t
            continue

        m_ans = answer_re.match(ln)
        if m_ans:
            if cur is not None:
                cur["answer"] = (m_ans.group(1) or "").strip()
            continue
        m_exp = explanation_re.match(ln)
        if m_exp:
            if cur is not None:
                cur["explanation"] = (m_exp.group(1) or "").strip()
            continue

        m_opt = option_re.match(ln)
        if m_opt:
            if cur is None:
                _start_question("")
            # 支持一行多选项："A. xx B. yy C. zz"
            opts = []
            matches = list(re.finditer(r"([A-H])\s*[.、)）]\s*", ln))
            for i, mo in enumerate(matches):
                start = mo.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(ln)
                opts.append({"label": mo.group(1), "text": ln[start:end].strip()})
            cur["options"].extend(opts)
            continue

        m_q = question_re.match(ln)
        if m_q:
            _start_question(m_q.group(2))
            pending_type = None
            continue

        # 普通行：续行归属（题干换行 / 选项文本换行）
        if cur is not None:
            if cur["options"] and len(ln) < 60:
                cur["options"][-1]["text"] += ln
            else:
                cur["question"] = (cur["question"] + " " if cur["question"] else "") + ln

    if cur is not None:
        _finalize_exam_question(cur)
        if _exam_question_usable(cur):
            questions.append(cur)
    return questions


def _normalize_exam_paper_json(raw: Any) -> List[Dict[str, Any]]:
    """JSON 试卷归一化：支持数组或 {questions:[...]}，中英文字段兼容。"""
    items = raw if isinstance(raw, list) else (raw or {}).get("questions") or []
    out: List[Dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        t_raw = str(it.get("type") or it.get("question_type") or "single").strip()
        q = {
            "type": _map_exam_type(t_raw),
            "question": str(it.get("question") or it.get("题干") or "").strip(),
            "answer": str(it.get("answer") or it.get("标准答案") or it.get("答案") or "").strip(),
            "explanation": str(it.get("explanation") or it.get("解析") or "").strip(),
            "options": [],
        }
        opts = it.get("options") or it.get("选项")
        if isinstance(opts, list):
            for o in opts:
                if isinstance(o, dict):
                    q["options"].append({"label": str(o.get("label") or o.get("key") or ""), "text": str(o.get("text") or o.get("value") or "")})
                else:
                    q["options"].append({"label": "", "text": str(o)})
        elif isinstance(opts, dict):
            for k, v in opts.items():
                q["options"].append({"label": k, "text": str(v)})
        _finalize_exam_question(q)
        if _exam_question_usable(q):
            out.append(q)
    return out


def _map_exam_type(t: str) -> str:
    t = (t or "").strip().lower()
    if t in ("single", "单选", "单选题", "choice", "选择题"):
        return "single"
    if t in ("multiple", "多选", "多选题"):
        return "multiple"
    if t in ("boolean", "bool", "判断", "判断题", "true/false"):
        return "boolean"
    if t in ("fill", "填空", "填空题", "blank"):
        return "fill"
    if t in ("essay", "简答", "简答题", "问答", "论述", "主观", "subjective"):
        return "essay"
    return "single"


def load_exam_paper(lesson_folder: str | None) -> Dict[str, Any]:
    """读取课程的标准试卷（exam/exam_paper.json）。"""
    if not lesson_folder:
        return {}
    try:
        p = ensure_lesson_dir(lesson_folder) / "exam" / "exam_paper.json"
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[exam_paper] 读取失败: {exc}", flush=True)
    return {}


@app.route("/api/lesson/<path:lesson_folder>/exam-paper", methods=["GET", "POST"])
def api_exam_paper(lesson_folder: str):
    """标准试卷管理：GET 查看 / POST 上传解析。"""
    if request.method == "GET":
        paper = load_exam_paper(lesson_folder)
        return jsonify({"ok": True, "count": len(paper.get("questions") or []), "source": paper.get("source", ""), "questions": paper.get("questions", [])})

    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "message": "未收到文件"}), 400
    safe = secure_filename(f.filename) or "exam_paper"
    ext = Path(safe).suffix.lower()
    data = f.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        return jsonify({"ok": False, "message": "文件过大，最大支持 10MB"}), 400
    text = ""
    if ext in (".txt", ".md", ".json"):
        text = data.decode("utf-8", errors="replace")
    elif ext in (".pdf", ".docx", ".doc", ".pptx", ".ppt"):
        tmp = Path(tempfile.gettempdir()) / f"exam_{int(time.time() * 1000)}{ext}"
        try:
            tmp.write_bytes(data)
            text = convert_document_to_markdown(tmp)
        except Exception as exc:
            return jsonify({"ok": False, "message": f"文档解析失败: {exc}"}), 400
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    else:
        return jsonify({"ok": False, "message": f"不支持的文件类型 {ext or '未知'}，支持 .txt/.md/.json/.pdf/.docx/.ppt"}), 400
    if not text.strip():
        return jsonify({"ok": False, "message": "未能从文件中提取文本内容"}), 400
    try:
        questions = parse_exam_paper_text(text)
    except Exception as exc:
        return jsonify({"ok": False, "message": f"解析失败: {exc}"}), 400
    if not questions:
        return jsonify({
            "ok": False,
            "message": "未识别出题目。请确保每题包含题干与'答案：'行（参考下方格式），或使用 JSON 格式。",
            "format_hint": _EXAM_PAPER_FORMAT_HINT,
        }), 400
    exam_dir = ensure_lesson_dir(lesson_folder) / "exam"
    exam_dir.mkdir(parents=True, exist_ok=True)
    (exam_dir / "exam_paper.json").write_text(
        json.dumps({"source": safe, "count": len(questions), "questions": questions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        (exam_dir / safe).write_bytes(data)
    except Exception:
        pass
    print(f"[exam_paper] 课程 {lesson_folder} 上传试卷 {safe}: {len(questions)} 题", flush=True)
    return jsonify({"ok": True, "count": len(questions), "questions": questions, "format_hint": _EXAM_PAPER_FORMAT_HINT})


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
    """将过长文本在句子边界切分，保护 LaTeX 公式与代码块（```...```）不被截断。"""
    if len(text) <= max_chars:
        return [text]

    segments: List[str] = []
    buf = ""
    in_display = False
    in_inline = False
    paren_depth = 0
    bracket_depth = 0
    in_code_block = False  # ``` 围栏内部不切分

    i = 0
    while i < len(text):
        # 检测代码块围栏（``` 切换状态，围栏本身计入 buf）
        if text[i:i + 3] == '```':
            was_open = in_code_block
            in_code_block = not in_code_block
            buf += '```'
            i += 3
            # 代码块结束：若已累积足够内容则整段 flush，
            # 让闭合围栏留在本段，避免句号切分把 ``` 拆到下一段
            if was_open and len(buf) >= max_chars // 2:
                segments.append(buf.strip())
                buf = ""
            continue
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
        protected = in_latex or in_code_block

        # 在句子边界切分（不在 LaTeX/代码块内，且已积累足够内容）
        if not protected and char in '。！？\n；' and len(buf) >= max_chars // 2:
            segments.append(buf.strip())
            buf = ""

        # 强制切分：达到 max_chars 且不在 LaTeX/代码块内
        if len(buf) >= max_chars and not protected:
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


_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_CODE_PH_RE = re.compile(r"\x00BLOCK(\d+)\x00")
# LaTeX 公式块（$$...$$ / $...$ / \(...\) / \[...\]）：分段时与代码块同样保护，
# 避免公式里的 \c（如 \cdot / \cancel）被误判为分段标记 \c 拦腰切断。
_LATEX_BLOCK_RE = re.compile(
    r"\$\$[\s\S]*?\$\$"          # 块级 $$ ... $$
    r"|\\\[[\s\S]*?\\\]"         # 块级 \[ ... \]
    r"|\$[^$\n]*\$"              # 行内 $ ... $（不跨行）
    r"|\\\([\s\S]*?\\\)"         # 行内 \( ... \)
)
# 存档清洗用的 LaTeX 占位（与 _split_by_marker_protecting_code 分开管理，避免并发/复用串扰）
_CLEAN_HOLD: List[str] = []

def _hold_for_clean(m: "re.Match[str]") -> str:
    _CLEAN_HOLD.append(m.group(0))
    return f"\x00BLOCK{len(_CLEAN_HOLD) - 1}\x00"


def _split_by_marker_protecting_code(text: str, marker: str) -> List[str]:
    """按分段标记（\\c 等）切分，但保护代码块与 LaTeX 公式：两者整体保留，不被切碎。

    实现：先把代码块/公式替换为占位符，切分后再还原（保持 re.split 原始语义）。
    """
    if not text:
        return []
    code_blocks: List[str] = []

    def _hold(m):
        code_blocks.append(m.group(0))
        return f"\x00BLOCK{len(code_blocks) - 1}\x00"

    held = _CODE_BLOCK_RE.sub(_hold, text)
    held = _LATEX_BLOCK_RE.sub(_hold, held)
    if marker == "\\c":
        raw = [s for s in re.split(r"\\+c", held) if s.strip()]
    else:
        raw = [s for s in held.split(marker) if s.strip()]

    def _restore(s: str) -> str:
        return _CODE_PH_RE.sub(lambda m: code_blocks[int(m.group(1))], s)

    return [_restore(s) for s in raw]


def _split_paragraphs_protecting_code(text: str) -> List[str]:
    """按空行（段落边界）分段，但保护代码块：代码块整体保留，不被空行切开。

    实现：先把代码块替换为占位符，分段后再还原（保持原 re.split 语义）。
    """
    if not text:
        return []
    code_blocks: List[str] = []

    def _hold(m):
        code_blocks.append(m.group(0))
        return f"\x00BLOCK{len(code_blocks) - 1}\x00"

    held = _CODE_BLOCK_RE.sub(_hold, text)
    raw = [s for s in re.split(r'\n\s*\n', held) if s.strip()]

    def _restore(s: str) -> str:
        return _CODE_PH_RE.sub(lambda m: code_blocks[int(m.group(1))], s)

    return [_restore(s) for s in raw]


def _repair_code_fence_markers(text: str, seg_marker: str) -> str:
    """兜底修复：模型误把分段标记输出成各种乱码形式时的清洗。

    本地模型常把提示词里的 `\\c` 理解成代码块语言标记（如输出 ```c / ```plaintext），
    甚至写成裸 `c` 单字符行，导致分段错乱、Markdown 渲染错乱。

    策略（栈式配对）：
    - ```<lang>（带语言标记）只能作【开】围栏，裸 ``` 依据当前状态开/闭；
    - 扫描后未配成对的围栏视为"被误用的分段标记"，替换为 seg_marker；
    - 正常成对的代码块（```lang ... ```）保持原样；
    - 额外：把"独立成行的裸 c"也当孤立分段标记（模型常把 `\\c` 转义后写成 c）。
    """
    import re as _re
    if not text or seg_marker == "```":
        return text
    lines = text.split("\n")
    fence_idx = [i for i, l in enumerate(lines) if l.strip().startswith("```")]
    if not fence_idx:
        return text

    paired: set[int] = set()
    stack: int | None = None  # 当前未闭合的开围栏行号
    for idx in fence_idx:
        is_lang = _re.match(r"^```\S", lines[idx].strip()) is not None
        if stack is None:
            stack = idx
        elif not is_lang:
            # 裸 ``` 闭合当前代码块
            paired.add(stack)
            paired.add(idx)
            stack = None
        else:
            # 已开又见 ```<lang> → 前面那个是误用标记，新围栏另起
            stack = idx

    out: list[str] = []
    for i, line in enumerate(lines):
        if i in fence_idx and i not in paired:
            out.append(seg_marker)   # 孤立围栏 → 还原为分段标记
        else:
            out.append(line)

    # 额外：识别"独立成行的裸 c"——模型常把 \c 转义丢失后写成单字符行
    # （在 HTML 提示词背景下，模型对反斜杠敏感，常常丢掉）
    if seg_marker == "\\c":
        fixed: list[str] = []
        for l in out:
            if l.strip() == "c":
                fixed.append("\\c")
            else:
                fixed.append(l)
        out = fixed

    return "\n".join(out)


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


# 工具调用标记正则：匹配 [TOOL:start_exam] 或 [TOOL:next_unit]（旧格式，无参数）
_TOOL_RE = re.compile(r"\[TOOL:\s*(start_exam|next_unit)\s*\]", re.IGNORECASE)
# 工具调用标记正则：匹配 [TOOL:{"type":"show_terminal",...}]（新格式，参数化 JSON 工具）
# 注意：必须非贪婪 + DOTALL，否则贪婪会把 JSON 内部的 } 吞掉导致匹配失败
_TOOL_JSON_RE = re.compile(r"\[TOOL:\s*(\{[\s\S]*?\})\s*\]", re.DOTALL)
# 允许的新工具类型（防止模型输出任意 JSON 被误解析）
_VALID_TOOL_TYPES = {"show_terminal", "show_image", "show_board"}


def _strip_tool_markers(text: str) -> str:
    """稳健地剥离 [TOOL:...] 标记（即使内部 JSON 嵌套/不合法），保留其余文本。

    用括号深度计数定位匹配的结束 ]，避免非贪婪正则被嵌套 } / ] 提前截断，
    导致 [TOOL:{...}] 残留在 clean_answer 里被 TTS 原样朗读。
    """
    if not text or "[TOOL" not in text.upper():
        return text
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == "[" and text[i:i + 5].upper() == "[TOOL":
            j, depth = i + 5, 1
            in_string = False
            while j < n:
                c = text[j]
                if in_string:
                    # JSON 字符串内部：转义字符跳过，普通字符不计入括号深度
                    if c == "\\":
                        j += 1
                    elif c == '"':
                        in_string = False
                else:
                    if c == '"':
                        in_string = True
                    elif c in "[{":
                        depth += 1
                    elif c in "]}":
                        depth -= 1
                        if depth == 0:
                            j += 1
                            break
                j += 1
            i = j  # 跳过整个 [TOOL:...] 标记
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _tts_safe_text(text: str) -> str:
    """朗读文本净化：TTS 只读自然语言（可见文本），过滤一切内部标记与 Markdown 语法。

    - 剥离 [TOOL:...] / [ACTION:...] / [EMOTION:...] / [PARAM:...] 及中文括号变体
    - 代码块整体不朗读（三个反引号 / 三个波浪线 / 三个双引号开头的围栏及其内容）
    - 图片 / 链接标记：图片删除；链接保留文字删除 URL
    - 加粗 / 斜体 / 删除线 / 行内反引号 / 标题 / 列表符号等标记删除，保留文字
    """
    if not text:
        return text
    cleaned = _strip_tool_markers(text)
    cleaned = _ACTION_RE.sub("", cleaned)
    cleaned = _EMOTION_RE.sub("", cleaned)
    cleaned = _PARAM_RE.sub("", cleaned)
    cleaned = re.sub(r"【(ACTION|EMOTION|TOOL|PARAM):[^】]*】", "", cleaned)
    # ── 分段标记（\c 等）清理：先保护 LaTeX 公式块，避免公式内 \cdot / \cancel 被误删 ──
    _CLEAN_HOLD.clear()
    held = _LATEX_BLOCK_RE.sub(_hold_for_clean, cleaned)
    held = re.sub(r"\\+c", " ", held)
    cleaned = _CODE_PH_RE.sub(lambda m: _CLEAN_HOLD[int(m.group(1))], held)
    # ── Markdown 可见文本净化 ──
    # 1) 代码块整体不朗读：成对围栏（```lang ... ``` / ~~~ ... ~~~ / """lang ... """）
    cleaned = re.sub(r"```[a-zA-Z0-9_\-+]*\s*\n[\s\S]*?```", " ", cleaned)
    cleaned = re.sub(r"~~~[a-zA-Z0-9_\-+]*\s*\n[\s\S]*?~~~", " ", cleaned)
    cleaned = re.sub(r'"""[^\n]*\n[\s\S]*?"""', " ", cleaned)
    # 2) 残留的单边围栏标记（模型常输出不配对围栏）
    cleaned = re.sub(r"```[^\n]*", " ", cleaned)
    cleaned = re.sub(r"~~~[^\n]*", " ", cleaned)
    cleaned = re.sub(r'"""[^\n]*', " ", cleaned)
    # 3) 图片整个删除；链接 [文字](url) 保留文字删 URL
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cleaned)
    # 4) 加粗 / 斜体 / 删除线标记删除，保留文字
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", cleaned)
    cleaned = re.sub(r"~~([^~]+)~~", r"\1", cleaned)
    # 5) 行内反引号删除（保留内容）；残留散反引号清掉
    cleaned = re.sub(r"`([^`\n]+)`", r"\1", cleaned)
    cleaned = re.sub(r"`+", "", cleaned)
    # 6) 标题标记 / 行首无序列表符号
    cleaned = re.sub(r"(?m)^\s*#{1,6}\s*", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*[-*+]\s+", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# 板书意图兜底：模型正文写公式并说"黑板/板书"但没输出 [TOOL:show_board] 时，
# 自动提取公式/推导行作为黑板内容（黑板上板书），正文保留叙述句。
_BOARD_WORD_RE = re.compile(r"黑板|板书")


def _clean_board_line(s: str) -> str:
    """黑板行规范化：剥 markdown 粗体 `**`，混合行只保留公式，行内公式提升为块公式。

    黑板渲染端只认 `$$...$$` 块公式，行内 `$...$` 与 `**` 会被逐字打出（如
    `**$F_{avg} ...$**` 字面量）。此函数把行内公式提升为块、丢弃非公式叙述。
    """
    s = re.sub(r"\*\*", "", s)                      # 剥 ** 粗体标记
    parts = re.findall(r"\$\$[\s\S]*?\$\$|\$[^$\n]+\$", s)  # 按序提取所有公式
    if not parts:
        return s
    seen_p, out = set(), []
    for p in parts:
        if p.startswith("$$"):
            b = p
        else:
            b = "$$ " + p[1:-1].strip() + " $$"      # 行内公式提升为块公式
        if b not in seen_p:
            seen_p.add(b)
            out.append(b)
    return "\n".join(out)


def _auto_board_from_answer(text: str) -> tuple[str | None, str]:
    """检测模型回复是否包含"想板书却没走工具"的意图。

    Returns:
        (board_content, cleaned_text)：检测到强板书意图时返回黑板内容与清洗后的正文；
        否则返回 (None, 原文)。
    """
    if not text or not _BOARD_WORD_RE.search(text):
        return None, text
    formula_blocks = re.findall(r"\$\$[\s\S]*?\$\$", text)
    formula_inlines = re.findall(r"\$[^$\n]+\$", text)
    if len(formula_blocks) + len(formula_inlines) < 4:
        return None, text
    # 黑板内容 = 公式块行 + 纯公式独占行 + 等号推导行（去重保序）。
    # 只取"整行都是公式/推导"的行，叙述句（含行内公式）不进黑板。
    seen, board_lines = set(), []
    for ln in text.split("\n"):
        s = ln.strip()
        if not s or s in seen:
            continue
        is_block = bool(re.search(r"\$\$[\s\S]*?\$\$", s))          # 含 $$ 块公式
        is_pure_math = bool(re.match(r"^\$[^$].*\$$", s))           # 行首即以 $ 开头（行内公式独占行）
        is_derivation = bool(re.match(r"^[A-Za-z_\\$Δαβγδθλ][\w\\.,\s$^{}()\[\]\-\+*/=]*=", s))  # 公式符号开头 + 等号推导
        if is_block or is_pure_math or is_derivation:
            seen.add(s)
            board_lines.append(_clean_board_line(s))
    board_lines = [l for l in board_lines if l]       # 规范化为空的行丢弃
    if not board_lines:
        return None, text
    content = "\n".join(board_lines)
    # 正文清洗：仅删除被提取的黑板行，保留叙述句（含行内公式）；全被提取时回退原文本
    cleaned = "\n".join(l for l in text.split("\n") if l.strip() not in seen).strip()
    if not cleaned:
        cleaned = text.strip()
    return content, cleaned


# 终端意图兜底：本地小模型（如 qwen3.5:4b）经常不输出 [TOOL:show_terminal]，
# 即使正文里已经写了数值计算。这里从正文保守提取纯数值表达式，
# 自动合成 show_terminal 工具（黑板上写公式、终端算结果的链路）。
# 提取不到安全表达式时返回 None，不打扰正常教学流程。
_CALC_TRIGGER_RE = re.compile(r"计算|算出|结果|等于|是多少|得多少|换算|求一求|算一算|算出来")

_TERM_EXTRACT_SYS = (
    "你是数值表达式提取器。给定一段教学回复，如果其中包含数值计算，"
    "把具体计算式用数字写成 Python 算术表达式（只支持数字 + - * / ** 和括号，负数加括号），"
    "只输出这一个表达式本身，不要 print、不要解释、不要代码块、不要单位、不要变量名。"
    "如果不包含任何数值计算，只输出 NONE。\n"
    "示例：\n"
    "回复：动量变化量 = 0.15 × (-30 - 40) = -10.5 kg·m/s\n"
    "输出：0.15*(-30-40)\n"
    "回复：用动量定理 FΔt = m(v_f - v_i) = 0.15 × (40-(-30)) 计算\n"
    "输出：0.15*(40-(-30))\n"
    "回复：这个公式推导先讲到这，我们下节课继续\n"
    "输出：NONE"
)


def _llm_extract_calc_expr(text: str) -> str | None:
    """二次小模型调用：正则提取不到数值表达式时，让模型吐出纯数值表达式。

    结果仍需过 _safe_expr_eval 白名单校验，防止模型输出变量/函数/注入内容。
    """
    cfg = load_config()
    base_url = (cfg.get("ollama_base_url") or "http://127.0.0.1:11434").rstrip("/")
    model = (cfg.get("ollama_model") or "qwen2.5:7b").strip()
    if not cfg.get("enable_local_ollama", True):
        return None
    try:
        resp = requests.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _TERM_EXTRACT_SYS},
                    {"role": "user", "content": "待提取回复：\n" + text[-1500:]},
                ],
                "stream": False,
                "think": False,
                "options": {"temperature": 0.1, "num_ctx": 4096, "num_predict": 60},
            },
            timeout=40,
        )
        if not resp.ok:
            return None
        content = (resp.json().get("message", {}).get("content") or "").strip()
        content = content.strip("`").strip()
        if not content or content.upper() == "NONE":
            return None
        return content
    except Exception as exc:
        print(f"[TERMINAL-AUTO] 二次提取失败: {exc}", flush=True)
        return None


def _has_binop(expr: str) -> bool:
    """表达式是否含真正的算术运算符（防止二次提取返回孤立常数 -10.5）。"""
    try:
        tree = ast.parse(expr.replace("^", "**"), mode="eval")
    except SyntaxError:
        return False
    return any(isinstance(n, ast.BinOp) for n in ast.walk(tree))


def _safe_expr_eval(expr: str):
    """仅允许纯数值四则/幂运算表达式；任何非法/危险内容返回 None。

    通过 1) 字符白名单 2) AST 节点白名单（无变量/函数/属性）双重校验后再 eval，
    杜绝表达式注入（模型/文本里混入 __import__ 之类）；
    并限制长度与结果量级，防止 999999**999999 这类表达式拖垮终端。
    """
    expr = expr.strip()
    if not expr or len(expr) > 80 or not re.fullmatch(r"[\d+\-*/^(). ]+", expr):
        return None
    py = expr.replace("^", "**")
    try:
        tree = ast.parse(py, mode="eval")
    except SyntaxError:
        return None
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
               ast.USub, ast.UAdd)
    for node in ast.walk(tree):
        if not isinstance(node, allowed):
            return None
    try:
        val = eval(py, {"__builtins__": {}}, {})
    except Exception:
        return None
    if not isinstance(val, (int, float)):
        return None
    try:
        if val != val or abs(val) > 1e15:   # NaN 或量级过大 → 拒绝
            return None
    except Exception:
        return None
    return val


def _value_in_answer(val, text: str) -> bool:
    """提取的表达式计算结果是否与正文出现的某个数值一致（容差内）。

    小模型提取表达式常拼错（如把 -10.5 当操作数），若结果与正文数值对不上，
    说明提取有误，不应把错误结果展示给学生，直接跳过终端。
    """
    try:
        fv = float(val)
    except Exception:
        return False
    for n in re.findall(r"-?\d+(?:\.\d+)?", text):
        try:
            fn = float(n)
        except Exception:
            continue
        if abs(fv - fn) <= 1e-6 * max(1.0, abs(fv)):
            return True
    return False


_EXPR_ITEM = r"-?\d+(?:\.\d+)?"
# 纯数值算术表达式：数字与运算符交替（支持括号、负数、× ÷ ^ − 等）。
# 注意重复组内运算符前要允许右括号 `\)?`——"(-4.5) - (6)" 这类被括号包裹
# 的负数若不支持右括号，匹配会在 -4.5 处被 `)` 阻断而失败（端到端实测复现）。
_EXPR_RE = re.compile(
    r"(?:\(?\s*)?" + _EXPR_ITEM + r"(?:\s*\)?\s*[×÷*/^+\-−]\s*\(?\s*" + _EXPR_ITEM + r"\s*\)?){1,}"
)


def _find_numeric_expr(held: str) -> str | None:
    """找文本中最长的纯数值算术表达式（含 × ÷ ^ − 等），找不到返回 None。"""
    best = None
    for m in _EXPR_RE.finditer(held):
        cand = m.group(0)
        if best is None or len(cand) > len(best):
            best = cand
    if not best:
        return None
    expr = best.replace("−", "-").replace("×", "*").replace("÷", "/")
    expr = expr.replace(" ", "").replace("^", "**")
    if _safe_expr_eval(expr) is None:
        return None
    return expr


def _latex_to_py_math(s: str) -> str:
    """把常见 LaTeX 算术写法翻译为可解析的纯文本，便于提取数值表达式。

    模型常把计算式整个写进 LaTeX 块（如 `\\begin{aligned} &= 0.15 \\times (-30) - (0.15 \\times 40) \\end{aligned}`），
    这类"老师自己写出的计算式"按字面求值即为其本意，可安全提取。
    """
    s = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", s)
    s = s.replace("\\times", "*").replace("\\cdot", "*").replace("\\div", "/")
    s = s.replace("\\,", "").replace("\\!", "").replace("\\;", "")
    s = s.replace("\\quad", " ").replace("\\qquad", " ").replace("\\ ", " ")
    s = s.replace("\\Delta", "d").replace("\\delta", "d")   # 变量名，不参与表达式匹配
    s = s.replace("\\(", " ").replace("\\)", " ").replace("\\[", " ").replace("\\]", " ")
    s = re.sub(r"\\begin\{[^}]*\}|\\end\{[^}]*\}", " ", s)
    # 兜底清掉所有残余反斜杠（LaTeX 换行符 \\ 会在上一步被 "\\ " 替换吃掉一半，
    # 残留的单反斜杠不清理会阻断数值表达式提取）
    s = re.sub(r"\\+", " ", s)
    s = s.replace("&", " ").replace("{", " ").replace("}", " ")
    return s


def _auto_terminal_from_answer(text: str) -> str | None:
    """若回复已含明确数值计算但无 show_terminal 工具，提取表达式生成 python print 代码。

    Returns:
        可执行的 python 代码（如 `print(0.15*(-30-40))`），提取不到则 None。
    """
    if not text or not re.search(r"\d", text):
        return None
    # 先保护 LaTeX，避免在公式内部误匹配（公式里的 × / - 不能当成计算表达式）
    _CLEAN_HOLD.clear()
    held = _LATEX_BLOCK_RE.sub(_hold_for_clean, text)
    # 1) 快速路径A：正文有字面数值表达式（如 "0.15 × (-30-40)"）→ 直接生成终端。
    #    不依赖"计算/结果"关键词——出现了字面计算式本身就值得弹终端算一下。
    expr = _find_numeric_expr(held)
    if expr:
        return f"print({expr})"
    # 2) 快速路径B：数值写在 LaTeX 块里（被保护遮蔽），把 LaTeX 算术翻译后再次提取。
    #    这类是模型自己写出的计算式，按字面求值即为其本意，无需一致性闸门。
    expr = _find_numeric_expr(_latex_to_py_math(text))
    if expr:
        print(f"[TERMINAL-AUTO] LaTeX 表达式提取: {expr}", flush=True)
        return f"print({expr})"
    # 3) 二次路径：正文有计算关键词且数值确实存在，但无字面表达式可提取时，
    #    调用小模型提取纯数值表达式，再经一致性闸门校验（结果须与正文数值吻合）。
    if not _CALC_TRIGGER_RE.search(held):
        return None
    _expr2 = _llm_extract_calc_expr(text)
    if _expr2 and _has_binop(_expr2):
        _val2 = _safe_expr_eval(_expr2)
        # 一致性闸门：计算结果必须与正文中的某个数值吻合，否则认为提取有误，跳过终端
        if _val2 is not None and _value_in_answer(_val2, text):
            print(f"[TERMINAL-AUTO] 二次提取表达式: {_expr2} = {_val2}", flush=True)
            return f"print({_expr2})"
        print(f"[TERMINAL-AUTO] 二次提取结果与正文数值不符，跳过: {_expr2} = {_val2}", flush=True)
    return None


# 用户明确要求打开终端时的意图词（4B 模型对工具协议遵循度低，
# 用户说"开终端"时模型常把代码写进 markdown 代码块冒充，需要后端兜底）
_TERM_REQUEST_RE = re.compile(
    r"开(?:一下|一)?终端|打开终端|拉(?:起|出)?终端|召唤终端|把终端|终端[打拉]|终端怎么"
)


def _extract_fence_blocks(text: str) -> list[tuple[str, str]]:
    """提取回复中的 ```lang\\n...\\n``` 代码块，返回 [(lang, code), ...]。

    lang 缺省或为空字符串时记为 'python'（终端默认语言）。
    """
    blocks = []
    for m in re.finditer(r"```([a-zA-Z0-9_+-]*)[ \t]*\n(.*?)```", text, re.DOTALL):
        code = m.group(2).strip("\n")
        if code.strip():
            blocks.append(((m.group(1).strip().lower() or "python"), code))
    return blocks


# markdown 代码块语言 → 终端支持语言映射
_TERM_LANG_MAP = {
    "bash": "shell", "sh": "shell", "shell": "shell", "powershell": "powershell",
    "js": "javascript", "node": "javascript", "javascript": "javascript",
    "py": "python", "python": "python",
    "rb": "ruby", "ruby": "ruby",
    "pl": "perl", "perl": "perl",
    "php": "php",
    "lua": "lua",
    "r": "r", "rscript": "r",
}


def _map_term_lang(lang: str) -> str:
    return _TERM_LANG_MAP.get(lang.lower(), "python")


def extract_tool_call(text: str) -> tuple[str, str | dict | None]:
    """从 LLM 回复中剥离工具调用标记。

    Returns:
        (clean_text, tool_event)：
          - tool_event 为 'start_exam' / 'next_unit'（旧格式，字符串）
          - 或参数化工具 dict：{"type": "show_terminal", "language": ..., "code": ...}
          - 无工具时为 None。
        若出现多个标记只取第一个；剥离标记后的纯文本用于流式展示与存档。
    """
    if not text:
        return text, None

    # 新格式：参数化 JSON 工具（show_terminal / show_image / show_board）
    m = _TOOL_JSON_RE.search(text)
    if m:
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict) and obj.get("type") in _VALID_TOOL_TYPES:
                cleaned = _TOOL_JSON_RE.sub("", text, count=1)
                cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip() + ("\n" if cleaned.endswith("\n") else "")
                return cleaned.strip(), obj
        except (json.JSONDecodeError, ValueError):
            pass  # 不是合法 JSON，落到旧格式继续尝试

    # 旧格式：[TOOL:start_exam] / [TOOL:next_unit]
    m = _TOOL_RE.search(text)
    if not m:
        # 最终兜底：无论 [TOOL:...] 内容是否合法 JSON / 嵌套多深，都完整剥离，
        # 避免标记残留在正文（尤其被 TTS 原样朗读）
        if "[TOOL" in text.upper():
            return _strip_tool_markers(text), None
        return text, None
    tool_event = m.group(1).lower()
    # 移除该标记及其周围多余空行
    cleaned = _TOOL_RE.sub("", text, count=1)
    # 去掉末尾因剥离留下的空行
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip() + ("\n" if cleaned.endswith("\n") else "")
    return cleaned.strip(), tool_event


def extract_tool_events_all(text: str) -> tuple[str, list]:
    """从回复中提取【所有】工具标记，一次性从正文全部剥离。

    此前 extract_tool_call 只取第一个工具：模型同时输出黑板 + 终端（或图片）时，
    终端/图片被丢弃并残留为正文字面量。本函数支持一次回复多个工具，
    按模型输出顺序返回 [tool_event, ...]（dict 或旧格式字符串 start_exam/next_unit）。

    Returns:
        (clean_text, [tool_event, ...])
    """
    if not text:
        return text, []
    tools: list = []
    for m in _TOOL_JSON_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
            if isinstance(obj, dict) and obj.get("type") in _VALID_TOOL_TYPES:
                tools.append(obj)
        except (json.JSONDecodeError, ValueError):
            continue  # 非法 JSON 标记后续统一剥离
    if tools:
        cleaned = _TOOL_JSON_RE.sub("", text)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip() + ("\n" if cleaned.endswith("\n") else "")
        # 部分标记 JSON 不合法时（未进 tools），也兜底剥掉，避免残留字面量
        if "[TOOL" in cleaned.upper():
            cleaned = _strip_tool_markers(cleaned)
        return cleaned.strip(), tools
    # 旧格式：[TOOL:start_exam] / [TOOL:next_unit]
    m = _TOOL_RE.search(text)
    if m:
        cleaned = _TOOL_RE.sub("", text, count=1)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip() + ("\n" if cleaned.endswith("\n") else "")
        return cleaned.strip(), [m.group(1).lower()]
    # 无新格式工具但残留 [TOOL:...]（模型输出残缺）：兜底剥离，不触发工具
    if "[TOOL" in text.upper():
        return _strip_tool_markers(text), []
    return text.strip(), []


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


# 参数直调标记正则：匹配 [PARAM:ParamAngleX=-15 ParamAngleY=5]（AI 直接控制模型参数）
_PARAM_RE = re.compile(r"\[PARAM:\s*([^\]]+)\]", re.IGNORECASE)


def extract_param_call(text: str):
    """从模型回复中提取 [PARAM:参数名=数值 ...] 参数直调标记。

    返回 (clean_text, params_dict|None)。params_dict 为 {参数名: float}。
    """
    if not text:
        return text, None
    m = _PARAM_RE.search(text)
    if not m:
        return text, None
    params: Dict[str, float] = {}
    for part in m.group(1).replace(",", " ").split():
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        try:
            params[k] = float(v)
        except ValueError:
            continue
    cleaned = _PARAM_RE.sub("", text, count=1)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip()
    return cleaned, (params if params else None)


# ============================
# 语境动作/情绪推断（兜底）：本地小模型常常不输出 [ACTION:]/[EMOTION:] 标记，
# 这里根据正文信号词自动推断动作与情绪，让老师"根据语境做出动作表情"。
# ============================
_CONTEXT_ACTION_RULES = [
    (("恭喜", "太棒了", "非常棒", "真棒", "答对了", "做得很好", "太好了", "了不起", "为你骄傲", "你真厉害", "给你们点赞"), "cheer"),
    (("没错", "说得对", "你说得对", "正是这样", "非常正确", "正是如此", "回答得很对"), "agree"),
    (("不对", "错了", "不是这样", "可不能", "这是不对的", "这样可不行", "再想想看"), "shake"),
    (("让我想想", "想一想", "这是个好问题", "关键在于", "思考一下", "让我思考"), "think"),
    (("有点奇怪", "这很奇怪", "嗯？", "怎么回事", "这是为什么"), "tilt"),
    (("真的吗", "竟然", "原来如此", "居然", "真没想到"), "gasp"),
    (("可惜", "遗憾", "没办法", "唉", "真无奈", "算了"), "sigh"),
    (("同学们好", "大家好", "同学们再见", "下课", "下次见", "我们下次课"), "bow"),
]


def infer_context_action(text: str) -> Optional[str]:
    if not text:
        return None
    for keywords, action in _CONTEXT_ACTION_RULES:
        for kw in keywords:
            if kw in text:
                return action
    return None


def infer_context_emotion(text: str) -> Optional[str]:
    """由语境推断情绪：优先由动作映射，再匹配正文情绪词。"""
    if not text:
        return None
    action = infer_context_action(text)
    action_to_emotion = {
        "cheer": "happy", "agree": "happy", "gasp": "surprised",
        "sigh": "sad", "tilt": "think", "think": "think",
    }
    if action in action_to_emotion:
        return action_to_emotion[action]
    if any(k in text for k in ("太好了", "真棒", "恭喜", "真厉害", "为你开心", "为你们骄傲")):
        return "happy"
    if any(k in text for k in ("抱歉", "遗憾", "可惜", "很遗憾", "唉")):
        return "sad"
    if any(k in text for k in ("真的吗", "竟然", "真没想到")):
        return "surprised"
    return None


# 情绪标记正则：匹配 [EMOTION:happy] / [EMOTION:think] 等
_EMOTION_RE = re.compile(r"\[EMOTION:\s*([a-zA-Z_]+)\s*\]", re.IGNORECASE)
# Open-LLM-VTuber 风格简化标签：直接 [joy] / [sadness] 等
_EMOTION_SHORT_RE = re.compile(r"\[(joy|sadness|anger|fear|disgust|surprise|neutral|smirk|happy|sad|angry|think|surprised)\]", re.IGNORECASE)
# 情绪标签标准化映射（Open-LLM-VTuber + 现有系统都支持）
_EMOTION_ALIAS = {
    "happy": "happy", "joy": "happy",
    "sad": "sad", "sadness": "sad",
    "angry": "angry", "anger": "angry",
    "think": "think",
    "surprised": "surprised", "surprise": "surprised",
    "fear": "fear",
    "disgust": "disgust",
    "neutral": "neutral",
    "smirk": "smirk",
}
VALID_EMOTIONS = set(_EMOTION_ALIAS.keys())


def _normalize_emotion(token: str) -> str | None:
    """标准化情绪名（大小写不敏感，找不到返回 None）。"""
    if not token:
        return None
    t = token.strip().lower()
    return _EMOTION_ALIAS.get(t)


def extract_emotion_call(text: str) -> tuple[str, str | None]:
    """从 LLM 回复中剥离情绪标记（取首个出现的）。

    支持两种格式：
      1. [EMOTION:happy] / [EMOTION:think] （旧格式）
      2. [joy] / [sadness] / [anger] 等（Open-LLM-VTuber 风格简写）

    Returns:
        (clean_text, emotion) — emotion 为标准化后的 happy/sad/angry/think/surprised/neutral/fear/disgust/smirk 或 None。
    """
    if not text:
        return text, None
    # 先找 [EMOTION:xxx]（避免被短标签正则抢匹配）
    m = _EMOTION_RE.search(text)
    if m:
        token = m.group(1)
        emotion = _normalize_emotion(token) or "neutral"
        cleaned = _EMOTION_RE.sub("", text, count=1)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip() + ("\n" if cleaned.endswith("\n") else "")
        return cleaned.strip(), emotion
    # 短标签 [joy] / [sadness]
    m = _EMOTION_SHORT_RE.search(text)
    if not m:
        return text, None
    emotion = _normalize_emotion(m.group(1)) or "neutral"
    cleaned = _EMOTION_SHORT_RE.sub("", text, count=1)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip() + ("\n" if cleaned.endswith("\n") else "")
    return cleaned.strip(), emotion


def extract_emotions_all(text: str) -> list[str]:
    """提取文本中所有情绪标签（按出现顺序），不去除。供流式逐帧检测。"""
    if not text:
        return []
    out: list[str] = []
    for m in _EMOTION_RE.finditer(text):
        e = _normalize_emotion(m.group(1))
        if e:
            out.append(e)
    for m in _EMOTION_SHORT_RE.finditer(text):
        e = _normalize_emotion(m.group(1))
        if e:
            out.append(e)
    return out


def strip_emotion_tags(text: str) -> str:
    """去除所有情绪标记（包括短标签和长标签），保留文本其余内容。"""
    if not text:
        return text
    cleaned = _EMOTION_RE.sub("", text)
    cleaned = _EMOTION_SHORT_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


@app.route("/api/chat", methods=["POST"])
def api_chat():
    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    lesson_folder = payload.get("lesson_folder") or ACTIVE_LESSON.get("folder")
    force_cloud = payload.get("force_cloud", False)
    explain_mode = payload.get("explain_mode", "")
    long_mode = bool(payload.get("long_mode", False))
    attachments = payload.get("attachments") or []
    if not message:
        return jsonify({"error": "message is required"}), 400
    if not lesson_folder:
        return jsonify({"error": "No active lesson selected"}), 400

    ACTIVE_LESSON["folder"] = lesson_folder

    # V4：多 Agent 意图分发（关键词路由；Tutor / 无法识别时回退主聊天流程）
    try:
        from agents import route_message
        _routed = route_message(message, lesson_folder=lesson_folder, learner_id="001")
    except Exception as _v4_exc:
        print(f"[v4] Agent 路由异常，回退主流程: {_v4_exc}", flush=True)
        _routed = None

    if _routed and not _routed.get("fallback_to_chat") and _routed.get("reply"):
        _agent_reply = str(_routed["reply"]).strip()
        _agent_name = str(_routed.get("agent") or "agent")

        def _agent_stream(_reply: str, _name: str):
            _segs = [s.strip() for s in re.split(r"\n\s*\n", _reply) if s.strip()] or [_reply]
            for _s_idx, _seg in enumerate(_segs):
                yield f"data: {json.dumps({'content': _seg, 'done': False, 'segment': _s_idx})}\n\n"
            _audio = local_tts_audio(_tts_safe_text(_reply)[:500]) if _reply else None
            _done: Dict[str, Any] = {
                "content": _reply,
                "done": True,
                "audio_url": _audio,
                "segment_count": len(_segs),
                "v4_agent": _name,
            }
            yield f"data: {json.dumps(_done)}\n\n"

        # 存档 Agent 对话（与主流程格式一致）
        try:
            _conv = load_conversation(lesson_folder)
            _conv.append({"role": "user", "content": message, "timestamp": now_iso()})
            _conv.append({"role": "assistant", "content": _agent_reply, "timestamp": now_iso()})
            save_conversation(lesson_folder, _conv)
            ACTIVE_LESSON["conversation"] = _conv
        except Exception as _v4_conv_exc:
            print(f"[v4] Agent 对话存档失败: {_v4_conv_exc}", flush=True)

        print(f"[v4] Agent 接管: {_agent_name}（fallback_to_chat=False）", flush=True)
        return Response(stream_with_context(_agent_stream(_agent_reply, _agent_name)), mimetype="text/event-stream")

    def generate():
        # 本地 Ollama 与云端都失败时的兜底：改为明确的故障提示，
        # 不再用"示例回复"占位文本（避免误导用户以为是模型思考/示例）。
        fallback_answer = (
            f"（抱歉，老师这次没能给出回答。你问的是：{message}\n\n"
            "本地 Ollama 与云端模型都没有返回有效内容，请检查：\n"
            "① 本地 Ollama 服务是否已启动（设置 → 模型 → 测试连接）\n"
            "② 云端 API Key 是否配置正确\n"
            "③ 当前选择的模型是否可用）"
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

        # 处理聊天附件：图片走识图模型提取内容，文本文件直接读取，合并进提问
        effective_message = message
        if attachments:
            extra_parts = []
            for att in attachments:
                att_type = (att.get("type") or "file").strip().lower()
                att_url = (att.get("url") or "").strip()
                att_name = (att.get("name") or "").strip() or "附件"
                att_content = (att.get("content") or "").strip()
                if att_type == "image":
                    desc = vision_describe(att_url, att_name)
                    extra_parts.append(
                        f"[用户上传图片：{att_name}]\n图片内容：{desc or '（未能识别图片内容）'}"
                    )
                else:
                    body = att_content or "（文件内容为空或无法读取）"
                    extra_parts.append(f"[用户上传文件：{att_name}]\n{body}")
            if extra_parts:
                effective_message = (
                    "用户上传了以下附件，请结合附件内容回答用户的问题：\n\n"
                    + "\n\n".join(extra_parts)
                    + f"\n\n用户问题：{message}"
                )

        # 选择模型：按 chat_provider 提供方决定链路
        #   auto → 本地 Ollama 优先，失败回退云端
        #   ollama → 只用本地
        #   cloud / openai_compatible（旧配置兼容）→ 只用云端
        #   force_cloud（斜杠指令强制）→ 云端优先，失败回退本地
        #
        # 真流式：模型边生成边把每个 token 作为 preview 帧推给前端（首字 1~3s 内可见），
        # 同时缓冲全文；生成完成后继续走下方原有解析/分段/工具调用管线（行为不变）。
        chat_cfg = load_config()
        chat_provider = (chat_cfg.get("chat_provider") or "auto").strip().lower()
        if force_cloud:
            provider_chain = ("cloud", "ollama")
        elif chat_provider in ("cloud", "openai_compatible"):
            provider_chain = ("cloud",)
        elif chat_provider == "ollama":
            provider_chain = ("ollama",)
        else:  # auto
            provider_chain = ("ollama", "cloud")

        buffered_pieces: List[str] = []
        for _provider in provider_chain:
            if _provider == "cloud":
                _stream_iter = cloud_llm_reply_stream(
                    effective_message, lesson_folder, history=history, long_mode=long_mode
                )
            else:
                _stream_iter = local_ollama_reply_stream(
                    effective_message, lesson_folder, history=history, long_mode=long_mode
                )
            got_piece = False
            for _piece in _stream_iter:
                got_piece = True
                buffered_pieces.append(_piece)
                yield f"data: {json.dumps({'preview': _piece, 'done': False})}\n\n"
            if got_piece:
                break  # 主用通道已有产出，不再尝试备用通道
        # 与旧的 local_ollama_reply 行为一致：剥离思考残留/独白后作为最终全文
        generated_answer = (
            _strip_thinking_lead(_strip_thinking_residue("".join(buffered_pieces).strip()))
            or fallback_answer
        )

        # 解析工具调用标记：支持一次回复输出多个工具（黑板+终端+图片可同时出现）。
        # 此前 extract_tool_call 只取第一个，模型同时输出 show_board+show_terminal 时
        # 终端被丢弃并残留为字面量——这正是"不主动调用终端"的根因之一。
        clean_answer, tool_events = extract_tool_events_all(generated_answer)
        tool_event = tool_events[0] if tool_events else None

        # 终端意图兜底：本地小模型不输出 show_terminal 但正文已做数值计算时，
        # 从正文提取表达式自动合成终端工具（黑板上写公式 + 终端算结果的完整链路）。
        # 必须在黑板兜底之前执行——黑板兜底会从正文删除公式行，可能把数值一并删掉。
        _term_code = None
        _term_lang = "python"
        if not any(isinstance(t, dict) and t.get("type") == "show_terminal" for t in tool_events):
            # 提取源 = 正文 + 模型已输出的黑板内容：
            # 本地小模型常把数值计算全写在 [TOOL:show_board] 的 content 里（LaTeX 公式），
            # 正文反而没有具体数值，仅从正文提取会漏掉终端（端到端实测复现）。
            _term_source = clean_answer
            _board_contents = [
                t.get("content", "") for t in tool_events
                if isinstance(t, dict) and t.get("type") == "show_board" and t.get("content")
            ]
            if _board_contents:
                _term_source = clean_answer + "\n" + "\n".join(_board_contents)
            _term_code = _auto_terminal_from_answer(_term_source)
            if _term_code:
                print(f"[TERMINAL-AUTO] 自动生成终端工具: {_term_code}", flush=True)

        # 用户明确要求"打开终端"但模型仍没输出 show_terminal 时兜底：
        # 4B 模型常把可执行代码写进 markdown 代码块（```python）冒充打开终端，
        # 后端把这些代码块提取成 show_terminal 工具；回复没有代码块则打开空交互终端。
        if _term_code is None and not any(isinstance(t, dict) and t.get("type") == "show_terminal" for t in tool_events):
            if _TERM_REQUEST_RE.search(message):
                _blocks = _extract_fence_blocks(clean_answer)
                if _blocks:
                    _lang, _code = _blocks[0]
                    _term_code = _code
                    _term_lang = _map_term_lang(_lang)
                    print(f"[TERMINAL-AUTO] 用户要求终端+正文含代码块, 提取 lang={_lang}->{_term_lang} len={len(_code)}", flush=True)
                else:
                    _term_code = ""   # 空代码 → 仅打开交互终端
                    _term_lang = "python"
                    print("[TERMINAL-AUTO] 用户要求终端但回复无代码块, 打开交互终端", flush=True)

        # 板书意图兜底：模型在正文写公式并说"黑板/板书"但没输出 [TOOL:show_board] 时，
        # 自动提取公式/推导行生成黑板工具（黑板上板书，正文保留叙述句）
        board_auto = False
        if not any(isinstance(t, dict) and t.get("type") == "show_board" for t in tool_events):
            _board_content, clean_answer = _auto_board_from_answer(clean_answer)
            if _board_content:
                tool_events.insert(0, {"type": "show_board", "content": _board_content})  # 黑板先弹
                board_auto = True
                print(f"[BOARD-AUTO] 自动生成黑板工具，content_len={len(_board_content)}", flush=True)

        # 终端工具在黑板之后追加（顺序：黑板写公式 → 终端算结果）
        # 注意：_term_code 可能为 ""（用户要求终端但无代码 → 只打开交互终端），也要追加
        if _term_code is not None:
            tool_events.append({"type": "show_terminal", "language": _term_lang, "code": _term_code})

        tool_event = tool_events[0] if tool_events else None

        # 解析动作指令标记：剥离后推送 action 给前端触发 Live2D 动作
        clean_answer, live2d_action = extract_action_call(clean_answer)
        print(f"[ACTION-DEBUG] live2d_action={live2d_action or 'NONE'}", flush=True)

        # 解析情绪标记：剥离后推送 emotion 给前端设置表情
        clean_answer, live2d_emotion = extract_emotion_call(clean_answer)
        clean_answer, live2d_params = extract_param_call(clean_answer)
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

        # 语境兜底：模型未输出动作/情绪标记时，根据正文信号词自动推断
        if not live2d_action:
            live2d_action = infer_context_action(clean_answer)
            if live2d_action:
                print(f"[ACTION-DEBUG] 语境推断动作={live2d_action}", flush=True)
        if not live2d_emotion:
            _inferred_emo = infer_context_emotion(clean_answer)
            if _inferred_emo:
                live2d_emotion = _inferred_emo
                print(f"[EMOTION-DEBUG] 语境推断情绪={_inferred_emo}", flush=True)

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
        # 模型的工具触发语（如"我们来测验一下"）不展示，避免刷屏。
        # 例外：黑板与终端工具保留正文——AI 边讲边算，聊天气泡仍显示讲解文字
        # （终端也保留正文：本地小模型常在正文里写好讲解 + 工具标记，终端弹窗只是验证结果）。
        if tool_event:
            _clears_answer = isinstance(tool_event, str) or (
                isinstance(tool_event, dict) and tool_event.get("type") == "show_image"
            )
            if _clears_answer:
                clean_answer = ""

        # 中文无空格时按字符分块，英文按空格分块；据此决定拼接时是否补空格
        # 分段策略（优先级递减）：
        # 1. 模型主动用分段标记（\c，配置可改）分段（优先，系统提示词已强制要求）
        # 2. 无标记 → 按空行（段落边界）分段
        # 3. 无空行 → 按句子边界切分（保护 LaTeX 公式不被截断）
        seg_cfg = load_config()
        seg_enabled = seg_cfg.get("segment_enabled", True)
        seg_marker = (seg_cfg.get("segment_marker") or "\\c").replace("\\n", "\n")
        # 模型可能误把分段标记输出为 Markdown 代码块围栏（如 ```c），需先兜底清洗：
        # 将孤立的 ```<lang> 行还原为分段标记，避免破坏 Markdown 渲染。
        clean_answer = _repair_code_fence_markers(clean_answer, seg_marker)

        raw_segments = _split_by_marker_protecting_code(clean_answer, seg_marker)
        raw_segments = [s.strip() for s in raw_segments if s.strip()]

        # [DEBUG] 分段诊断日志
        has_c_marker = seg_marker in clean_answer
        para_count = len([s for s in re.split(r'\n\s*\n', clean_answer) if s.strip()])
        print(f"[SEG-DEBUG] answer_len={len(clean_answer)}, seg_enabled={seg_enabled}, marker={seg_marker!r}, "
              f"has_marker={has_c_marker}, split_by_marker={len(raw_segments)}, para_count={para_count}", flush=True)
        print(f"[SEG-DEBUG] answer_preview(200)={repr(clean_answer[:200])}", flush=True)
        if has_c_marker:
            print(f"[SEG-DEBUG] marker positions: {[m.start() for m in re.finditer(re.escape(seg_marker), clean_answer)]}", flush=True)

        if not seg_enabled or len(raw_segments) <= 1:
            # 关闭分段 或 无分段标记 → 按空行分段（段落边界；保护代码块不被空行切开）
            raw_segments = _split_paragraphs_protecting_code(clean_answer)
            raw_segments = [s.strip() for s in raw_segments if s.strip()]
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
                # 检测当前 chunk 中是否含情绪标签，如 [joy]\n 或内嵌的 "好的！[joy] 我们来..."
                _emo_inline = _EMOTION_SHORT_RE.search(chunk) or _EMOTION_RE.search(chunk)
                if _emo_inline:
                    # 仅当剥离标签后无剩余文本（整行就是标签）才跳过文本、只推送 emotion 事件；
                    # 内嵌标签的 chunk 走正常累积流程，标签在显示时统一剥离。
                    if not strip_emotion_tags(chunk):
                        _emo_norm = _normalize_emotion(_emo_inline.group(1)) or "neutral"
                        if _emo_norm != live2d_emotion:
                            yield f"data: {json.dumps({'emotion_stream': _emo_norm, 'segment': seg_idx})}\n\n"
                        continue
                if idx == 0:
                    accumulator = chunk
                elif chunk == '\n' or accumulator.endswith('\n'):
                    accumulator = accumulator + chunk
                else:
                    accumulator = (accumulator + " " + chunk) if use_space else (accumulator + chunk)
                # 检测累积文本中是否刚出现新情绪（流中插入的标签）
                _stream_emos = extract_emotions_all(accumulator)
                if _stream_emos and _stream_emos[-1] != live2d_emotion:
                    live2d_emotion = _stream_emos[-1]
                    yield f"data: {json.dumps({'emotion_stream': live2d_emotion, 'segment': seg_idx})}\n\n"
                # 显示前剥离情绪标签
                display_content = strip_emotion_tags(accumulator)
                payload = {"content": display_content, "done": False, "segment": seg_idx}
                yield f"data: {json.dumps(payload)}\n\n"
                # 去掉后端人为节流：前端已有 MIN_RENDER_INTERVAL=50ms DOM 节流，
                # 后端不再 sleep（原 8ms/帧，500 字 ≈ 4s 纯延迟）

        # 存档时去掉分段标记（默认 \c，配置可改；兼容单/多反斜杠 + c 的旧历史），
        # 并剥离所有情绪标签（内嵌多标签时 extract_emotion_call 只清掉第一个）
        if seg_marker == "\\c":
            # 先保护 LaTeX 公式，再清 \c，避免公式里的 \cdot / \cancel 被误删
            _CLEAN_HOLD.clear()
            held_answer = _LATEX_BLOCK_RE.sub(_hold_for_clean, clean_answer)
            held_answer = re.sub(r"\\+c", "", held_answer)
            clean_answer_stored = strip_emotion_tags(_CODE_PH_RE.sub(lambda m: _CLEAN_HOLD[int(m.group(1))], held_answer)).strip()
        else:
            clean_answer_stored = strip_emotion_tags(clean_answer.replace(seg_marker, "")).strip()

        audio_url = local_tts_audio(_tts_safe_text(clean_answer)[:500]) if clean_answer else None

        conversation = load_conversation(lesson_folder)
        conversation.append({"role": "user", "content": effective_message, "timestamp": now_iso()})
        # 存档：若有工具事件，不把 teacher 的工具触发语存进对话历史（避免下次对话里重复出现）
        if not tool_event and clean_answer_stored:
            conversation.append({"role": "assistant", "content": clean_answer_stored, "timestamp": now_iso()})
        save_conversation(lesson_folder, conversation)
        progress["last_access"] = now_iso()
        save_progress(lesson_folder, progress)
        ACTIVE_LESSON["conversation"] = conversation
        ACTIVE_LESSON["progress"] = load_progress(lesson_folder)

        # 单元内画像节流蒸馏：每满 5 轮师生互动且距上次蒸馏 >10 分钟，后台异步更新画像（可迭代）
        try:
            _cur_unit = int(progress.get("current_unit", 0) or 0)
            _profile_data = _load_student_profile(lesson_folder)
            _unit_info = (_profile_data.get("units") or {}).get(str(_cur_unit)) or {}
            _last_ts = str(_unit_info.get("distilled_at") or "")
            _too_recent = False
            if _last_ts:
                try:
                    _too_recent = (datetime.now() - datetime.fromisoformat(_last_ts)).total_seconds() < 600
                except Exception:
                    _too_recent = False
            if not _too_recent and len(conversation) >= 10:
                threading.Thread(
                    target=_distill_student_profile,
                    args=(lesson_folder, _cur_unit, conversation),
                    daemon=True,
                ).start()
        except Exception as _exc:
            print(f"[profile] 对话后蒸馏触发失败: {_exc}", flush=True)

        done_payload: Dict[str, Any] = {"content": clean_answer_stored, "done": True, "audio_url": audio_url, "segment_count": len(segments)}
        if live2d_action:
            done_payload["action"] = live2d_action
        if live2d_emotion:
            done_payload["emotion"] = live2d_emotion
        if live2d_params:
            done_payload["params"] = live2d_params
        if tool_events:
            done_payload["tool_events"] = tool_events        # 全部工具（黑板→终端→图片按序触发）
            done_payload["tool_event"] = tool_events[0]      # 兼容旧前端：第一个为主事件
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
    score_history = progress.get("score_history") or []
    if not isinstance(score_history, list):
        score_history = []
    wrong_book = progress.get("wrong_book") or []
    if not isinstance(wrong_book, list):
        wrong_book = []
    # 学习统计：平均分 / 总测验次数 / 最近一次 / 错题总数 / 高频薄弱知识点
    avg_score = round(sum(r.get("score", 0) for r in score_history) / len(score_history)) if score_history else 0
    wrong_freq: Dict[str, int] = {}
    for w in wrong_book:
        _q = str(w.get("question") or "")[:24]
        if _q:
            wrong_freq[_q] = wrong_freq.get(_q, 0) + int(w.get("wrong_count") or 1)
    weak_topics = [q for q, _ in sorted(wrong_freq.items(), key=lambda kv: -kv[1])[:5]]
    return jsonify({
        "lesson_folder": lesson_folder,
        "current_unit": int(progress.get("current_unit", 0) or 0),
        "completed_units": progress.get("completed_units", []),
        "total_units": len(units),
        "units": [{"title": u.get("title", f"第 {i + 1} 课"), "summary": u.get("summary", "")} for i, u in enumerate(units)],
        "has_units": bool(units),
        "stats": {
            "quiz_count": len(score_history),
            "avg_score": avg_score,
            "last_score": score_history[-1].get("score", 0) if score_history else 0,
            "final_count": len(progress.get("final_scores") or []),
            "final_latest": progress.get("final_latest") or {},
            "score_history": score_history[-20:],
            "wrong_book_count": len(wrong_book),
            "wrong_book": wrong_book[-50:],
            "weak_topics": weak_topics,
            "code_attempts": int(progress.get("code_attempts", 0) or 0),
            "started_at": progress.get("started_at", ""),
            "last_access": progress.get("last_access", ""),
        },
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
        # 过滤"元认知/学习态度"套路题（绝对不允许出现无意义题）
        if not _is_meaningful_question(item):
            continue
        out.append(item)
    return out


@app.route("/api/exam/generate", methods=["POST"])
def api_exam_generate():
    payload = request.get_json(silent=True) or {}
    topic = (payload.get("topic") or ACTIVE_LESSON.get("prepared", {}).get("topic") or "Python基础").strip()
    force_regenerate = bool(payload.get("force", False))

    lesson_folder = ACTIVE_LESSON.get("folder")
    metadata = load_lesson_metadata(lesson_folder) if lesson_folder else {}

    # 期末测验模式：直接使用已上传的标准试卷原题（提交时按标准答案批改）
    mode = str(payload.get("mode") or "").strip().lower()
    if mode == "final":
        paper = load_exam_paper(lesson_folder)
        questions = paper.get("questions") or []
        if not questions:
            return jsonify({"ok": False, "message": "尚未上传标准试卷，请先在测验面板上传试卷后再开始期末测验"}), 400
        ACTIVE_LESSON["last_exam"] = questions  # 保留标准答案供 submit 批改
        ACTIVE_LESSON["last_exam_mode"] = "final"
        safe_questions = []
        for q in questions:
            safe = {"question": q.get("question", ""), "type": q.get("type", "single")}
            if q.get("type") in ("single", "multiple", "boolean"):
                safe["options"] = q.get("options", [])
            safe_questions.append(safe)
        return jsonify({"questions": safe_questions, "total": len(safe_questions), "mode": "final", "source": paper.get("source", "")})

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
        # 读取本课聊天记录，让题目基于真实对话内容动态生成
        chat_history = load_conversation(lesson_folder) if lesson_folder else []
        # 出题 provider 与备课 provider 解耦：
        # ① 本地模型可用（配置了 base_url/model 且 enable_local_ollama=True）→ 优先本地
        # ② 本地失败/不可用 → 云端模型高质量出题兜底（4B 本地模型常出"大纲归属"烂题）
        # ③ 两者都失败 → 结构化 fallback 题库
        from_path = (cfg.get("enable_local_ollama", True)
                     and cfg.get("ollama_base_url", "").strip()
                     and cfg.get("ollama_model", "").strip())

        def _qualified(quiz: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            """归一化 + 过滤无意义题后仍能用的题目；不够格的不算出题成功。"""
            return _normalize_quiz_questions(quiz)

        generated: List[Dict[str, Any]] = []
        if from_path:
            ollama_config = {
                "ollama_base_url": cfg.get("ollama_base_url", ""),
                "ollama_model": cfg.get("ollama_model", ""),
            }
            generated = _qualified(generate_quiz_with_ollama(
                unit_content, personality_prompt, ollama_config, chat_history
            ))
        # 本地题不够格（空 / 全被"无意义题"过滤器拦截）→ 云端高质量兜底
        if len(generated) < 3 and (cfg.get("cloud_api_key", "").strip() or cfg.get("siliconflow_api_key", "").strip()):
            cloud_config = {
                "cloud_api_key": cfg.get("cloud_api_key", ""),
                "cloud_api_base_url": cfg.get("cloud_base_url", ""),
                "cloud_provider": cfg.get("cloud_provider", ""),
                "cloud_model": cfg.get("cloud_model", ""),
                "siliconflow_api_key": cfg.get("siliconflow_api_key", ""),
                "siliconflow_base_url": cfg.get("siliconflow_base_url", ""),
                "siliconflow_model": cfg.get("siliconflow_model", ""),
            }
            generated = _qualified(generate_quiz_with_model(
                unit_content, personality_prompt, cloud_config, chat_history
            ))
            if generated:
                print(f"[quiz] 本地出题失败/质量不足，云端兜底成功: {len(generated)} 题", flush=True)

        if generated:
            questions = generated
            # 仅当模型出题成功（>=3 题，避免兜底缓存回读）才写 quiz_preset；
            # 否则下次请求会从缓存读到兜底题（空泛题）。
            if lesson_folder and len(questions) >= 3:
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

    # 3. 最终兜底题库（基于单元 key_points 生成与具体知识点绑定的题目，严禁泛化套路题）
    if not questions:
        questions = _fallback_quiz(cur_unit or {"title": topic})

    questions = _normalize_quiz_questions(questions)

    # 4. 模型出的题若全被"无意义题"过滤器拦截（4B 偶发输出崩坏格式）→ 二次兜底到
    #    本地结构化题库，绝不能让用户拿到空题单
    if not questions:
        questions = _normalize_quiz_questions(_fallback_quiz(cur_unit or {"title": topic}))

    # 题型兜底：4B 本地模型常漏出多选/判断/填空（实测只出 2 道单选），
    # 从本地题库补齐缺失题型，确保填空等题型可用。
    if cur_unit and questions:
        have_types = {q.get("type") for q in questions}
        fb_quiz = _fallback_quiz(cur_unit)
        for want in ("multiple", "boolean", "fill"):
            if want in have_types:
                continue
            cand = next((q for q in fb_quiz if q.get("type") == want), None)
            if cand:
                questions.append(cand)
        questions = _normalize_quiz_questions(questions)

    ACTIVE_LESSON["last_exam"] = questions
    ACTIVE_LESSON["last_exam_mode"] = "normal"
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
    # 兼容对象形式（前端以 {0:答案,1:答案} 提交）：转成有序列表
    if isinstance(answers, dict):
        answers = [answers.get(str(i), "") for i in range(len(answers))]
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
        elif q_type == "essay":
            # 简答题：基于标准答案的语义/关键词评判（复用语义评判器）
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
        record = {"score": score, "timestamp": now_iso(), "total": total, "correct": correct}
        if ACTIVE_LESSON.get("last_exam_mode") == "final":
            # 期末测验成绩单独沉淀（final_scores），供复盘与掌握度分析
            progress["final_scores"] = progress.get("final_scores", []) + [record]
            progress["final_latest"] = record
        else:
            progress["score_history"] = progress.get("score_history", []) + [record]
            progress["completed_quizzes"] = list(dict.fromkeys(progress["completed_quizzes"] + ["quiz_1"]))

        # 错题本沉淀：每题去重合并（同一题干多次答错，累加出错次数并保留最新答案/解析）
        if wrong_indices:
            wrong_book = progress.get("wrong_book") or []
            if not isinstance(wrong_book, list):
                wrong_book = []
            _unit_idx = int(progress.get("current_unit", 0) or 0)
            for idx in wrong_indices:
                if 0 <= idx < len(questions):
                    q = questions[idx]
                    entry = {
                        "question": q.get("question", ""),
                        "type": q.get("type", "single"),
                        "student_answer": answers[idx] if idx < len(answers) else "",
                        "correct_answer": q.get("answer", ""),
                        "explanation": q.get("explanation", ""),
                        "unit": _unit_idx,
                        "last_wrong_at": now_iso(),
                    }
                    hit = next((w for w in wrong_book if w.get("question") == entry["question"]), None)
                    if hit:
                        hit.update({k: v for k, v in entry.items() if k != "question"})
                        hit["wrong_count"] = int(hit.get("wrong_count") or 0) + 1
                    else:
                        entry["wrong_count"] = 1
                        entry["first_wrong_at"] = now_iso()
                        wrong_book.append(entry)
            progress["wrong_book"] = wrong_book[-200:]  # 只保留最近 200 条，避免无限膨胀
        save_progress(ACTIVE_LESSON["folder"], progress)

        # V4 学习评估闭环：同步学生数字孪生（测验记录 + 知识点掌握度 + 错题 + 答题连击）
        try:
            from pedagogical_engine import record_answer_result as _ped_streak
            from learner_model import add_assessment_record as _lm_assess
            from learner_model import add_error_record as _lm_error
            _learner_id = "001"
            _unit_idx = int(progress.get("current_unit", 0) or 0)
            _concept_id = f"KG{_unit_idx + 1:03d}"
            _lm_assess(_learner_id, {
                "score": score,
                "concepts": [_concept_id],
                "total": total,
                "correct": correct,
            })
            for _idx in wrong_indices:
                if 0 <= _idx < len(questions):
                    _q = questions[_idx]
                    _lm_error(
                        _learner_id, _concept_id,
                        f"{str(_q.get('question') or '')[:60]}（正确答案：{_q.get('answer') or ''}）",
                    )
            _ped_streak(_learner_id, correct == total, _concept_id)
            print(f"[v4] 学生孪生已同步: learner={_learner_id} concept={_concept_id} score={score}", flush=True)
        except Exception as _v4_exc:
            print(f"[v4] 测验孪生同步失败: {_v4_exc}", flush=True)

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
        # 单元结束时蒸馏当前单元的学生画像（后台线程，不阻塞进入下一单元）
        try:
            _conv_snapshot = load_conversation(folder)
            threading.Thread(
                target=_distill_student_profile,
                args=(folder, cur, _conv_snapshot),
                daemon=True,
            ).start()
        except Exception as _exc:
            print(f"[profile] 单元切换触发蒸馏失败: {_exc}", flush=True)
        progress["current_unit"] = cur + 1
        progress.setdefault("completed_units", [])
        if cur not in progress["completed_units"]:
            progress["completed_units"].append(cur)
        save_progress(folder, progress)
        # 归档当前单元对话（供历史回顾/导出），再清空实现单元间对话隔离
        _archive_conversation(folder, cur, load_conversation(folder))
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


@app.route("/api/profile/distill", methods=["POST"])
def api_profile_distill():
    """手动触发当前单元学生画像蒸馏（同步等待结果，供前端按钮调用）。"""
    payload = request.get_json(silent=True) or {}
    folder = payload.get("folder") or ACTIVE_LESSON.get("folder")
    if not folder:
        return jsonify({"error": "未选择课程"}), 400
    progress = load_progress(folder)
    unit_index = int(payload.get("unit_index", progress.get("current_unit", 0) or 0))
    try:
        conv = load_conversation(folder)
    except Exception:
        conv = []
    ok = _distill_student_profile(folder, unit_index, conv)
    data = _load_student_profile(folder)
    return jsonify({"success": ok, "distilled": ok, "profile": data.get("merged") or {}, "units": data.get("units") or {}})


@app.route("/api/profile", methods=["GET"])
def api_profile_get():
    """查看当前课程已蒸馏的学生画像（按单元 + 合并画像）。"""
    folder = request.args.get("folder") or ACTIVE_LESSON.get("folder")
    if not folder:
        return jsonify({"error": "未选择课程"}), 400
    data = _load_student_profile(folder)
    return jsonify({"profile": data.get("merged") or {}, "units": data.get("units") or {}, "updated_at": data.get("updated_at") or ""})


@app.route("/api/lesson/<path:lesson_folder>/history", methods=["GET"])
def api_lesson_history(lesson_folder: str):
    """历史对话回顾：返回全部已归档单元对话 + 当前单元对话。"""
    archived = _load_archived_conversations(lesson_folder)
    current = load_conversation(lesson_folder)
    metadata = load_lesson_metadata(lesson_folder)
    units = metadata.get("units") or []
    unit_titles = {}
    for i, u in enumerate(units):
        unit_titles[str(i)] = str(u.get("title") or f"第 {i + 1} 课")
    units_out = []
    for key in sorted(archived.keys(), key=lambda k: int(k.replace("unit_", "") or 0)):
        idx = key.replace("unit_", "")
        units_out.append({
            "unit_index": int(idx or 0),
            "title": unit_titles.get(idx, f"第 {int(idx or 0) + 1} 课"),
            "conversation": archived[key],
            "archived": True,
        })
    if current:
        cur = int(load_progress(lesson_folder).get("current_unit", 0) or 0)
        units_out.append({
            "unit_index": cur,
            "title": unit_titles.get(str(cur), f"第 {cur + 1} 课"),
            "conversation": current,
            "archived": False,
        })
    return jsonify({"units": units_out, "count": len(units_out)})


@app.route("/api/lesson/<path:lesson_folder>/history/export", methods=["GET"])
def api_lesson_history_export(lesson_folder: str):
    """导出全部对话为 Markdown（含历史归档单元 + 当前单元）。"""
    archived = _load_archived_conversations(lesson_folder)
    current = load_conversation(lesson_folder)
    metadata = load_lesson_metadata(lesson_folder)
    topic = (metadata.get("topic") or lesson_folder).strip()
    units = metadata.get("units") or []

    blocks: List[str] = [f"# 《{topic}》对话记录", f"\n> 导出时间：{now_iso()}\n"]

    def _unit_title(idx: int) -> str:
        if 0 <= idx < len(units):
            return str(units[idx].get("title") or f"第 {idx + 1} 课")
        return f"第 {idx + 1} 课"

    for key in sorted(archived.keys(), key=lambda k: int(k.replace("unit_", "") or 0)):
        idx = int(key.replace("unit_", "") or 0)
        md = _conversation_to_markdown(archived[key])
        if md:
            blocks.append(f"\n---\n\n## 📚 单元回顾：{_unit_title(idx)}\n\n{md}")
    if current:
        cur = int(load_progress(lesson_folder).get("current_unit", 0) or 0)
        md = _conversation_to_markdown(current)
        if md:
            blocks.append(f"\n---\n\n## 📖 当前单元：{_unit_title(cur)}\n\n{md}")

    content = "\n".join(blocks)
    fname = f"对话记录_{lesson_folder}.md"
    return Response(
        content,
        mimetype="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{urllib.parse.quote(fname)}"},
    )


@app.route("/api/unit/welcome", methods=["POST"])
def api_unit_welcome():
    """首次进入单元时，AI 主动开场讲解本课基础知识点（流式 SSE）。

    把系统提示词（人格/课程背景/本单元内容）交给模型生成开场讲解；
    讲解写入对话历史（作为该单元第一条 assistant 消息），并记录到 welcomed_units 避免重复。
    """
    payload = request.get_json(silent=True) or {}
    lesson_folder = payload.get("lesson_folder") or ACTIVE_LESSON.get("folder")
    if not lesson_folder:
        return jsonify({"error": "No active lesson selected"}), 400

    metadata = load_lesson_metadata(lesson_folder)
    units = metadata.get("units") or []
    if not units:
        return jsonify({"error": "该课程无分课内容"}), 400

    progress = load_progress(lesson_folder)
    cur = int(progress.get("current_unit", 0) or 0)
    if not (0 <= cur < len(units)):
        return jsonify({"error": "单元索引越界"}), 400
    welcomed = progress.get("welcomed_units") or []
    if not isinstance(welcomed, list):
        welcomed = []
    if cur in welcomed:
        # 已欢迎过：轻量返回，前端不重复讲解
        return jsonify({"ok": True, "already": True, "unit_index": cur}), 200

    unit = units[cur]
    unit_title = unit.get("title") or f"第 {cur + 1} 课"
    system_prompt = build_system_prompt(lesson_folder)
    user_msg = (
        f"请为「{unit_title}」这节课做一个开场讲解，帮助学生开始学习。要求：\n"
        "1. 先简短介绍这节课要学什么、为什么重要（1-2 句）\n"
        "2. 然后系统性地讲解本课的基础知识点，覆盖关键要点，每个要点配一个简单例子\n"
        "3. 用 Markdown 结构化输出（标题、列表、粗体），口语化、亲切，像真人老师面对面授课\n"
        "4. 篇幅适中（约 300-500 字），不要过于冗长，不要输出任何工具调用标记\n"
        "5. 开场白不要报时间/日期/星期，直接进入课程内容\n"
    )

    def generate():
        # 真流式：与 /api/chat 一致，按 chat_provider 决定 provider_chain，
        # 模型边生成边把每个 token 作为 preview 帧推给前端（首字可见），同时缓冲全文。
        chat_cfg = load_config()
        chat_provider = (chat_cfg.get("chat_provider") or "auto").strip().lower()
        if chat_provider in ("cloud", "openai_compatible"):
            provider_chain = ("cloud",)
        elif chat_provider == "ollama":
            provider_chain = ("ollama",)
        else:  # auto：本地优先，失败回退云端
            provider_chain = ("ollama", "cloud")

        buffered_pieces: List[str] = []
        for _provider in provider_chain:
            if _provider == "cloud":
                _stream_iter = cloud_llm_reply_stream(user_msg, lesson_folder, history=[])
            else:
                _stream_iter = local_ollama_reply_stream(user_msg, lesson_folder, history=[])
            got_piece = False
            for _piece in _stream_iter:
                got_piece = True
                buffered_pieces.append(_piece)
                yield f"data: {json.dumps({'preview': _piece, 'done': False})}\n\n"
            if got_piece:
                break  # 主用通道已有产出，不再尝试备用通道
        answer = _strip_thinking_lead(_strip_thinking_residue("".join(buffered_pieces).strip()))
        # 清除分段符 \c（welcome 链路按空行分段，不消费 \c；先保护 LaTeX 再剥离，
        # 避免 \c 泄漏到气泡和对话历史存档）
        _CLEAN_HOLD.clear()
        held_answer = _LATEX_BLOCK_RE.sub(_hold_for_clean, answer)
        held_answer = re.sub(r"\\+c", "", held_answer)
        answer = _CODE_PH_RE.sub(lambda m: _CLEAN_HOLD[int(m.group(1))], held_answer).strip()
        if not answer:
            answer = "这节课的基础知识我还没准备好，你可以直接问我任何问题，我来为你讲解。"

        # 段落边界分段，逐段推送（制造"一段一段讲"的效果）
        segments = _split_paragraphs_protecting_code(answer)
        segments = [s.strip() for s in segments if s.strip()] or [answer]
        for seg_idx, segment in enumerate(segments):
            yield f"data: {json.dumps({'content': segment, 'done': False, 'segment': seg_idx})}\n\n"
            time.sleep(0.05)   # 原 250ms/段，整体偏慢，降到 50ms 仍保留逐段节奏

        # 写入对话历史（作为该单元第一条消息）并标记已欢迎
        conversation = load_conversation(lesson_folder)
        conversation.append({"role": "user", "content": f"（系统：请讲解「{unit_title}」的开场内容）", "timestamp": now_iso()})
        conversation.append({"role": "assistant", "content": answer, "timestamp": now_iso()})
        save_conversation(lesson_folder, conversation)
        welcomed.append(cur)
        progress["welcomed_units"] = welcomed
        save_progress(lesson_folder, progress)

        done_payload = {"content": answer, "done": True, "unit_index": cur, "progress": load_progress(lesson_folder)}
        yield f"data: {json.dumps(done_payload)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


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
    # 兼容两种入参：lesson_folder（旧）或 lesson_id（新）——两者都是目录名本身
    lesson_folder = (payload.get("lesson_id") or payload.get("lesson_folder") or "").strip()
    if not lesson_folder:
        return jsonify({"error": "lesson_id is required"}), 400

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


# ---------------------------------------------------------------------------
# V4 自适应教学系统 API（/api/v4/* 前缀，避免与现有路由冲突）
# ---------------------------------------------------------------------------
def _v4_learner_id() -> str:
    """单机应用默认学生 001；可通过 learner_id 参数（query / json）覆盖。"""
    lid = request.args.get("learner_id", "")
    if request.is_json:
        payload = request.get_json(silent=True) or {}
        lid = str(payload.get("learner_id") or lid)
    return (str(lid).strip() or "001")


@app.route("/api/v4/learner/<path:learner_id>/progress", methods=["GET"])
def api_v4_learner_progress(learner_id: str):
    """获取学生学习进度曲线（测验分数序列 + 知识点平均掌握度）。"""
    try:
        curve = eval_progress_curve(learner_id)
        return jsonify(curve)
    except Exception as exc:
        return jsonify({"error": f"进度曲线获取失败: {exc}"}), 500


@app.route("/api/v4/learner/<path:learner_id>/gain_report", methods=["GET"])
def api_v4_learner_gain_report(learner_id: str):
    """获取学习增益报告（前测/后测/增益/效率/ROI）。"""
    try:
        time_spent = request.args.get("time_spent")
        effort = request.args.get("effort", 1.0)
        report = eval_gain_report(
            learner_id,
            time_spent_hours=float(time_spent) if time_spent else None,
            effort=float(effort or 1.0),
        )
        return jsonify(report)
    except Exception as exc:
        return jsonify({"error": f"增益报告生成失败: {exc}"}), 500


@app.route("/api/v4/learner/<path:learner_id>/insights", methods=["GET"])
def api_v4_learner_insights(learner_id: str):
    """获取 AI 生成的学习洞察。"""
    try:
        return jsonify(eval_insights(learner_id))
    except Exception as exc:
        return jsonify({"error": f"学习洞察生成失败: {exc}"}), 500


@app.route("/api/v4/learner/<path:learner_id>/diagnosis", methods=["GET"])
def api_v4_learner_diagnosis(learner_id: str):
    """获取诊断报告（薄弱点 / 错题汇总 / 建议）。"""
    try:
        lesson_folder = request.args.get("lesson_folder") or ACTIVE_LESSON.get("folder")
        return jsonify(ped_diagnosis(learner_id, lesson_folder))
    except Exception as exc:
        return jsonify({"error": f"诊断失败: {exc}"}), 500


@app.route("/api/v4/learner/<path:learner_id>/record_answer", methods=["POST"])
def api_v4_learner_record_answer(learner_id: str):
    """记录一次答题结果（更新连击 + 掌握度），供前端对话/测验闭环调用。"""
    payload = request.get_json(silent=True) or {}
    is_correct = bool(payload.get("is_correct", False))
    concept_id = (payload.get("concept_id") or "").strip()
    try:
        if concept_id:
            lm_update_knowledge_state(learner_id, concept_id, 1.0 if is_correct else 0.0)
        streak = ped_record_answer(learner_id, is_correct, concept_id or None)
        return jsonify({"status": "ok", "streak": streak})
    except Exception as exc:
        return jsonify({"error": f"答题记录失败: {exc}"}), 500


@app.route("/api/v4/dashboard", methods=["GET"])
def api_v4_dashboard():
    """学习仪表盘聚合数据：知识掌握度 + 推荐下一步 + 诊断 + 进度曲线 + 错题本。"""
    lesson_folder = request.args.get("lesson_folder") or ACTIVE_LESSON.get("folder")
    learner_id = _v4_learner_id()
    try:
        from knowledge_graph import load_knowledge_graph as _kg_load
        graph = _kg_load(lesson_folder)
        learner = lm_get_learner(learner_id, create=True)
        ks = learner.get("knowledge_state", {}) or {}
        nodes = []
        for n in graph.get("nodes", []):
            cid = n.get("id")
            nodes.append({**n, "mastery": round(float(ks.get(cid, 0) or 0), 4)})
        # 无图谱时回退：直接展示学生档案中已记录的知识点掌握度
        if not nodes and ks:
            nodes = [{"id": cid, "name": cid, "mastery": round(float(v or 0), 4)}
                     for cid, v in ks.items() if (v or 0) > 0]
        plan = ped_plan_next(learner_id, lesson_folder)
        diagnosis = ped_diagnosis(learner_id, lesson_folder)
        curve = eval_progress_curve(learner_id)
        return jsonify({
            "learner_id": learner_id,
            "lesson_folder": lesson_folder,
            "knowledge_state": nodes,
            "recommended": plan.get("recommended"),
            "recommend_reason": plan.get("reason"),
            "diagnosis": diagnosis,
            "progress_curve": curve,
            "behavior": learner.get("learning_behavior", {}),
            "error_memory": (learner.get("error_memory") or [])[-50:],
            "has_kg": bool(graph.get("nodes")),
        })
    except Exception as exc:
        return jsonify({"error": f"仪表盘数据获取失败: {exc}"}), 500


if __name__ == "__main__":
    # 启动时同步 OCR 总开关（config.json 的 ocr_enabled）
    try:
        set_ocr_enabled(bool(load_config().get("ocr_enabled", True)))
    except Exception:
        pass
    # use_reloader=False：Windows 下 debug 自动重载器不稳定，改代码时进程会悄悄退出，
    # 导致前端 "Failed to fetch"。改用显式重启：改完代码由开发侧重启服务。
    # debug=False：启用 GZip/静态缓存优化（werkzeug 的 debug 调试器会破坏 flask-compress 的压缩）。
    # 如需开发期错误详情，可用环境变量开启：FLASK_DEBUG=1 python app.py
    import os
    _flask_debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=5000, debug=_flask_debug, use_reloader=False)
