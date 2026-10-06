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
    expr = expr.strip()
    if len(expr) > 300:
        return "表达式过长（上限 300 字符）"
    parse_src = _norm_expr(expr)
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


def _norm_expr(expr: str) -> str:
    """数学写法 → Python 能解析的写法。

    `^` 是乘方（Python 里却是异或），`×÷−π` 这些也很常见，前端求值器都按数学
    含义处理，所以服务端校验必须先转换 —— 否则 `a*x^2+b*x+c` 这种最常见的
    二次函数会被判成非法（实测踩过）。

    隐式乘法（`2x`、`3sin(x)`、`xy`）也要补上：前端求值器认这种写法，
    服务端要是只认 `2*x`，就会出现「校验说非法、其实能画」的假拦截。
    **改这里要同步改 agent_ui.js 的 compileExpr**，两边必须一致。
    """
    out = (expr or "").strip()
    for src, dst in (("^", "**"), ("×", "*"), ("·", "*"), ("÷", "/"),
                     ("−", "-"), ("–", "-"), ("—", "-"), ("π", "pi")):
        out = out.replace(src, dst)
    out = re.sub(r"(\d)\s*([a-zA-Z(])", r"\1*\2", out)
    out = re.sub(r"\)\s*(\d|[a-zA-Z(])", r")*\1", out)
    # `xy` 就是 x·y；只在全是 x/y 时才拆，免得把 sin、speed 这类名字拆坏
    out = re.sub(r"(?<![a-zA-Z0-9_.])([xy]{2,})(?![a-zA-Z0-9_(])",
                 lambda m: "*".join(m.group(1)), out)
    return out


# ---------------------------------------------------------------------------
# 图形计算器：一行公式 → 它到底是什么
# ---------------------------------------------------------------------------
# 前端 agent_ui.js 里有一套一模一样的判断（用户敲进去的公式也要走同一套规则）。
# 这里这一份的价值是：**在模型那边就把错误拦住并说清楚**，别让学生看到空白画布。
_TEXT_DESMOS = re.compile(r'^\(\s*(["\'])(.*?)\1\s*(?:,\s*\(([^)]*)\)\s*)?\)$', re.S)
_TEXT_AT = re.compile(r'^(["\'])(.*?)\1\s*(?:@\s*\(?([^)]*?)\)?)?$', re.S)
_NAME_RE = re.compile(r"^[a-zA-Z]\w*$")
_FX_LHS_RE = re.compile(r"^[a-zA-Z]\w*\s*\(\s*x\s*\)$")


def _split_commas_top(src: str) -> List[str]:
    """顶层逗号切分（括号里的逗号不算）"""
    out: List[str] = []
    depth, quote, cur = 0, None, ""
    for ch in src:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            cur += ch
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
            continue
        cur += ch
    out.append(cur)
    return out


def _split_top_ops(src: str) -> List[tuple]:
    """在括号/引号之外找最外层运算符（= < > <= >=）"""
    ops: List[tuple] = []
    depth, quote, i = 0, None, 0
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0:
            if ch in "<>":
                two = src[i + 1:i + 2] == "="
                ops.append((ch + "=" if two else ch, i))
                if two:
                    i += 1
            elif ch == "=" and src[i + 1:i + 2] != "=" and (i == 0 or src[i - 1] not in "=<>"):
                ops.append(("=", i))
        i += 1
    return ops


def _names(expr: str) -> set:
    """表达式里用到的名字（变量/参数），函数名与内置常数不算"""
    try:
        tree = ast.parse(_norm_expr(expr), mode="eval")
    except SyntaxError:
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
    return out - _MATH_FUNCS - {"pi", "e", "tau"}


def _point_parts(src: str):
    s = src.strip()
    if not (s.startswith("(") and s.endswith(")")):
        return None
    parts = [p.strip() for p in _split_commas_top(s[1:-1])]
    if len(parts) != 2:
        return None
    if any(check_math_expr(p) for p in parts):
        return None
    return [parts[0], parts[1]]


