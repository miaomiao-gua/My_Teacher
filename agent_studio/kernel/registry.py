"""工具注册表 —— Agent 能做的每一件事，都是这里注册的一个工具。

为什么要有注册表
----------------
老架构里「工具」是提示词里约定的文本标记（`[TOOL:{...}]`），前端解析后自己执行，
结果靠另一个接口二次回灌——**模型在一个回合内看不到工具输出**，所以只能靠
「严禁心算」「写完代码自己逐行检查」这类提示词补丁去堵漏。

注册表把这件事反过来：工具是我们（服务端）拥有的能力，schema 直接喂给模型的
function calling，执行结果当场回灌进同一个回合。

两条设计红线
------------
1. **`summary` 短、`data`/`ui` 长**。`summary` 会进入模型上下文，多轮循环下必须克制；
   富数据只走事件流给前端。
2. **插件工具与内置工具同表**。模型看到的只有一个扁平工具列表，不区分来源；
   这让「调试成熟的功能固化成插件」在模型侧完全透明（`source` 只用于前端标注与热重载）。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass
class ToolContext:
    """工具执行时拿到的上下文。工具通过它访问会话、工作目录，并主动推事件。"""

    session: Any                      # agent.session.Session
    call_id: str
    step: int = 0
    username: Optional[str] = None
    _emit: Optional[Callable[[Any], Awaitable[None]]] = None

    async def push(self, event: Any) -> None:
        """工具主动往前端推一个事件（如长任务的进度、即时 UI）。

        工具不返回值也能产生效果——比如 `write_file` 写完立刻把预览面板推出去。
        """
        if self._emit is not None:
            await self._emit(event)

    @property
    def workspace(self):
        return self.session.workspace

    @property
    def lesson_folder(self) -> Optional[str]:
        return getattr(self.session, "lesson_folder", None)


@dataclass
class ToolResult:
    """工具的执行结果。

    - ``summary``: 回灌给模型的文本，**保持简短**（建议 < 1500 字），否则多轮循环会吃光上下文
    - ``data``:    结构化结果，只给前端（可以很大）
    - ``ui``:      生成式 UI 规格，前端渲染到主舞台
    - ``artifacts``: 产出的文件 [{path, name, kind, url, bytes}]
    - ``stage``:   角色表现 {emotion, motion, speak}
    """

    ok: bool = True
    summary: str = ""
    data: Any = None
    ui: Optional[Dict[str, Any]] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    stage: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    @classmethod
    def fail(cls, summary: str, error: Optional[str] = None) -> "ToolResult":
        """失败也要回灌给模型——让它有机会自己改参数重试，而不是直接崩掉整轮对话。"""
        return cls(ok=False, summary=summary, error=error or summary)


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Any]
    parallel_safe: bool = True
    source: str = "builtin"
    # 前端提示：这个工具会产出 UI / 文件 / 角色表现，便于前端做面板切换
    produces: List[str] = field(default_factory=list)
    # 这个工具自己的超时（秒）。不填就用全局 tool_timeout。
    # 为什么需要：备课要调一次大模型生成整份教案（1~3 分钟），
    # 用全局的 60 秒会被误杀 ——「工具自己知道要多久」比调大全局超时更准确。
    timeout: Optional[float] = None

    def schema(self, provider: str = "ollama") -> Dict[str, Any]:
        """生成 function calling 的 schema。

        Ollama 与 OpenAI 兼容接口用的是同一份 JSON Schema，只是外层包装略不同，
        所以两种 provider 共用这里，避免出现「本地能调、云端调不动」的分裂。
        """
        fn = {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters
            or {"type": "object", "properties": {}, "required": []},
        }
        if provider == "openai":
            return {"type": "function", "function": fn}
        return {"type": "function", "function": fn}

    def validate(self, args: Dict[str, Any]) -> Optional[str]:
        """极简参数校验：只查必填与顶层类型。

        为什么不用 jsonschema：本地小模型经常给出「差不多对」的参数（数字给成字符串等），
        过严的校验会让它反复失败；这里只拦住会导致 handler 崩溃的情况，
        宽容的部分交给 handler 自己转换（并在失败时返回可读错误让模型重试）。
        """
        if not isinstance(args, dict):
            return "参数必须是 JSON 对象"
        spec = self.parameters or {}
        for key in spec.get("required", []) or []:
            if key not in args or args[key] in (None, ""):
                return f"缺少必填参数 `{key}`"
        props = spec.get("properties", {}) or {}
        for key, value in args.items():
            if key not in props or value is None:
                continue
            want = props[key].get("type")
            if want == "string" and not isinstance(value, str):
                args[key] = json_str(value)
            elif want == "integer" and isinstance(value, float) and value.is_integer():
                args[key] = int(value)
        return None

    async def call(self, ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
        result = self.handler(ctx, args)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, ToolResult):
            result = ToolResult(summary=str(result))
        return result


def json_str(value: Any) -> str:
    import json

    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


# ---------------------------------------------------------------------------
# 全局注册表
# ---------------------------------------------------------------------------
REGISTRY: Dict[str, Tool] = {}


def register(tool_obj: Tool, *, replace: bool = True) -> Tool:
    if not replace and tool_obj.name in REGISTRY:
        raise ValueError(f"工具已存在: {tool_obj.name}")
    REGISTRY[tool_obj.name] = tool_obj
    return tool_obj


def tool(
    name: str,
    description: str,
    parameters: Optional[Dict[str, Any]] = None,
    *,
    parallel_safe: bool = True,
    source: str = "builtin",
    produces: Optional[List[str]] = None,
    timeout: Optional[float] = None,
):
    """把一个函数注册成工具。

    handler 签名固定为 ``(ctx: ToolContext, args: dict) -> ToolResult | Awaitable[ToolResult]``。
    """

    def deco(fn: Callable):
        register(
            Tool(
                name=name,
                description=description,
                parameters=parameters or {"type": "object", "properties": {}, "required": []},
                handler=fn,
                parallel_safe=parallel_safe,
                source=source,
                produces=list(produces or []),
                timeout=timeout,
            )
        )
        return fn

    return deco


def get(name: str) -> Optional[Tool]:
    return REGISTRY.get(name)


def all_tools() -> List[Tool]:
    return list(REGISTRY.values())


def tools_payload(provider: str = "ollama") -> List[Dict[str, Any]]:
    return [t.schema(provider) for t in REGISTRY.values()]


def describe_tools() -> List[Dict[str, Any]]:
    """给前端/调试用的工具清单（不含 handler）。"""
    return [
        {
            "name": t.name,
            "description": t.description,
            "source": t.source,
            "parallel_safe": t.parallel_safe,
            "produces": t.produces,
            "parameters": t.parameters,
        }
        for t in REGISTRY.values()
    ]


def unregister_source(source: str) -> int:
    """按来源卸载工具（插件热重载用）。"""
    names = [n for n, t in REGISTRY.items() if t.source == source]
    for n in names:
        REGISTRY.pop(n, None)
    return len(names)
