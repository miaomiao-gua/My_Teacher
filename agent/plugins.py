"""插件机制 —— 「调试成熟的复杂功能」固化下来的地方。

设计目标（对应产品需求）
------------------------
讲酸碱盐时想让学生动手做滴定仿真，这种界面一次生成往往不完美，要来回改好几轮。
改好之后不该每次上课都重新生成一遍（慢、还不稳定），而应该**固化成一个稳定工具**：
以后模型一句 `render_scene(plugin="acid_base_lab", params={...})` 就能调出来。

一个插件 = 一个目录，包含：

    agent/plugins/<name>/
        plugin.json     清单：插件元信息 + 工具定义 + 前端资源位置
        handler.py      工具实现（函数签名与内置工具一致）
        ui/index.html   可选：插件自带的前端界面（沙箱 iframe 里运行）

`plugin.json` 示例::

    {
      "name": "acid_base_lab",
      "title": "酸碱滴定仿真",
      "description": "可交互的酸碱中和滴定实验……（这段描述会直接进模型的工具列表，
                      写得越清楚，模型越知道什么时候该用它）",
      "version": "0.1.0",
      "tools": [
        {"name": "acid_base_lab",
         "description": "打开酸碱滴定仿真界面",
         "parameters": {"type": "object", "properties": {"acid": {"type": "string"}}, "required": []},
         "handler": "handler.py:run",
         "produces": ["ui"]}
      ],
      "ui": {"entry": "ui/index.html", "height": 560, "title": "酸碱滴定仿真"}
    }

热重载：`POST /agent/plugins/reload` 会卸载全部插件工具再重新扫描——
调试插件时不用重启服务（这是「多调试几轮」的工程前提）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import PLUGINS_DIR
from .registry import Tool, register, unregister_source

PLUGIN_SOURCE_PREFIX = "plugin:"


@dataclass
class Plugin:
    name: str
    title: str
    description: str
    version: str
    path: Path
    tool_names: List[str] = field(default_factory=list)
    ui: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    loaded: bool = False

    def public(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "version": self.version,
            "tools": self.tool_names,
            "ui": self.ui or None,
            "loaded": self.loaded,
            "error": self.error,
            "path": str(self.path),
        }


PLUGINS: Dict[str, Plugin] = {}


def _load_handler(module_path: Path) -> Any:
    """按文件路径导入插件模块（插件目录不在 sys.path 里，所以手工 spec 加载）。"""
    mod_name = f"agent_plugin_{module_path.parent.name}_{module_path.stem}"
    spec = importlib.util.spec_from_file_location(mod_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载 {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_handler(spec_str: str, plugin_path: Path) -> Callable:
    """解析 `handler.py:run` 形式的处理函数引用。"""
    if ":" not in spec_str:
        raise ValueError(f"handler 必须写成 `文件:函数`，收到：{spec_str}")
    file_part, func_name = spec_str.split(":", 1)
    module = _load_handler(plugin_path / file_part.strip())
    fn = getattr(module, func_name.strip(), None)
    if fn is None or not callable(fn):
        raise ValueError(f"{file_part} 里找不到可调用的 {func_name}")
    return fn


def load_plugins(verbose: bool = False) -> Dict[str, Plugin]:
    """扫描并加载全部插件（幂等：先卸载同源工具再注册）。"""
    PLUGINS.clear()
    if not PLUGINS_DIR.exists():
        return PLUGINS

    for child in sorted(PLUGINS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        manifest_path = child / "plugin.json"
        if not manifest_path.exists():
            continue
        plugin = Plugin(name=child.name, title=child.name, description="", version="0.0.0", path=child)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            plugin.name = manifest.get("name") or child.name
            plugin.title = manifest.get("title") or plugin.name
            plugin.description = manifest.get("description") or ""
            plugin.version = str(manifest.get("version") or "0.0.0")
            plugin.ui = manifest.get("ui") or {}

            source = PLUGIN_SOURCE_PREFIX + plugin.name
            unregister_source(source)

            tool_defs = manifest.get("tools") or []
            if not tool_defs and plugin.ui:
                # 只声明了前端界面、没写工具：自动补一个「打开界面」工具，
                # 让最简单的插件（一个做好的 HTML）也能被模型直接调用。
                tool_defs = [
                    {
                        "name": plugin.name,
                        "description": plugin.description or f"打开{plugin.title}界面",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                        "handler": "__ui__",
                        "produces": ["ui"],
                    }
                ]

            for td in tool_defs:
                name = td.get("name") or plugin.name
                handler_spec = td.get("handler") or "__ui__"
                if handler_spec == "__ui__":
                    entry = (plugin.ui or {}).get("entry")
                    if not entry:
                        raise ValueError(f"工具 {name} 既没有 handler 也没有 ui.entry")
                    handler = _make_ui_handler(plugin, entry)
                else:
                    handler = _resolve_handler(handler_spec, child)
                register(
                    Tool(
                        name=name,
                        description=td.get("description") or plugin.description or name,
                        parameters=td.get("parameters") or {"type": "object", "properties": {}, "required": []},
                        handler=handler,
                        parallel_safe=bool(td.get("parallel_safe", True)),
                        source=source,
                        produces=list(td.get("produces") or (["ui"] if plugin.ui else [])),
                    )
                )
                plugin.tool_names.append(name)

            plugin.loaded = True
            if verbose:
                print(f"[plugin] 已加载 {plugin.name} v{plugin.version} → 工具 {plugin.tool_names}")
        except Exception as exc:
            plugin.error = f"{exc}"
            if verbose:
                traceback.print_exc()
        PLUGINS[plugin.name] = plugin
    return PLUGINS


def _make_ui_handler(plugin: Plugin, entry: str) -> Callable:
    """给「纯界面插件」生成默认 handler：直接把插件界面作为 UI 推给学生。"""
    height = int((plugin.ui or {}).get("height") or 560)
    title = (plugin.ui or {}).get("title") or plugin.title

    async def handler(ctx, args):  # noqa: ANN001
        from .registry import ToolResult

        query = ""
        if args:
            import urllib.parse

            query = "?" + urllib.parse.urlencode({k: v for k, v in args.items() if isinstance(v, (str, int, float))})
        spec = {
            "title": title,
            "items": [
                {"type": "frame", "src": f"/agent/plugins/{plugin.name}/{entry}{query}", "height": height},
            ],
        }
        return ToolResult(
            summary=f"已打开「{title}」交互界面（学生可以自己操作）。",
            ui=spec,
            data={"plugin": plugin.name, "params": args},
        )

    return handler


def list_plugins() -> List[Dict[str, Any]]:
    return [p.public() for p in PLUGINS.values()]


def reload_plugins() -> List[Dict[str, Any]]:
    load_plugins(verbose=True)
    return list_plugins()
