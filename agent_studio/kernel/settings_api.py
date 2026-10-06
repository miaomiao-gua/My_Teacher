"""设置中心：把「所有可配置项」收在一处，读写都在这里。

学生要能在界面上改：模型链路（含 base_url / API Key）、运行参数、**人格提示词**、桌宠形象。

三条硬规矩（都是安全/稳定性问题，不许绕过）：
1. **密钥只进不出**：读接口永远不回传 key 的明文，只回 `chat_api_key_set: true/false`。
   学生想换 key 就填新的；想清掉就带 `chat_api_key_clear`。
2. **白名单**：只接受 `FIELDS` 里列出的键，别的一律忽略（防止界面被改坏或误写内部字段）。
   每个键都带类型与取值范围，越界就报错说清楚。
3. **落盘要原子**：先写 `.tmp` 再 replace，避免写一半断电/崩溃把配置写坏。
   密钥用 `crypto_utils.encrypt_str` 加密后存（磁盘上是 `enc$...`）。

提示词分两块：
- `data/persona.md`      —— 人格（老师是谁、怎么教）。**整段替换**内置的 BASE_PERSONA。
- `data/persona_extra.md`—— 附加要求（"每次课都要留作业"这类）。追加在最后。
工具使用规则（TOOL_POLICY）**不开放编辑**：那不是人格，是让工具别用错的功能性约束，
删了模型就会开始瞎编界面。界面上要写清楚这一点。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .paths import data_root

# ---------------------------------------------------------------------------
# 白名单：键 → (类型, 最小值, 最大值, 是否密钥)
# 类型只允许 str / int / float / bool。范围只对数字生效。
# ---------------------------------------------------------------------------
FIELDS: Dict[str, Tuple[Any, Optional[float], Optional[float], bool]] = {
    # 云端链路（OpenAI 兼容：硅基流动 / DeepSeek / OpenAI / 自建都行）
    "chat_base_url": (str, None, None, False),
    "chat_model": (str, None, None, False),
    "chat_api_key": (str, None, None, True),
    "chat_temperature": (float, 0.0, 2.0, False),
    # 本地链路（Ollama）
    "ollama_base_url": (str, None, None, False),
    "ollama_model": (str, None, None, False),
    "ollama_temperature": (float, 0.0, 2.0, False),
    "ollama_num_ctx": (int, 1024, 262144, False),
    "ollama_num_predict": (int, 128, 65536, False),
    "agent_think": (bool, None, None, False),
    # 运行参数
    "agent_max_steps": (int, 1, 50, False),
    "agent_run_timeout": (float, 30, 7200, False),
    "agent_tool_timeout": (float, 5, 900, False),
    "agent_python_timeout": (float, 2, 600, False),
    "agent_num_ctx": (int, 1024, 262144, False),
    "agent_num_predict": (int, 128, 65536, False),
    # 桌宠
    "pet_scale": (float, 0.6, 2.6, False),
    "pet_visible": (bool, None, None, False),
    # 语音（TTS）
    "tts_provider": (str, None, None, False),
    "tts_windows_voice": (int, 0, 64, False),
    "tts_cloud_base_url": (str, None, None, False),
    "tts_cloud_model": (str, None, None, False),
    "tts_cloud_voice": (str, None, None, False),
    "tts_cloud_api_key": (str, None, None, True),
    "tts_speed": (float, 0.5, 2.0, False),
    "tts_volume": (int, 0, 100, False),
    "tts_auto_speak": (bool, None, None, False),
    # 看图（按快捷键读屏）
    "vision_provider": (str, None, None, False),
    "vision_model": (str, None, None, False),
    "vision_prompt": (str, None, None, False),
    "vision_hotkey": (str, None, None, False),
}

# 只能取固定几个值的字段（界面上渲染成下拉框，服务端也校验）
CHOICES: Dict[str, List[str]] = {
    "llm_provider": ["cloud", "local", "auto"],
    "tts_provider": ["off", "windows", "openai"],
    "vision_provider": ["off", "local", "cloud"],
}

SECRET_FIELDS = {k for k, v in FIELDS.items() if v[3]}

# 这些是「模型链路选择」，存在 settings.json（和 kernel/config.py 的默认值一致）
CHAIN_FIELDS = {"llm_provider": ("cloud", "local", "auto"), "fallback_local": True}

# 界面上分组的展示顺序（给前端用，前端不用自己猜哪个字段属于哪组）
GROUPS: List[Dict[str, Any]] = [
    {"key": "chain", "title": "模型链路",
     "fields": ["llm_provider", "fallback_local"],
     "hint": "cloud=走云端 API（需要 Key）；local=走本机 Ollama；auto=云端优先、本地兜底"},
    {"key": "cloud", "title": "云端 API",
     "fields": ["chat_base_url", "chat_model", "chat_api_key", "chat_temperature"],
     "hint": "任何 OpenAI 兼容的服务都行。base_url 一般以 /v1 结尾"},
    {"key": "local", "title": "本机模型（Ollama）",
     "fields": ["ollama_base_url", "ollama_model", "ollama_temperature", "ollama_num_ctx",
                "ollama_num_predict", "agent_think"],
     "hint": "本机模型的兜底链路。上下文越大越吃显存"},
    {"key": "run", "title": "运行参数",
     "fields": ["agent_max_steps", "agent_run_timeout", "agent_tool_timeout",
                "agent_python_timeout", "agent_num_ctx", "agent_num_predict"],
     "hint": "一般不用改。卡住或太慢时再调"},
    {"key": "pet", "title": "桌宠",
     "fields": ["pet_scale", "pet_visible"],
     "hint": "大小 1.0 是默认；形象在下面选，也可以上传自己的 Live2D"},
    {"key": "tts", "title": "语音（让她把话念出来）",
     "fields": ["tts_provider", "tts_windows_voice", "tts_speed", "tts_volume", "tts_auto_speak",
                "tts_cloud_base_url", "tts_cloud_model", "tts_cloud_voice", "tts_cloud_api_key"],
     "hint": "「Windows 本机语音」最省事（离线、不用 key）；云端要填 OpenAI 兼容的 /audio/speech 服务"},
    {"key": "vision", "title": "看图（按快捷键读屏幕）",
     "fields": ["vision_provider", "vision_model", "vision_hotkey", "vision_prompt"],
     "hint": "主链路（DeepSeek-V3）看不见图，所以这条要单独选一个**能看图**的模型："
             "本机 qwen3.5:9b，或云端 Qwen2.5-VL 之类。快捷键格式：ctrl+alt+s"},
]

LABELS: Dict[str, str] = {
    "llm_provider": "用哪条链路",
    "fallback_local": "云端失败时退回本机",
    "chat_base_url": "接口地址 base_url",
    "chat_model": "模型名",
    "chat_api_key": "API Key",
    "chat_temperature": "随机性 temperature",
    "ollama_base_url": "本机地址",
    "ollama_model": "本机模型名",
    "ollama_temperature": "本机随机性",
    "ollama_num_ctx": "本机上下文长度",
    "ollama_num_predict": "本机单次最多生成",
    "agent_think": "让本机模型“想一下”再答",
    "agent_max_steps": "一轮最多几步",
    "agent_run_timeout": "一轮最长秒数",
    "agent_tool_timeout": "单个工具超时秒数",
    "agent_python_timeout": "跑代码超时秒数",
    "agent_num_ctx": "上下文长度",
    "agent_num_predict": "单次最多生成",
    "pet_scale": "桌宠大小（1.0 默认）",
    "pet_visible": "显示桌宠",
    "tts_provider": "用哪种语音",
    "tts_windows_voice": "本机嗓子（序号）",
    "tts_cloud_base_url": "云端语音地址 base_url",
    "tts_cloud_model": "云端语音模型",
    "tts_cloud_voice": "音色 voice",
    "tts_cloud_api_key": "云端语音 API Key（留空则复用上面那把）",
    "tts_speed": "语速",
    "tts_volume": "音量（0-100）",
    "tts_auto_speak": "气泡自动念出来",
    "vision_provider": "看图用哪条链路",
    "vision_model": "视觉模型名",
    "vision_prompt": "看图时对她说的话（可改）",
    "vision_hotkey": "读屏快捷键",
}


def _cfg_path() -> Path:
    return data_root() / "config.json"


def _settings_path() -> Path:
    return data_root() / "settings.json"


def _persona_path() -> Path:
    return data_root() / "persona.md"


def _extra_path() -> Path:
    return data_root() / "persona_extra.md"


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _coerce(key: str, value: Any) -> Any:
    """按白名单把界面传来的值转成正确类型。转不了就抛 ValueError（消息给人看）。"""
    kind, lo, hi, _ = FIELDS[key]
    if kind is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "是")
        return bool(value)
    if kind is int:
        try:
            out = int(float(str(value).strip()))
        except Exception:
            raise ValueError(f"「{LABELS.get(key, key)}」要填整数")
        if lo is not None and out < lo:
            raise ValueError(f"「{LABELS.get(key, key)}」不能小于 {int(lo)}")
        if hi is not None and out > hi:
            raise ValueError(f"「{LABELS.get(key, key)}」不能大于 {int(hi)}")
        return out
    if kind is float:
        try:
            out = float(str(value).strip())
        except Exception:
            raise ValueError(f"「{LABELS.get(key, key)}」要填数字")
        if lo is not None and out < lo:
            raise ValueError(f"「{LABELS.get(key, key)}」不能小于 {lo}")
        if hi is not None and out > hi:
            raise ValueError(f"「{LABELS.get(key, key)}」不能大于 {hi}")
        return out
    return str(value).strip()


# 出厂默认值：config.json 里没写的键，界面上要显示**实际生效的那个值**，
# 否则输入框是空的 —— 学生一按保存，空值变成 NaN，整份设置都存不进去
# （实测就是这样：报了一串「要填数字」）。
DEFAULTS: Dict[str, Any] = {
    "chat_base_url": "https://api.siliconflow.cn/v1",
    "chat_model": "deepseek-ai/DeepSeek-V3",
    "chat_temperature": 0.7,
    "ollama_base_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen3.5:9b",
    "ollama_temperature": 0.7,
    "ollama_num_ctx": 32768,
    "ollama_num_predict": 4096,
    "agent_think": False,
    "agent_max_steps": 12,
    "agent_run_timeout": 600,
    "agent_tool_timeout": 60,
    "agent_python_timeout": 20,
    "agent_num_ctx": 32768,
    "agent_num_predict": 4096,
    # 桌宠与语音
    "pet_scale": 1.0,
    "pet_visible": True,
    "tts_provider": "off",
    "tts_windows_voice": 0,
    "tts_cloud_base_url": "https://api.siliconflow.cn/v1",
    "tts_cloud_model": "FunAudioLLM/CosyVoice2-0.5B",
    "tts_cloud_voice": "FunAudioLLM/CosyVoice2-0.5B:alex",
    "tts_speed": 1.0,
    "tts_volume": 100,
    "tts_auto_speak": False,
    # 看图：默认走本机（免费、离线）；本机模型不支持看图时学生会看到明确提示
    "vision_provider": "local",
    "vision_model": "qwen2.5vl:7b",
    "vision_prompt": "",
    "vision_hotkey": "ctrl+shift+t",
}


def read_state() -> Dict[str, Any]:
    """给界面的完整现状。**密钥不出明文**，只给「设没设」和尾部几位便于确认。"""
    from .config import agent_settings, load_app_config, load_agent_settings

    cfg = load_app_config()          # 已解密
    settings = load_agent_settings()
    live = agent_settings()          # 运行参数的实际生效值（含代码里的兜底）

    values: Dict[str, Any] = {}
    secrets: Dict[str, Dict[str, Any]] = {}
    for key in FIELDS:
        if key in SECRET_FIELDS:
            raw = str(cfg.get(key) or "")
            secrets[key] = {
                "set": bool(raw),
                "tail": ("…" + raw[-4:]) if len(raw) >= 8 else ("…" if raw else ""),
            }
            continue
        if key in cfg and cfg[key] not in (None, ""):
            values[key] = cfg[key]
            continue
        # 没配过 → 显示实际生效的默认值（运行时那几个从 agent_settings() 取）
        if key == "agent_max_steps":
            values[key] = int(live.get("max_steps") or DEFAULTS[key])
        elif key == "agent_run_timeout":
            values[key] = float(live.get("run_timeout") or DEFAULTS[key])
        elif key == "agent_tool_timeout":
            values[key] = float(live.get("tool_timeout") or DEFAULTS[key])
        elif key == "agent_python_timeout":
            values[key] = float(live.get("python_timeout") or DEFAULTS[key])
        else:
            values[key] = DEFAULTS.get(key, "")
    values["llm_provider"] = str(settings.get("llm_provider") or "cloud")
    values["fallback_local"] = bool(settings.get("fallback_local", True))

    return {
        "values": values,
        "defaults": dict(DEFAULTS),
        "choices": {k: list(v) for k, v in CHOICES.items()},
        "secrets": secrets,
        "groups": GROUPS,
        "labels": LABELS,
        "types": {k: v[0].__name__ for k, v in FIELDS.items()},
        "ranges": {k: [v[1], v[2]] for k, v in FIELDS.items() if v[1] is not None or v[2] is not None},
        "persona": persona_state(),
        "about": about(),
    }


def persona_state() -> Dict[str, Any]:
    """人格提示词现状：是不是自定义的、内容、以及内置默认（用于「恢复默认」对照）"""
    from .prompts import BASE_PERSONA

    custom = _persona_path()
    extra = _extra_path()
    text = custom.read_text(encoding="utf-8") if custom.is_file() else ""
    extra_text = extra.read_text(encoding="utf-8") if extra.is_file() else ""
    return {
        "text": text,
        "extra": extra_text,
        "is_default": not text.strip(),
        "default": BASE_PERSONA,
        "note": "工具使用规则、界面组件表这些是功能性的，不开放编辑（改了模型就会用错工具）。",
    }


def write_state(patch: Dict[str, Any]) -> Dict[str, Any]:
    """把界面提交的改动落盘。返回 (新状态, 错误列表)。

    `patch` 可以只带一部分键；`*_clear: true` 表示清空对应密钥/文本。
    """
    if not isinstance(patch, dict):
        raise ValueError("提交的内容格式不对")

    errors: List[str] = []
    cfg = _read_json(_cfg_path())
    settings = _read_json(_settings_path()) or {}
    if not settings:
        from .config import load_agent_settings

        settings = dict(load_agent_settings())

    from .crypto_utils import encrypt_str

    for key, value in patch.items():
        if key in ("persona", "persona_extra", "about"):
            continue
        if key.endswith("_clear"):
            base = key[: -len("_clear")]
            if base in SECRET_FIELDS:
                cfg.pop(base, None)
            continue
        if key in CHAIN_FIELDS:
            if key == "llm_provider":
                if str(value) not in CHAIN_FIELDS["llm_provider"]:
                    errors.append(f"「{LABELS.get(key, key)}」只能是 cloud / local / auto")
                    continue
                settings[key] = str(value)
            else:
                settings[key] = bool(value)
            continue
        if key in CHOICES and str(value) not in CHOICES[key]:
            errors.append(f"「{LABELS.get(key, key)}」只能是 " + " / ".join(CHOICES[key]))
            continue
        if key not in FIELDS:
            continue                                   # 白名单外的键：安静忽略
        if key in SECRET_FIELDS:
            text = str(value or "").strip()
            if not text:
                continue                               # 留空 = 不改（界面上是这么写的）
            cfg[key] = encrypt_str(text)               # 磁盘上只存密文
            continue
        try:
            cfg[key] = _coerce(key, value)
        except ValueError as exc:
            errors.append(str(exc))

    if errors:
        return {"ok": False, "errors": errors, "state": read_state()}

    _write_json(_cfg_path(), cfg)
    _write_json(_settings_path(), settings)

    if "persona" in patch:
        text = str(patch.get("persona") or "").strip()
        if text:
            _persona_path().write_text(text, encoding="utf-8")
        else:
            _persona_path().unlink(missing_ok=True)    # 空 = 回到内置人格
    if "persona_extra" in patch:
        text = str(patch.get("persona_extra") or "").strip()
        if text:
            _extra_path().write_text(text, encoding="utf-8")
        else:
            _extra_path().unlink(missing_ok=True)

    return {"ok": True, "errors": [], "state": read_state()}


def persona_override() -> Optional[str]:
    """给 prompts.py 用：自定义人格（没有就返回 None，用内置的）"""
    p = _persona_path()
    try:
        if p.is_file():
            text = p.read_text(encoding="utf-8").strip()
            return text or None
    except Exception:
        pass
    return None


def extra_override() -> str:
    """给 prompts.py 用：附加要求（没有就是空串）"""
    p = _extra_path()
    try:
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


async def test_connection() -> Dict[str, Any]:
    """拿当前配置真发一次请求，看链路通不通。界面上要有个「测试连接」。"""
    from .config import llm_configs
    from .llm import probe_chain

    try:
        results = await probe_chain(llm_configs())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "results": []}
    ok = any(r.get("ok") for r in results)
    return {"ok": ok, "results": results}


def about() -> Dict[str, Any]:
    """「关于」里那点信息：版本、数据目录、工具数、形象清单数量。"""
    info: Dict[str, Any] = {}
    try:
        from . import __version__

        info["version"] = __version__
    except Exception:
        info["version"] = "?"
    try:
        from .paths import describe

        paths = describe()
        info["data_root"] = paths.get("data_root")
        info["resource_root"] = paths.get("resource_root")
        info["frozen"] = paths.get("frozen")
    except Exception:
        pass
    try:
        # 工具是「导入即注册」，所以要先让它加载（不然单独跑这个模块时工具数是 0）
        from .tools import load_builtin_tools

        load_builtin_tools()
        from .registry import describe_tools

        info["tools"] = len(describe_tools())
    except Exception:
        pass
    try:
        from . import pet_models

        info["pet_models"] = len(pet_models.scan_models())
        cur = pet_models.current_model()
        info["pet_current"] = (cur or {}).get("label") or (cur or {}).get("name")
    except Exception:
        pass
    try:
        from . import tts

        info["tts"] = tts.self_check()
    except Exception:
        pass
    return info