def classify_row(src: str) -> Dict[str, Any]:
    """判断一行公式是什么（与前端 classifyRow 同一套规则）。

    返回 {"kind": "explicit|implicit|vertical|ineqY|ineqXY|point|text|constant|empty|error", ...}
    """
    s = str(src or "").strip()
    if not s:
        return {"kind": "empty"}
    m = _TEXT_DESMOS.match(s) or _TEXT_AT.match(s)
    if m:
        text = m.group(2)
        at = None
        if m.group(3) and m.group(3).strip():
            parts = [p.strip() for p in _split_commas_top(m.group(3))]
            if len(parts) == 2 and not any(check_math_expr(p) for p in parts):
                at = [parts[0], parts[1]]
        return {"kind": "text", "text": text, "at": at}
    if s[0] in "\"'":
        return {"kind": "text", "text": s.strip("\"'")}

    ops = _split_top_ops(s)
    op, idx = ops[0] if ops else (None, 0)
    if op is None:
        pt = _point_parts(s)
        if pt:
            return {"kind": "point", "x": pt[0], "y": pt[1]}
        err = check_math_expr(s)
        if err:
            return {"kind": "error", "error": err}
        names = _names(s)
        # 只写了左边（x^2+y^2-16）也当方程看：等于 0 那条线
        return {
            "kind": "implicit" if "y" in names else "explicit",
            "expr": s,
            "params": sorted(names - {"x", "y"}),
        }

    lhs, rhs = s[:idx].strip(), s[idx + len(op):].strip()
    if not lhs or not rhs:
        return {"kind": "error", "error": "运算符两边都要有内容"}

    if op == "=":
        if _NAME_RE.match(lhs) and lhs not in ("x", "y"):
            err = check_math_expr(rhs)
            if err is None:
                nm = _names(rhs)
                if "x" not in nm and "y" not in nm:
                    return {"kind": "constant", "name": lhs, "expr": rhs,
                            "params": sorted(nm)}
        if _FX_LHS_RE.match(lhs) or lhs == "y":
            err = check_math_expr(rhs)
            if err is None and "y" not in _names(rhs):
                return {"kind": "explicit", "expr": rhs,
                        "params": sorted(_names(rhs) - {"x", "y"})}
        for part in (lhs, rhs):
            err = check_math_expr(part)
            if err:
                return {"kind": "error", "error": f"`{part}` 有问题：{err}"}
        nm = _names(lhs) | _names(rhs)
        if not (nm & {"x", "y"}):
            return {"kind": "error", "error": "这一行看不出画什么（定义参数请写单字母，如 a=2）"}
        return {
            "kind": "implicit" if "y" in nm else "vertical",
            "expr": f"({lhs})-({rhs})",
            "params": sorted(nm - {"x", "y"}),
        }

    # 不等式
    if lhs == "y":
        err = check_math_expr(rhs)
        if err is None and "y" not in _names(rhs):
            return {"kind": "ineqY", "op": op, "expr": rhs,
                    "params": sorted(_names(rhs) - {"x", "y"})}
    for part in (lhs, rhs):
        err = check_math_expr(part)
        if err:
            return {"kind": "error", "error": f"`{part}` 有问题：{err}"}
    nm = _names(lhs) | _names(rhs)
    if not (nm & {"x", "y"}):
        return {"kind": "error", "error": "不等式里没有 x 或 y"}
    return {"kind": "ineqXY", "op": op, "expr": f"({lhs})-({rhs})",
            "params": sorted(nm - {"x", "y"})}


_KIND_CN = {
    "explicit": "函数曲线", "implicit": "隐式方程", "vertical": "竖直线",
    "ineqY": "不等式区域", "ineqXY": "不等式区域", "point": "点",
    "text": "文字标注", "constant": "参数",
}
MAX_CALC_ROWS = 24


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


