"""计算工具 —— 把「模型心算」这件事从根上取消。

老项目只能用一句提示词去要求「严禁心算，必须输出终端标记算一遍」，
但模型看不到计算结果，所以经常出现「正文写 3.5，工具输出 22.25」的错位。

现在数值计算是一个真正的工具：模型调用 → 拿到真实结果 → 再写正文。
并且**表达式和结果都由我们（而非模型）计算**，从机制上杜绝算错。
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any, Dict

from ..registry import ToolContext, ToolResult, tool

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_FUNCS: Dict[str, Any] = {
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum, "pow": pow,
    "sqrt": math.sqrt, "cbrt": getattr(math, "cbrt", lambda x: x ** (1 / 3)),
    "exp": math.exp, "log": math.log, "log2": math.log2, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "degrees": math.degrees, "radians": math.radians,
    "floor": math.floor, "ceil": math.ceil, "trunc": math.trunc,
    "factorial": math.factorial, "gcd": math.gcd, "hypot": math.hypot,
    "fabs": math.fabs, "fmod": math.fmod, "copysign": math.copysign,
}

_CONSTS: Dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}

# 模型常写的全角/习惯写法，先归一化再解析
_NORMALIZE = {
    "×": "*", "·": "*", "÷": "/", "−": "-", "–": "-", "—": "-",
    "（": "(", "）": ")", "，": ",", "^": "**", "²": "**2", "³": "**3", "π": "pi",
}


def _norm(expr: str) -> str:
    out = expr.strip()
    for src, dst in _NORMALIZE.items():
        out = out.replace(src, dst)
    # `**` 归一化后再处理 `^`→`**` 的重复（`^^` 之类）
    return out.replace("***", "**")


def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, bool)):
            return node.value
        raise ValueError(f"不支持的常量：{node.value!r}")
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持的运算符：{type(node.op).__name__}")
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 512 or abs(left) > 1e12):
            raise ValueError("幂运算结果过大，请分步计算")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError(f"不支持的一元运算符：{type(node.op).__name__}")
        return op(_eval(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError(f"未知的名称 `{node.id}`（只支持 pi / e / tau 这类常量）")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            allowed = ", ".join(sorted(_FUNCS)[:14])
            raise ValueError(f"不支持的函数调用（可用示例：{allowed} …）")
        args = [_eval(a) for a in node.args]
        return _FUNCS[node.func.id](*args)
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval(e) for e in node.elts]
    raise ValueError(f"表达式里含有不支持的语法：{type(node).__name__}")


def evaluate(expression: str) -> Dict[str, Any]:
    """计算一个数学表达式，返回 {ok, value, pretty, error}。"""
    expr = _norm(expression)
    if not expr:
        return {"ok": False, "error": "表达式为空"}
    if len(expr) > 500:
        return {"ok": False, "error": "表达式过长"}
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        return {"ok": False, "error": f"表达式语法错误：{exc.msg}"}
    try:
        value = _eval(tree)
    except ZeroDivisionError:
        return {"ok": False, "error": "除以零了（分母为 0）"}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"计算失败：{exc}"}

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            pretty = str(value)
        elif value == int(value) and abs(value) < 1e15:
            pretty = f"{int(value)}"
        else:
            pretty = f"{value:.10g}"
    elif isinstance(value, list):
        pretty = ", ".join(str(v) for v in value)
    else:
        pretty = str(value)
    return {"ok": True, "value": value, "pretty": pretty, "normalized": expr}


@tool(
    "calc",
    "计算数学表达式并返回精确结果。任何数值计算（代数运算、百分比、单位换算、统计、"
    "三角函数、开方、幂）都必须用这个工具，禁止心算。例如 calc(\"sqrt(2)*12.5\")、"
    "calc(\"(85+92+78)/3\")。",
    {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "要计算的数学表达式，用 Python 语法，如 (12.5*3+7)/2、sqrt(2)*sin(pi/4)、log(100,10)",
            }
        },
        "required": ["expression"],
    },
)
async def calc_tool(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    expr = str(args.get("expression") or "").strip()
    result = evaluate(expr)
    if not result.get("ok"):
        return ToolResult.fail(
            f"计算失败：{result['error']}。请修正表达式后重新调用 calc。",
            error=result.get("error"),
        )
    pretty = result["pretty"]
    normalized = result.get("normalized") or expr
    shown = normalized if normalized != expr else expr
    return ToolResult(
        summary=f"{shown} = {pretty}",
        data={"expression": expr, "normalized": normalized, "value": result["value"], "pretty": pretty},
    )
