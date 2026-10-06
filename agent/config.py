"""路径与运行配置。

内核需要知道三件事：项目根在哪、老 Flask 的配置怎么读、用哪个模型。
这里集中处理，避免内核各处硬编码路径。

注意：**内核只读不写 `data/config.json`**（那是老 Flask 的地盘），
Agent 自己的运行时数据全部落在 `data/agent/` 下，互相不打架。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent           # D:\My_teacher
SERVER_DIR = ROOT / "server"
CLIENT_DIR = ROOT / "client"
DATA_DIR = ROOT / "data"
AGENT_DATA = DATA_DIR / "agent"
SESSIONS_DIR = AGENT_DATA / "sessions"                  # 会话消息历史
WORKSPACES_DIR = AGENT_DATA / "workspaces"              # AI 生成的文件（每个会话一个目录）
AGENT_SETTINGS_PATH = AGENT_DATA / "settings.json"      # Agent 自己的设置（不碰老应用的 config.json）
PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"   # 插件目录（仓库内，随代码走）

for _d in (AGENT_DATA, SESSIONS_DIR, WORKSPACES_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# 默认设置：**云端优先**。
# 理由（实测）：本地 qwen3.5:9b 调工具本身没问题，但**多轮**（写文件 → 自检 → 改 → 再自检）
# 时随机性明显，会出现「说一声我这就做、然后直接结束回合」这类空转；而且生成一个 10KB 的
# 仿真要 2~8 分钟。云端模型在这两件事上稳定得多。
# 本地保留为**兜底**：云端不通时还能上课（降级，而不是罢工）。
DEFAULT_AGENT_SETTINGS: Dict[str, Any] = {
    "llm_provider": "cloud",     # cloud | local | auto
    "fallback_local": True,      # 云端失败时是否允许退回本地
}


def load_agent_settings() -> Dict[str, Any]:
    data: Dict[str, Any] = dict(DEFAULT_AGENT_SETTINGS)
    if AGENT_SETTINGS_PATH.exists():
        try:
            raw = json.loads(AGENT_SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
        except Exception:
            pass
    env_provider = _env("MY_TEACHER_AGENT_PROVIDER")
    if env_provider:
        data["llm_provider"] = env_provider
    return data


def save_agent_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    data = load_agent_settings()
    data.update({k: v for k, v in (patch or {}).items() if v is not None})
    try:
        AGENT_SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return data


def load_app_config() -> Dict[str, Any]:
    """读老 Flask 的 data/config.json（模型、密钥等都在那儿）。

    密钥可能是加密的（`enc$...`，由 server/crypto_utils.py 用 data/.secret_key 加密），
    所以这里复用老代码的解密函数；导入失败就退化为「读不到密钥」，不抛异常。
    """
    path = DATA_DIR / "config.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    # 解密敏感字段（API Key 在磁盘上是 `enc$...` 密文）。
    # 踩过的坑：`open_dict(data)` 少传了第二个参数，异常被下面的兜底吞掉，
    # 结果**把密文当成了 API Key 发出去**，云端一路 401，而健康检查还报「配置齐全」。
    # 教训有两条：① 兜底异常必须留下痕迹，不能静默；② 密钥类配置必须真发一次请求验证。
    try:
        import sys

        if str(SERVER_DIR) not in sys.path:
            sys.path.insert(0, str(SERVER_DIR))
        from crypto_utils import is_encrypted, open_dict  # type: ignore

        fields = [k for k, v in data.items() if is_encrypted(v)]   # 不写死字段名，防止漏掉
        if fields:
            decrypted = open_dict(data, fields)
            failed = [k for k in fields if not decrypted.get(k)]
            data = decrypted
            if failed:
                print(
                    f"[agent/config] ⚠️ 这些密钥解密失败（data/.secret_key 可能被更换过）：{failed}",
                    flush=True,
                )
    except Exception as exc:  # noqa: BLE001
        print(f"[agent/config] ⚠️ 解密密钥时出错：{type(exc).__name__}: {exc}", flush=True)
    return data


@dataclass
class LLMConfig:
    """一条模型链路。`provider` 决定用哪种协议说话。"""

    provider: str = "ollama"                 # ollama | openai
    model: str = "qwen3.5:9b"
    base_url: str = "http://127.0.0.1:11434"
    api_key: str = ""
    temperature: float = 0.7
    num_ctx: int = 32768
    num_predict: int = 4096
    think: Optional[bool] = False            # 思考型模型：教学场景默认关掉思考，降低首字延迟
    timeout: float = 300.0
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def primary_llm_config(cfg: Optional[Dict[str, Any]] = None) -> LLMConfig:
    """主力链路：本地 Ollama（免费、快、可反复调试）。

    依据（实测环境）：本机 Ollama 里 qwen3.5:9b 声明支持 tools + vision + 262k 上下文，
    是当前唯一同时满足「会调工具」和「看得懂图」的本地模型。
    """
    cfg = cfg if cfg is not None else load_app_config()
    base = _env("MY_TEACHER_OLLAMA_BASE_URL") or cfg.get("ollama_base_url") or "http://127.0.0.1:11434"
    model = _env("MY_TEACHER_AGENT_MODEL") or cfg.get("agent_model") or "qwen3.5:9b"
    return LLMConfig(
        provider="ollama",
        model=model,
        base_url=base.rstrip("/"),
        temperature=float(cfg.get("ollama_temperature", 0.7) or 0.7),
        num_ctx=int(cfg.get("agent_num_ctx") or cfg.get("ollama_num_ctx") or 32768),
        num_predict=int(cfg.get("agent_num_predict") or cfg.get("ollama_num_predict") or 4096),
        think=cfg.get("agent_think", False),
    )


def fallback_llm_config(cfg: Optional[Dict[str, Any]] = None) -> Optional[LLMConfig]:
    """兜底链路：云端（本地模型崩了/效果太差时接手）。

    没有配置 API Key 就返回 None——兜底是「有则用」，不是「必须有」。
    """
    cfg = cfg if cfg is not None else load_app_config()
    key = _env("MY_TEACHER_CHAT_API_KEY") or _env("MY_TEACHER_CLOUD_API_KEY") or cfg.get("chat_api_key") or cfg.get("cloud_api_key") or ""
    if not key:
        return None
    base = (
        _env("MY_TEACHER_CHAT_BASE_URL")
        or cfg.get("chat_base_url")
        or cfg.get("cloud_base_url")
        or "https://api.siliconflow.cn/v1"
    )
    model = _env("MY_TEACHER_CHAT_MODEL") or cfg.get("chat_model") or cfg.get("cloud_model") or "deepseek-ai/DeepSeek-V3"
    return LLMConfig(
        provider="openai",
        model=model,
        base_url=base.rstrip("/"),
        api_key=key,
        temperature=float(cfg.get("chat_temperature", 0.7) or 0.7),
        num_predict=int(cfg.get("agent_num_predict") or 4096),
        think=None,   # 云端模型自行决定是否返回 reasoning_content
    )


def llm_configs() -> List[LLMConfig]:
    """按优先级返回链路列表：主力在前，兜底在后。

    主力由 `data/agent/settings.json` 的 `llm_provider` 决定（默认 cloud）。
    换模型只需要改这一个字段，或在界面上切换（POST /agent/settings），不用动代码。
    """
    cfg = load_app_config()
    settings = load_agent_settings()
    provider = str(settings.get("llm_provider") or "cloud").strip().lower()
    fallback_local = bool(settings.get("fallback_local", True))

    local = primary_llm_config(cfg)
    cloud = fallback_llm_config(cfg)

    if provider in ("cloud", "openai", "remote") and cloud is not None:
        return [cloud] + ([local] if fallback_local else [])
    if provider in ("local", "ollama"):
        return [local] + ([cloud] if cloud is not None else [])
    # auto：云端优先，本地兜底；没配云端就是纯本地
    return ([cloud] if cloud is not None else []) + [local]


def agent_settings() -> Dict[str, Any]:
    """Agent 自己的可调参数（写在 data/config.json 的 agent_* 字段里，缺省即默认值）。"""
    cfg = load_app_config()
    return {
        "max_steps": int(cfg.get("agent_max_steps") or 12),
        "run_timeout": float(cfg.get("agent_run_timeout") or 600),
        "tool_timeout": float(cfg.get("agent_tool_timeout") or 60),
        "python_timeout": float(cfg.get("agent_python_timeout") or 20),
        "public_base": str(cfg.get("agent_public_base") or "").rstrip("/"),
    }