_GRAPH_TOOL_DESC = (
    "图形计算器：**只要写公式就能画图**，而且学生可以在界面上自己改公式、拖参数滑块。"
    "一行一个公式，支持：显函数 `y=x^2-2x-3`、隐式方程（圆/椭圆）`x^2+y^2=16`、"
    "不等式 `y<=x+1`（会填色）、竖直线 `x=3`、点 `(2,3)`、文字标注 `\"圆心 O\"`、"
    "参数 `a=2`（自动变成可拖的滑块）。讲函数图像、方程与不等式、圆锥曲线、参数影响时用它。"
)

_GRAPH_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "expressions": {
            "type": "array",
            "description": (
                "公式数组，一行一个字符串。例如："
                "[\"x^2+y^2=16\", \"y=x^2-2x-3\", \"y<=x+1\", \"(2,3)\", \"\\\"圆心 O\\\"\", \"a=2\", \"y=a*x\"]。"
                "学生能直接在上面改这些公式，所以尽量写成「学生看得懂、改得动」的形式。"
            ),
            "items": {"type": "string"},
        },
        "texts": {
            "type": "array",
            "description": "图上要写的字（也可以直接写成 expressions 里的一行）：[{\"text\":\"圆心 O\",\"at\":[0,4.5]}]",
            "items": {"type": "object"},
        },
        "params": {
            "type": "array",
            "description": "参数滑块的取值范围（不写就默认 -5..5）：[{\"name\":\"a\",\"value\":1,\"min\":-5,\"max\":5,\"step\":0.1}]",
            "items": {"type": "object"},
        },
        "xMin": {"type": "number", "description": "横轴最小值，默认 -10"},
        "xMax": {"type": "number", "description": "横轴最大值，默认 10"},
        "yMin": {"type": "number", "description": "纵轴最小值（不写就按内容自动取景）"},
        "yMax": {"type": "number", "description": "纵轴最大值"},
        "title": {"type": "string", "description": "标题"},
        "height": {"type": "integer", "description": "画布高度（像素），默认 430"},
        "grid": {"type": "boolean", "description": "是否显示网格，默认显示"},
        "equalAspect": {"type": "boolean", "description": "纵横等比例（默认 true，圆才是圆）"},
        "decimals": {
            "type": "integer",
            "description": ("精确度：坐标刻度与读数保留几位小数（0~6）。不写 = 自动。"
                            "想让坐标读成整数写 0，想读细一点写 2~4。学生也能在界面上自己改。"),
        },
        "quality": {
            "type": "string",
            "description": ("曲线画得多细：standard（默认，快）/ hd（高清）/ ultra（超清，"
                            "放大看圆和曲线最顺滑）。学生也能自己改。"),
        },
    },
    "required": ["expressions"],
}


def _clean_calc_rows(raw: Any, problems: List[str]) -> List[Any]:
    """把模型给的每一条归一化成「一行文本」或带样式的对象（前端两种都吃）"""
    rows: List[Any] = []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return rows
    for i, item in enumerate(raw):
        if len(rows) >= MAX_CALC_ROWS:
            problems.append(f"公式太多了（上限 {MAX_CALC_ROWS} 行），请精简")
            break
        if isinstance(item, dict):
            if item.get("text") is not None:
                entry: Dict[str, Any] = {"text": str(item["text"])}
                at = item.get("at")
                if isinstance(at, (list, tuple)) and len(at) == 2:
                    try:
                        entry["at"] = [float(at[0]), float(at[1])]
                    except Exception:
                        pass
                if item.get("color"):
                    entry["color"] = str(item["color"])
                rows.append(entry)
                continue
            if item.get("point") is not None:
                pt = item["point"]
                if isinstance(pt, (list, tuple)) and len(pt) == 2:
                    try:
                        rows.append({"point": [float(pt[0]), float(pt[1])],
                                     "label": str(item.get("label") or "")})
                        continue
                    except Exception:
                        pass
                problems.append(f"expressions[{i}] 的 point 要写成两个数，如 [2,3]")
                continue
            if item.get("name") is not None and item.get("value") is not None:
                rows.append(f"{item['name']} = {item['value']}")
                continue
            src = item.get("expr")
            if src is None:
                src = item.get("formula")
            if src is None:
                problems.append(f"expressions[{i}] 里既没有 expr 也没有 text / point")
                continue
        else:
            src = item
        src = str(src).strip()
        if not src:
            continue
        info = classify_row(src)
        if info["kind"] == "error":
            problems.append(f"expressions[{i}]（`{src}`）：{info['error']}")
            continue
        row: Dict[str, Any] = {"expr": src}
        if isinstance(item, dict):
            for key in ("color", "label"):
                if item.get(key):
                    row[key] = str(item[key])
            if item.get("hidden"):
                row["hidden"] = True
        rows.append(row)
    return rows


