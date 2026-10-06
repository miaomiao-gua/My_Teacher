"""生成式 UI 工具 —— 「AI 直接写界面」的入口。

这是整个重构里最核心的能力：老师不只用文字讲课，而是**现场造出学生能操作的界面**。
模型通过 `render_ui` 输出一棵声明式组件树，前端渲染器把它画到主舞台上。

为什么用「组件树 + 白名单」而不是让模型直接写 HTML
--------------------------------------------------
- 组件树的渲染结果是可控的：字体、配色、交互跟应用主题一致，不会五颜六色各写各的
- 安全性：没有任意脚本执行，模型写错也不会把页面搞崩
- 可交互：`quiz` 学生点选即判分、`slider` 拖动即联动、`plot` 参数实时重绘——这些是
  「写死的一坨 HTML」做不到的

需要**完全自由的界面**时走另一条路：`write_file` 写单文件 HTML → `run_python` 验证 →
`show_artifact` 放进沙箱 iframe。两条路各司其职：
**结构化的用组件树，自由的用 HTML。**
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Dict, List

from ..registry import ToolContext, ToolResult, tool
from ..sandbox import kind_of
from ..session import safe_relpath

# 组件白名单：与前端渲染器（client/static/js/agent_ui.js）保持一致
KNOWN_TYPES = {
    "text", "list", "keyvalue", "table", "steps", "timeline", "callout", "stat", "progress",
    "badge", "code", "chart", "plot", "quiz", "slider", "tabs", "card", "grid", "row", "col",
    "mermaid", "frame", "image", "audio", "video", "divider", "spacer", "json", "diff",
    "accordion", "hero", "link", "copy", "input", "textarea", "select", "radio", "submit",
    "checkbox", "switch", "button", "keyvalue", "file-tree", "breadcrumb", "diagram", "echart",
}

# 这些字段里装的是「子组件」
_CHILD_KEYS = ("items", "steps", "tabs")
MAX_SPEC_BYTES = 120_000


def validate_spec(spec: Any) -> List[str]:
    """结构校验：拦掉会让前端白屏的错误，并把问题反馈给模型让它自己修。

    只做**结构**校验（缺 type、items 不是数组、嵌套里塞了字符串……），
    不认识的新组件类型只当警告不报错——前端会优雅降级成占位块，
    这样前端加新组件不需要同步改后端校验。
    """
    errors: List[str] = []

    def check(node: Any, path: str, depth: int) -> None:
        if depth > 8:
            errors.append(f"{path} 嵌套过深（最多 8 层）")
            return
        if not isinstance(node, dict):
            errors.append(f"{path} 必须是组件对象，收到 {type(node).__name__}")
            return
        ctype = node.get("type")
        if not isinstance(ctype, str) or not ctype:
            errors.append(f"{path} 缺少 `type` 字段")
            return
        if ctype not in KNOWN_TYPES:
            # 不报错：交给前端降级渲染（并保留给模型一个温和提示）
            pass
        for key in _CHILD_KEYS:
            if key not in node:
                continue
            value = node[key]
            if not isinstance(value, list):
                errors.append(f"{path}.{key} 必须是数组")
                continue
            for i, child in enumerate(value):
                if key == "steps":
                    if isinstance(child, dict) and isinstance(child.get("items"), list):
                        for j, sub in enumerate(child["items"]):
                            check(sub, f"{path}.{key}[{i}].items[{j}]", depth + 1)
                    continue
                if key == "tabs":
                    if isinstance(child, dict) and isinstance(child.get("items"), list):
                        for j, sub in enumerate(child["items"]):
                            check(sub, f"{path}.{key}[{i}].items[{j}]", depth + 1)
                    continue
                check(child, f"{path}.{key}[{i}]", depth + 1)

    if not isinstance(spec, dict):
        return ["spec 必须是一个 JSON 对象，形如 {\"items\":[...]}"]
    items = spec.get("items")
    if not isinstance(items, list) or not items:
        return ["spec 必须包含非空的 `items` 数组，例如 {\"items\":[{\"type\":\"text\",\"content\":\"你好\"}]}"]
    for i, node in enumerate(items):
        check(node, f"items[{i}]", 1)
    return errors


def _count_nodes(spec: Dict[str, Any]) -> int:
    total = 0
    stack: List[Any] = [spec]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if "type" in node:
                total += 1
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return total


@tool(
    "render_ui",
    "把一个交互界面展示给学生（渲染在教室主舞台上）。用于：分步推导、概念对照表、"
    "参数探索（滑块联动）、随堂小测（学生点选即判分）、数据图表、函数图像、"
    "把一段讲解结构化。**能用界面讲清楚的，就不要写一大段文字。**",
    {
        "type": "object",
        "properties": {
            "spec": {
                "type": "object",
                "description": (
                    "组件树，形如 {\"title\":\"可选标题\",\"items\":[组件...]}。"
                    "组件 vocabulary 见系统提示词。每个组件必须有 type 字段。"
                ),
            },
            "mount": {
                "type": "string",
                "description": "展示位置：surface=教室主舞台（默认）；inline=跟在对话气泡里",
            },
        },
        "required": ["spec"],
    },
    produces=["ui"],
)
async def render_ui(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    spec = args.get("spec")
    errors = validate_spec(spec)
    if errors:
        return ToolResult.fail(
            "界面规格有结构错误，请修正后重新调用 render_ui：\n- " + "\n- ".join(errors[:8]),
            error="; ".join(errors[:8]),
        )
    assert isinstance(spec, dict)
    raw = json.dumps(spec, ensure_ascii=False)
    if len(raw.encode("utf-8")) > MAX_SPEC_BYTES:
        return ToolResult.fail(
            f"界面规格太大（{len(raw.encode('utf-8'))} 字节，上限 {MAX_SPEC_BYTES}）。"
            "请拆成两次 render_ui 调用，或把内容精简。"
        )
    mount = (args.get("mount") or "surface").strip()
    types: List[str] = []
    stack: List[Any] = [spec]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("type"):
                types.append(str(node["type"]))
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    uniq = sorted(set(types))
    return ToolResult(
        summary=(
            f"界面已渲染给学生（{_count_nodes(spec)} 个组件：{', '.join(uniq[:8])}）。"
            "不要再在正文里复述界面内容，直接接着讲解即可。"
        ),
        ui={"mount": mount, **spec},
        data={"component_count": _count_nodes(spec), "types": uniq},
    )


def _spec_for_file(session: Any, relpath: str, height: int) -> Dict[str, Any]:
    """按文件类型选择最合适的展示方式。"""
    kind = kind_of(relpath)
    url = session.file_url(relpath)
    name = Path(relpath).name

    if kind == "html":
        return {"items": [{"type": "frame", "src": url, "height": height, "title": name}]}
    if kind == "image":
        return {"items": [{"type": "image", "src": url, "alt": name}]}
    if kind == "json":
        path = session.workspace / relpath
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return {"items": [{"type": "json", "value": value}]}
        except Exception:
            pass
    if kind == "csv":
        path = session.workspace / relpath
        try:
            text = path.read_text(encoding="utf-8-sig")
            rows = list(csv.reader(io.StringIO(text)))
            if rows:
                head, body = rows[0], rows[1:31]
                return {
                    "items": [
                        {"type": "table", "columns": head, "rows": body},
                        {"type": "text", "size": "muted", "content": f"（{name} 前 {len(body)} 行）"},
                    ]
                }
        except Exception:
            pass
    path = session.workspace / relpath
    try:
        content = path.read_text(encoding="utf-8", errors="replace")[:6000]
    except Exception:
        content = "（无法读取文件内容）"
    lang = {"python": "python", "javascript": "javascript", "css": "css", "markdown": "markdown"}.get(kind, "text")
    return {"items": [{"type": "code", "lang": lang, "code": content, "title": name}]}


@tool(
    "show_artifact",
    "把工作目录里生成的文件展示给学生：HTML 会在沙箱里真正运行起来（可点击、可拖动），"
    "图片直接显示，CSV/JSON 变成表格。生成 HTML 仿真后**必须**调用它，否则学生看不到。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作目录内的相对路径，如 acid_base_sim.html"},
            "height": {"type": "integer", "description": "展示高度（像素），默认 560"},
        },
        "required": ["path"],
    },
    produces=["ui"],
)
async def show_artifact(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    relpath = safe_relpath(str(args.get("path") or ""))
    target = ctx.session.workspace / relpath
    if not target.exists() or not target.is_file():
        from ..sandbox import list_files

        available = [f["path"] for f in list_files(ctx.session.workspace)]
        return ToolResult.fail(
            f"工作目录里没有 `{relpath}`。当前可用文件：{', '.join(available) or '（空）'}。"
            "请先用 write_file 生成文件，或改用正确的文件名。"
        )
    height = int(args.get("height") or 560)
    height = max(240, min(1200, height))
    kind = kind_of(relpath)

    # 有确定性错误的页面展示出去 = 学生看到一片点不动的死界面，不如拦下来让模型修
    extra_note = ""
    if kind == "html":
        from .web import is_fatal, lint_html

        problems = await lint_html(target.read_text(encoding="utf-8", errors="replace"), ctx.session.workspace)
        fatal = [p for p in problems if is_fatal(p)]
        if fatal:
            return ToolResult.fail(
                "这个页面有会让它跑不起来的问题，展示给学生只会是一片点不动的死界面，所以先拦住：\n- "
                + "\n- ".join(fatal)
                + "\n请用 write_file 重写修好它，再调用 show_artifact。"
            )
        if problems:
            extra_note = "（另有小问题，建议下次修： " + "；".join(problems[:3]) + "）"

    spec = _spec_for_file(ctx.session, relpath, height)
    return ToolResult(
        summary=(
            f"已把 `{relpath}` 展示给学生"
            + ("（界面已在沙箱中运行，学生可以直接操作）。" if kind == "html" else "。")
            + extra_note
            + "不要在正文里复述界面内容。"
        ),
        ui={"title": Path(relpath).name, **spec},
        data={"path": relpath, "kind": kind},
    )
