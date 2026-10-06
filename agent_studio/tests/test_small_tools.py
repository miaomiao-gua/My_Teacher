#!/usr/bin/env python
"""三个交互小工具的自测：几何画板 / 数据统计台 / 方程求解器。

为什么值得测：
  1. 这三个工具的**计算在前后端各有一份**（服务端算一遍放进 summary 给模型讲，
     前端算一遍显示给学看）。两边算得不一样就会出现「老师说 3、界面显示 2.99」，
     这是学生一眼就能看出来、而且会让整堂课失去可信度的错误。
     所以这里的用例表和 data/agent/_tools_tests.js 是**一一对应**的。
  2. 模型写错引用（比如连线里用了没定义的点名）必须**当场拦住并说清楚**，
     否则前端只能画出一张缺胳膊少腿的图。

跑法（agent_studio 目录下）：py -3.14 tests/test_small_tools.py
"""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

from kernel.registry import ToolContext, get                       # noqa: E402
from kernel.session import STORE                                   # noqa: E402
from kernel.tools import load_builtin_tools                        # noqa: E402
from kernel.tools.interactive import frac_text, simplify_sqrt       # noqa: E402
from kernel.tools.ui import validate_spec                          # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


async def main() -> int:
    load_builtin_tools()
    ctx = ToolContext(session=STORE.get_or_create("tools3_test", username="t3"), call_id="c1")

    print("===== 1) 共用的数学小函数（前后端必须一致）=====")
    check("√8 = 2√2", simplify_sqrt(8) == (2, 2), str(simplify_sqrt(8)))
    check("√9 = 3（完全平方）", simplify_sqrt(9) == (3, 1), str(simplify_sqrt(9)))
    check("√12 = 2√3", simplify_sqrt(12) == (2, 3), str(simplify_sqrt(12)))
    check("分数约分 6/4 = 3/2", frac_text(6, 4) == "3/2", frac_text(6, 4))
    check("分母 1 只写分子", frac_text(-5, 1) == "-5", frac_text(-5, 1))
    check("负号在分子上", frac_text(-3, 6) == "-1/2", frac_text(-3, 6))

    print("\n===== 2) 几何画板 =====")
    geo = get("geometry_board")
    g = await geo.call(ctx, {
        "title": "勾股定理", "showSquares": True,
        "points": [{"name": "A", "at": [0, 0], "fixed": True},
                   {"name": "B", "at": [4, 0]}, {"name": "C", "at": [0, 3]}],
        "segments": [["A", "B"], ["A", "C"], ["B", "C"]],
        "polygons": [["A", "B", "C"]],
        "task": "拖动顶点，看看 a²+b² 与 c²",
    })
    check("调用成功", g.ok, g.summary[:100] if not g.ok else "")
    spec = g.ui["items"][0]
    check("产出 geometry 组件", spec.get("type") == "geometry", str(spec.get("type")))
    check("点/线/面都带上了",
          len(spec["points"]) == 3 and len(spec["segments"]) == 3 and len(spec["polygons"]) == 1)
    check("勾股数被算出来（3-4-5）", "|BC| = 5.00" in g.summary, g.summary[:130])
    check("面积也算对了（6.00）", "面积 = 6.00" in g.summary, g.summary[:170])
    check("spec 能过结构校验", validate_spec(g.ui) == [])

    bad_ref = await geo.call(ctx, {
        "points": [{"name": "A", "at": [0, 0]}, {"name": "B", "at": [1, 0]}],
        "segments": [["A", "Z"]],
    })
    check("连线引用不存在的点 → 拦住并点名",
          not bad_ref.ok and "Z" in (bad_ref.error or "") + bad_ref.summary,
          (bad_ref.error or bad_ref.summary)[:120])
    one_point = await geo.call(ctx, {"points": [{"name": "A", "at": [0, 0]}]})
    check("只给一个点 → 拦住", not one_point.ok)
    no_links = await geo.call(ctx, {"points": [{"name": "A", "at": [0, 0]}, {"name": "B", "at": [1, 1]}]})
    check("只有点没有连线 → 拦住并说明原因", not no_links.ok and "segments" in (no_links.error or ""),
          (no_links.error or "")[:90])
    frac_pt = await geo.call(ctx, {"points": [{"name": "A", "at": "x"}, {"name": "B", "at": [1, 0]}],
                                   "segments": [["A", "B"]]})
    check("坐标不是数字 → 拦住", not frac_pt.ok, (frac_pt.error or "")[:80])

    print("\n===== 3) 数据统计台 =====")
    dl = get("data_lab")
    d = await dl.call(ctx, {"values": [1, 2, 2, 3, 9], "title": "全班身高"})
    check("调用成功", d.ok, d.summary[:100] if not d.ok else "")
    check("平均数 3.4", "平均数 3.4" in d.summary, d.summary[:120])
    check("中位数 2", "中位数 2" in d.summary, d.summary[:120])
    check("众数 2", "众数 2" in d.summary, d.summary[:120])
    check("极差 8", "极差 8" in d.summary, d.summary[:140])
    check("方差 8.24", "方差 8.24" in d.summary, d.summary[:160])
    check("返回的 data 里数字是同一份",
          abs(d.data["mean"] - 3.4) < 1e-9 and abs(d.data["variance"] - 8.24) < 1e-9 and d.data["mode"] == [2])
    check("spec 能过结构校验", validate_spec(d.ui) == [])
    check("数据里的 chart 类型合法", (d.ui["items"][0].get("chart") or "bars") in ("bars", "line", "dot"))
    bad = await dl.call(ctx, {"values": [1, "abc", 3]})
    check("非数字被拦住", not bad.ok, (bad.error or "")[:80])
    empty = await dl.call(ctx, {"values": []})
    check("空数据被拦住", not empty.ok)
    comma = await dl.call(ctx, {"values": "1，2，3"})
    check("逗号分隔的字符串也能收", comma.ok and comma.data["n"] == 3, str(comma.data.get("n")))
    allsame = await dl.call(ctx, {"values": [5, 5, 5]})
    check("数据全相同时众数 = 5", allsame.data["mode"] == [5], str(allsame.data["mode"]))
    distinct = await dl.call(ctx, {"values": [1, 2, 3]})
    check("每个数只出现一次 → 没有众数", distinct.data["mode"] == [], str(distinct.data["mode"]))

    print("\n===== 4) 方程求解器（结果要与前端一致）=====")
    sv = get("solve_equation")
    s1 = await sv.call(ctx, {"a": 1, "b": -5, "c": 6, "title": "解方程"})
    check("x²−5x+6=0 调用成功", s1.ok)
    check("Δ = 1", "Δ = 1" in s1.summary, s1.summary[:110])
    check("两根是 3 和 2", "3.0000" in s1.summary and "2.0000" in s1.summary, s1.summary[:150])
    check("精确根写成整数 3、2", "精确：3、2" in s1.summary, s1.summary[:150])
    check("顶点 (2.5, -0.25)", "(2.5" in s1.summary and "-0.25" in s1.summary, s1.summary[:220])
    check("韦达定理也对", "x₁+x₂=5" in s1.summary, s1.summary[:240])
    check("spec 能过结构校验", validate_spec(s1.ui) == [])
    s2 = await sv.call(ctx, {"a": 2, "b": -5, "c": 3})
    check("2x²−5x+3=0 的精确根是 3/2 与 1", "3/2、1" in s2.summary, s2.summary[:150])
    s3 = await sv.call(ctx, {"a": 1, "b": 0, "c": -2})
    check("x²−2=0 写成 ±√2 形式", "√2" in s3.summary, s3.summary[:160])
    s4 = await sv.call(ctx, {"a": 1, "b": 0, "c": 1})
    check("x²+1=0 说明没有实数根", "没有实数根" in s4.summary, s4.summary[:130])
    s5 = await sv.call(ctx, {"kind": "linear", "a": 2, "b": -6})
    check("一元一次 2x−6=0 → x=3", s5.ok and "x = 3" in s5.summary, s5.summary[:90])
    s6 = await sv.call(ctx, {"kind": "linear", "a": 0, "b": 5})
    check("一次方程 a=0 时说明无解", "无解" in s6.summary, s6.summary[:90])

    print("\n===== 5) 三件能不能拼成一个完整界面 =====")
    spec = {
        "title": "勾股定理 · 一节课",
        "items": [
            {"type": "callout", "tone": "info", "title": "先看事实", "content": "a²+b²=c²"},
            {"type": "tabs", "tabs": [
                {"label": "动手", "items": [g.ui["items"][0]]},
                {"label": "数据", "items": [d.ui["items"][0],
                                            {"type": "table", "columns": ["a", "b", "c"],
                                             "rows": [["3", "4", "5"], ["6", "8", "10"]]}]},
                {"label": "算一算", "items": [s1.ui["items"][0],
                                              {"type": "calculator", "expressions": ["x^2+y^2=25"],
                                               "xMin": -8, "xMax": 8}]},
            ]},
            {"type": "quiz", "question": "直角边 6 和 8，斜边？",
             "options": [{"label": "10", "correct": True, "feedback": "36+64=100"},
                         {"label": "14", "feedback": "那是相加了"}], "explanation": "a²+b²=c²"},
        ],
    }
    errs = validate_spec(spec)
    check("整页 spec 通过结构校验", errs == [], str(errs[:3]))
    types = []
    stack = [spec]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("type"):
                types.append(node["type"])
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    for t in ("geometry", "datalab", "solver", "calculator", "quiz", "tabs", "table", "callout"):
        check(f"整页里有 {t}", t in types, ",".join(sorted(set(types)))[:120])

    print("\n===== 6) 结构错误提示要说清「你发的是什么」 =====")
    from kernel.tools.ui import validate_spec as vs
    err = vs({"tabs": [{"label": "x", "items": []}]})
    check("顶层直接写组件时，提示里点出实际收到的键名",
          err and "tabs" in err[0], (err[0] if err else "")[:110])
    check("空对象也有可读提示", bool(vs({})) and "空对象" in vs({})[0], str(vs({}))[:80])

    print()
    if _failures == 0:
        print("全部通过 ✓")
    else:
        print(f"{_failures} 项失败 ✗")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
