"""数学可视化工具 —— 图形计算器与画板。

为什么要有这两件工具
--------------------
数学和物理课上，「讲清楚」经常等于「画出来」：
- 一个函数随参数变化怎么变 → 图形计算器（拖滑块实时重画）
- 一道几何题的辅助线怎么添、受力分析怎么画 → 画板（分步作图，像老师一步步写板书）

它们和 `render_ui` 的区别：`render_ui` 是**通用**界面（表格、卡片、小测……），
这两件是**专用**可视化，schema 更严、渲染更专门，所以模型更容易一次用对。

画板采用「分步累积」模型：第 n 步显示前 n 步的所有图形 + 当前步的说明文字。
这与真实板书一致（老师不是一次画完整张图，而是边讲边加），学生也能一步步跟着看。
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Dict, List

from ..registry import ToolContext, ToolResult, tool

# 画板支持的图元（必须与前端 agent_ui.js 的 board 渲染器一致）
SHAPE_KINDS = {
    "axes",      # 坐标系：{"kind":"axes","xRange":[-5,5],"yRange":[-5,5],"grid":true,"xLabel":"x","yLabel":"y"}
    "line",      # 线段：{"kind":"line","from":[0,0],"to":[3,4],"dashed":false,"color":"#f87171","label":"AB"}
    "arrow",     # 箭头（受力/向量）：{"kind":"arrow","from":[...],"to":[...],"label":"F"}
    "ray",       # 射线
    "circle",    # 圆：{"kind":"circle","center":[0,0],"r":2,"label":"⊙O"}
    "dot",       # 点：{"kind":"dot","at":[1,1],"label":"A"}
    "polygon",   # 多边形：{"kind":"polygon","points":[[0,0],[3,0],[3,4]],"fill":"rgba(96,165,250,.18)","label":"△ABC"}
    "rect",      # 矩形：{"kind":"rect","from":[0,0],"to":[4,3],"label":"ABCD"}
    "curve",     # 函数曲线：{"kind":"curve","expr":"x^2-2x","xRange":[-2,4],"color":"#60a5fa"}
    "arc",       # 圆弧（标角）：{"kind":"arc","center":[0,0],"r":1,"start":0,"end":60,"label":"60°"}
    "angle",     # 直角标记：{"kind":"angle","at":[3,0],"from":[0,0],"to":[3,4]}
    "text",      # 文字注记：{"kind":"text","at":[1,2],"text":"顶点","size":14,"color":"#334155"}
    "measure",   # 标注尺寸（虚线 + 数值）：{"kind":"measure","from":[0,0],"to":[4,0],"label":"4 cm"}
    "hbar",      # 水平辅助线（虚线跨整个画布）：{"kind":"hbar","y":2,"label":"y=2"}
    "vbar",      # 垂直辅助线
}

_EXPR_OK = re.compile(r"^[0-9a-zA-Z_+\-*/^().,\s]*$")

# 允许出现的数学函数（与前端表达式求值器同一套；改这里要同步改 agent_ui.js）
_MATH_FUNCS = {
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh",
    "sqrt", "cbrt", "abs", "exp", "log", "ln", "log2", "log10",
    "floor", "ceil", "round", "sign", "pow", "min", "max", "hypot",
    "degrees", "radians",
}

# 表达式里允许出现的语法节点（白名单，其余一律拒绝）
_MATH_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Call, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub, ast.UAdd,
    ast.Tuple, ast.List,
)


def check_math_expr(expr: str) -> str | None:
    """校验数学表达式，返回错误说明（None = 合法）。

    **必须真解析，不能只查字符集**：`import os` 这种字符串全部由合法字符组成，
    只查字符就放它过去了，前端拿到只能画出一张**空白图且不报错**。
    这类静默失败对学生和模型都是灾难：模型以为画好了，学生看到一片空白。

    `ast.parse(..., mode="eval")` 天然挡掉语句（import / 赋值 / 多语句），
    再叠加节点白名单与函数白名单，保证「要么画出正确的图，要么给出能改的错误」。
    """
    if not expr or not expr.strip():
        return "表达式为空"
    if len(expr) > 300:
        return "表达式过长（上限 300 字符）"
    # 先归一化，再查字符、再解析：
    # 数学写法里 `^` 是乘方（Python 里却是异或），`×÷−π` 也常见，
    # 前端求值器都按数学含义处理，所以服务端校验必须先转换——
    # 否则 `a*x^2+b*x+c` 这种最常见的二次函数会被判成非法（实测踩过）。
    parse_src = expr
    for src, dst in (("^", "**"), ("×", "*"), ("·", "*"), ("÷", "/"),
                     ("−", "-"), ("–", "-"), ("—", "-"), ("π", "pi")):
        parse_src = parse_src.replace(src, dst)
    if not _EXPR_OK.match(parse_src):
        return "含有不支持的字符（只支持变量、数字、+ - * / ^ ( ) 和函数名）"
    try:
        tree = ast.parse(parse_src, mode="eval")
    except SyntaxError as exc:
        return f"语法错误：{exc.msg}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "id", None)
            if name is None:
                return "只能直接调用函数名（不支持 obj.method() 这种写法）"
            if name.lower() not in _MATH_FUNCS:
                return f"不支持的函数 `{name}`（可用：sin cos tan sqrt abs exp log ln pow min max …）"
        elif isinstance(node, ast.Name):
            continue          # 变量名（x、参数名）允许，实际取值交给前端
        elif not isinstance(node, _MATH_NODES):
            return f"表达式里有不支持的语法（{type(node).__name__}）"
    return None


def _clean_shapes(shapes: Any) -> tuple[List[Dict[str, Any]], List[str]]:
    """过滤 + 校验图元，返回 (合法图元, 问题列表)。"""
    out: List[Dict[str, Any]] = []
    problems: List[str] = []
    if not isinstance(shapes, list):
        return [], ["shapes 必须是数组"]
    for i, shape in enumerate(shapes):
        if not isinstance(shape, dict):
            problems.append(f"shapes[{i}] 必须是对象")
            continue
        kind = str(shape.get("kind") or "").strip()
        if kind not in SHAPE_KINDS:
            problems.append(f"shapes[{i}] 的 kind=`{kind}` 不支持（可用：{'、'.join(sorted(SHAPE_KINDS))}）")
            continue
        # 模型常把矩形写成 points 四点（polygon 的习惯）。这里做一次归一化，
        # 而不是直接判错——宽容地接受合理写法，比让模型反复试错更省轮次。
        if kind == "rect" and not (shape.get("from") and shape.get("to")):
            pts = shape.get("points")
            if isinstance(pts, list) and len(pts) >= 3:
                shape = dict(shape)
                shape["from"] = pts[0]
                shape["to"] = pts[2]
        if kind in ("line", "arrow", "ray", "measure", "rect") and not (shape.get("from") and shape.get("to")):
            problems.append(f"shapes[{i}]（{kind}）缺少 from / to 坐标（矩形也可以给四点 points）")
            continue
        if kind == "curve":
            expr = str(shape.get("expr") or "")
            err = check_math_expr(expr)
            if err:
                problems.append(f"shapes[{i}] 的曲线表达式有问题：{err}")
                continue
        out.append(shape)
    return out, problems


@tool(
    "plot_function",
    "画函数图像（图形计算器）：可同时画多条曲线，每条曲线可以带参数滑块，学生拖动参数图像实时变化。"
    "讲函数性质、参数影响、方程解、物理公式关系时用它。",
    {
        "type": "object",
        "properties": {
            "series": {
                "type": "array",
                "description": "要画的曲线数组，如 [{\"expr\":\"a*x^2+b\",\"label\":\"抛物线\","
                               "\"params\":[{\"name\":\"a\",\"value\":1,\"min\":-3,\"max\":3},{\"name\":\"b\",\"value\":0,\"min\":-5,\"max\":5}]}]",
                "items": {"type": "object"},
            },
            "xMin": {"type": "number", "description": "横轴最小值，默认 -10"},
            "xMax": {"type": "number", "description": "横轴最大值，默认 10"},
            "title": {"type": "string", "description": "图标题"},
            "height": {"type": "integer", "description": "画布高度（像素），默认 300"},
        },
        "required": ["series"],
    },
    produces=["ui"],
)
async def plot_function(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    raw = args.get("series")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return ToolResult.fail("series 必须是数组（JSON），例如 [{\"expr\":\"x^2\"}]")
    if not isinstance(raw, list) or not raw:
        return ToolResult.fail("series 不能为空，至少要有一条曲线，例如 [{\"expr\":\"x^2\"}]")

    series: List[Dict[str, Any]] = []
    problems: List[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            problems.append(f"series[{i}] 必须是对象")
            continue
        expr = str(item.get("expr") or "").strip()
        err = check_math_expr(expr)
        if err:
            problems.append(f"series[{i}] 的表达式 `{expr}` 有问题：{err}")
            continue
        entry: Dict[str, Any] = {"expr": expr}
        if item.get("label"):
            entry["label"] = str(item["label"])
        if item.get("color"):
            entry["color"] = str(item["color"])
        params = []
        for p in item.get("params") or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            params.append(
                {
                    "name": str(p["name"]),
                    "value": float(p.get("value", 1)),
                    "min": float(p.get("min", -5)),
                    "max": float(p.get("max", 5)),
                    "step": float(p.get("step", 0.1)),
                }
            )
        if params:
            entry["params"] = params
        series.append(entry)

    if problems:
        return ToolResult.fail("函数表达式有问题：\n- " + "\n- ".join(problems[:5]))
    if not series:
        return ToolResult.fail("没有可用的曲线表达式。")

    x_min = float(args.get("xMin", -10) or -10)
    x_max = float(args.get("xMax", 10) or 10)
    if x_max <= x_min:
        x_min, x_max = -10.0, 10.0
    spec = {
        "title": str(args.get("title") or "函数图像"),
        "items": [
            {
                "type": "plot",
                "title": str(args.get("title") or ""),
                "xMin": x_min,
                "xMax": x_max,
                "height": int(args.get("height") or 300),
                "series": series,
            }
        ],
    }
    param_note = "，含参数滑块" if any(s.get("params") for s in series) else ""
    return ToolResult(
        summary=(
            f"已画好函数图像（{len(series)} 条曲线{param_note}），学生可以拖动参数看变化。"
            "不要在正文里复述图像，直接讲结论。"
        ),
        ui=spec,
        data={"series": series, "xMin": x_min, "xMax": x_max},
    )


@tool(
    "draw_board",
    "画板（板书作图）：按步骤画几何图形、受力分析、函数曲线、示意图。"
    "每一步可以附一句说明，学生点「下一步」就能看着图一步步长出来。"
    "讲几何证明、物理受力、坐标变换、电路/光路示意时用它。",
    {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "description": "作图步骤数组，如 [{\"caption\":\"先画三角形 ABC\",\"shapes\":[{\"kind\":\"polygon\","
                               "\"points\":[[0,0],[4,0],[4,3]],\"label\":\"△ABC\"}]}]",
                "items": {"type": "object"},
            },
            "title": {"type": "string", "description": "画板标题"},
            "xRange": {"type": "array", "description": "横轴范围 [min,max]，默认 [-5,5]"},
            "yRange": {"type": "array", "description": "纵轴范围 [min,max]，默认 [-3,3]"},
            "height": {"type": "integer", "description": "画布高度（像素），默认 400"},
        },
        "required": ["steps"],
    },
    produces=["ui"],
)
async def draw_board(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    raw_steps = args.get("steps")
    if isinstance(raw_steps, str):
        try:
            raw_steps = json.loads(raw_steps)
        except Exception:
            return ToolResult.fail("steps 必须是数组（JSON），每项形如 {\"caption\":\"...\",\"shapes\":[...]}")
    if not isinstance(raw_steps, list) or not raw_steps:
        return ToolResult.fail(
            "steps 不能为空。至少给一步，形如 "
            "[{\"caption\":\"画坐标系\",\"shapes\":[{\"kind\":\"axes\",\"xRange\":[-5,5],\"yRange\":[-3,3]}]}]"
        )

    steps: List[Dict[str, Any]] = []
    problems: List[str] = []
    for i, step in enumerate(raw_steps[:12], 1):
        if not isinstance(step, dict):
            problems.append(f"steps[{i}] 必须是对象")
            continue
        shapes, shape_problems = _clean_shapes(step.get("shapes"))
        problems.extend(f"第 {i} 步：{p}" for p in shape_problems)
        if shapes:
            steps.append({"caption": str(step.get("caption") or f"第 {i} 步"), "shapes": shapes})

    if problems:
        return ToolResult.fail("作图指令有问题，请修正后重新调用：\n- " + "\n- ".join(problems[:6]))
    if not steps:
        return ToolResult.fail("没有可用的作图步骤。")

    def _range(value: Any, fallback: List[float]) -> List[float]:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                lo, hi = float(value[0]), float(value[1])
                return [lo, hi] if hi > lo else fallback
            except Exception:
                return fallback
        return fallback

    spec = {
        "title": str(args.get("title") or ""),
        "items": [
            {
                "type": "board",
                "title": str(args.get("title") or ""),
                "height": int(args.get("height") or 400),
                "xRange": _range(args.get("xRange"), [-5.0, 5.0]),
                "yRange": _range(args.get("yRange"), [-3.0, 3.0]),
                "steps": steps,
            }
        ],
    }
    total_shapes = sum(len(s["shapes"]) for s in steps)
    return ToolResult(
        summary=(
            f"画板已就绪：{len(steps)} 步、共 {total_shapes} 个图形，学生可以按「下一步」跟着看。"
            "讲解时按步骤顺序说，不要复述图形本身。"
        ),
        ui=spec,
        data={"steps": len(steps), "shapes": total_shapes},
    )