def _collect_params(args: Dict[str, Any], rows: List[Any]) -> List[Dict[str, Any]]:
    params: List[Dict[str, Any]] = []
    seen = set()
    raw = args.get("params")
    if isinstance(raw, list):
        for p in raw[:12]:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            name = str(p["name"])
            try:
                entry = {
                    "name": name,
                    "value": float(p.get("value", 1)),
                    "min": float(p.get("min", -5)),
                    "max": float(p.get("max", 5)),
                    "step": float(p.get("step", 0.1)),
                }
            except Exception:
                continue
            params.append(entry)
            seen.add(name)
    # 公式里用到、但没给范围的参数 → 补一条默认滑块（否则学生只能看到一个死参数）
    for row in rows:
        src = row.get("expr") if isinstance(row, dict) else row
        if not src:
            continue
        for nm in classify_row(str(src)).get("params") or []:
            if nm in seen or not _NAME_RE.match(nm):
                continue
            seen.add(nm)
            params.append({"name": nm, "value": 1, "min": -5, "max": 5, "step": 0.1})
    return params


async def _render_calculator(args: Dict[str, Any]) -> ToolResult:
    """graph_calculator 与 plot_function 共用的实现（两个工具出口，一套行为）"""
    problems: List[str] = []
    raw = args.get("expressions")
    if raw is None:
        raw = args.get("series")
    if raw is None:
        raw = args.get("expr") or args.get("formula")
    if raw is None:
        return ToolResult.fail(
            "请给 expressions（一行一个公式），例如 "
            "[\"x^2+y^2=16\", \"y=x^2-2x-3\", \"(2,3)\"]"
        )
    rows = _clean_calc_rows(raw, problems)

    texts = args.get("texts")
    if isinstance(texts, list):
        for t in texts[:8]:
            if isinstance(t, str):
                rows.append({"text": t})
            elif isinstance(t, dict) and t.get("text") is not None:
                entry = {"text": str(t["text"])}
                at = t.get("at")
                if isinstance(at, (list, tuple)) and len(at) == 2:
                    try:
                        entry["at"] = [float(at[0]), float(at[1])]
                    except Exception:
                        pass
                rows.append(entry)

    if problems:
        return ToolResult.fail("公式有问题，改好再画：\n- " + "\n- ".join(problems[:8]))
    if not rows:
        return ToolResult.fail("一行公式都没有，画不出东西。")

    try:
        x_min = float(args.get("xMin", -10) if args.get("xMin") is not None else -10)
        x_max = float(args.get("xMax", 10) if args.get("xMax") is not None else 10)
    except Exception:
        x_min, x_max = -10.0, 10.0
    if x_max <= x_min:
        x_min, x_max = -10.0, 10.0

    calc: Dict[str, Any] = {
        "type": "calculator",
        "expressions": rows,
        "height": int(args.get("height") or 430),
        "xMin": x_min,
        "xMax": x_max,
        "grid": args.get("grid") is not False,
        "equalAspect": args.get("equalAspect") is not False,
    }
    try:
        if args.get("yMin") is not None and args.get("yMax") is not None:
            ylo, yhi = float(args["yMin"]), float(args["yMax"])
            if yhi > ylo:
                calc["yMin"], calc["yMax"] = ylo, yhi
    except Exception:
        pass
    # 精确度：小数位（0~6）与画质。学生也能在界面上改，这里只是给个初始值。
    try:
        if args.get("decimals") is not None and str(args.get("decimals")) != "":
            d = int(float(args["decimals"]))
            if 0 <= d <= 6:
                calc["decimals"] = d
    except Exception:
        pass
    q = str(args.get("quality") or "").strip().lower()
    if q in ("standard", "hd", "ultra"):
        calc["quality"] = q
    params = _collect_params(args, rows)
    if params:
        calc["params"] = params

    title = str(args.get("title") or "图形计算器")
    spec = {"title": title, "items": [calc]}

    kinds: Dict[str, int] = {}
    for row in rows:
        src = row.get("expr") if isinstance(row, dict) else None
        if src is None:
            kinds["text"] = kinds.get("text", 0) + 1
            continue
        k = classify_row(str(src))["kind"]
        kinds[k] = kinds.get(k, 0) + 1
    desc = "、".join(f"{n} 个{_KIND_CN.get(k, k)}" for k, n in kinds.items())
    param_note = f"，{len(params)} 个参数滑块（学生可以拖）" if params else ""
    prec_note = ""
    if calc.get("decimals") is not None:
        prec_note = f"，坐标保留 {calc['decimals']} 位小数" if calc["decimals"] else "，坐标取整显示"
    return ToolResult(
        summary=(
            f"图形计算器已就绪：{len(rows)} 行公式（{desc}）{param_note}{prec_note}。"
            "**学生可以在上面直接改公式、加点、缩放，也能自己调精确度和画质**，"
            "所以不要在正文里复述这张图，直接讲结论。"
        ),
        ui=spec,
        data={"rows": len(rows), "kinds": kinds, "params": [p["name"] for p in params],
              "decimals": calc.get("decimals"), "quality": calc.get("quality"),
              "xMin": x_min, "xMax": x_max},
    )


@tool("graph_calculator", _GRAPH_TOOL_DESC, _GRAPH_TOOL_SCHEMA, produces=["ui"])
async def graph_calculator(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    return await _render_calculator(args)


@tool(
    "plot_function",
    "画函数图像（plot_function 现在和 graph_calculator 是同一台图形计算器："
    "画出来学生能改公式、能拖参数）。只画 y=f(x) 时用它最短，"
    "要画圆/椭圆/不等式/点/文字就用 graph_calculator。",
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
            "height": {"type": "integer", "description": "画布高度（像素），默认 430"},
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

    # 转成图形计算器的一行行公式：**这样学生拿到的是能改的计算器，而不是一张死图**
    expressions: List[Any] = []
    params: List[Dict[str, Any]] = []
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
        row: Dict[str, Any] = {"expr": expr}
        if item.get("label"):
            row["label"] = str(item["label"])
        if item.get("color"):
            row["color"] = str(item["color"])
        expressions.append(row)
        for p in item.get("params") or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            try:
                params.append({
                    "name": str(p["name"]),
                    "value": float(p.get("value", 1)),
                    "min": float(p.get("min", -5)),
                    "max": float(p.get("max", 5)),
                    "step": float(p.get("step", 0.1)),
                })
            except Exception:
                continue
    if problems:
        return ToolResult.fail("函数表达式有问题：\n- " + "\n- ".join(problems[:5]))

    return await _render_calculator({
        "expressions": expressions,
        "params": params,
        "xMin": args.get("xMin", -10),
        "xMax": args.get("xMax", 10),
        "title": args.get("title"),
        "height": args.get("height") or 430,
    })


@tool(
    "draw_board",
    "画板（板书作图）：**按步骤**画几何图形、受力分析、示意图，学生点「下一步」看着图一步步长出来。"
    "讲几何证明、物理受力、坐标变换、电路/光路时用它。"
    "**注意：只有一步的静态图不要用画板**（学生看到一排灰掉的「上一步/下一步」会以为坏了），"
    "静态图形用 graph_calculator；函数图像更是一律用 graph_calculator。",
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
