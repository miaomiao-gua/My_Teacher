#!/usr/bin/env python
"""图形计算器自测：模型只写公式，学生还能自己改。

为什么值得测：
  1. `classify_row` 这套「一行公式是什么」的判断，前端（agent_ui.js）和这里
     各有一份。两边一旦不一致，就会出现「服务端说合法、前端画不出来」这种
     最糟糕的静默失败 —— 所以两边的用例表必须一样。
  2. 工具产出的 spec 必须能通过 render_ui 的结构校验（否则前端白屏）。
  3. `import os` 这类不是数学的东西**必须被拦住并说清楚**，不能放过去画一张空图。

跑法（agent_studio 目录下）：py -3.14 tests/test_calculator.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

from kernel.registry import ToolContext, get                       # noqa: E402
from kernel.session import STORE                                   # noqa: E402
from kernel.tools import load_builtin_tools                        # noqa: E402
from kernel.tools.mathviz import classify_row                      # noqa: E402
from kernel.tools.ui import validate_spec                          # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


# 这张表与 data/agent/_calc_tests.js 里的 cases 一一对应（两边行为必须一致）
CASES = [
    ("y=x^2-2x-3", "explicit"),
    ("x^2", "explicit"),
    ("sin(x)+1", "explicit"),
    ("2x+1", "explicit"),
    ("f(x)=x^2+1", "explicit"),
    ("x^2+y^2=16", "implicit"),
    ("x^2+y^2-16", "implicit"),
    ("(x-1)^2+(y-2)^2=9", "implicit"),
    ("x=3", "vertical"),
    ("y<=x+1", "ineqY"),
    ("y>2x-1", "ineqY"),
    ("x^2+y^2<=4", "ineqXY"),
    ("(2,3)", "point"),
    ("(-1.5, 2)", "point"),
    ('"圆心 O"', "text"),
    ('("切点", (1,2))', "text"),
    ("a=2", "constant"),
    ("", "empty"),
    ("import os", "error"),
    ("y=", "error"),
]


async def main() -> int:
    load_builtin_tools()
    ctx = ToolContext(session=STORE.get_or_create("calc_test", username="calc_test"), call_id="calc_test")

    print("===== 1) 一行公式被认成什么（前后端必须一致） =====")
    for src, want in CASES:
        info = classify_row(src)
        got = info["kind"]
        check(f"`{src}` → {want}", got == want, f"实际 {got}" + (f"：{info.get('error')}" if info.get("error") else ""))

    print("\n===== 2) 参数与文字 =====")
    check("y=a*x 里的 a 被认成参数", classify_row("y=a*x")["params"] == ["a"])
    check("x、y 不算参数", classify_row("x^2+y^2=16")["params"] == [])
    check("a=2 认出参数名", classify_row("a=2").get("name") == "a")
    at = classify_row('("切点", (1, 2.5))').get("at")
    check("文字的位置解析出来了", at == ["1", "2.5"], str(at))

    print("\n===== 3) graph_calculator（模型只写公式） =====")
    tool = get("graph_calculator")
    res = await tool.call(ctx, {
        "expressions": ["x^2+y^2=16", "y=x/2+1", "y<=x+1", "(2,3)", '"圆心 O"', "a=2", "y=a*x"],
        "xMin": -6, "xMax": 6, "title": "圆与直线",
    })
    check("调用成功", res.ok, res.summary[:120] if not res.ok else "")
    spec = (res.ui or {})
    calc = (spec.get("items") or [{}])[0]
    check("产出的是 calculator 组件", calc.get("type") == "calculator", str(calc.get("type")))
    check("公式原样带上（学生要能改）", len(calc.get("expressions") or []) == 7,
          str(len(calc.get("expressions") or [])))
    check("视野与标题带上", calc.get("xMin") == -6 and calc.get("xMax") == 6 and spec.get("title") == "圆与直线")
    names = [p["name"] for p in calc.get("params") or []]
    check("参数滑块自动补齐（a）", "a" in names, str(names))
    check("spec 能通过结构校验", validate_spec(spec) == [], str(validate_spec(spec)[:2]))
    check("摘要告诉模型别复述图形", "不要" in res.summary and "改公式" in res.summary, res.summary[:100])

    print("\n===== 4) 写错了必须拦住并说清楚 =====")
    bad = await tool.call(ctx, {"expressions": ["y=x^2", "import os"]})
    check("不是数学的公式被拒", not bad.ok)
    check("错误信息点到了具体第几行", "expressions[1]" in (bad.error or "") + bad.summary,
          (bad.error or bad.summary)[:120])
    empty = await tool.call(ctx, {"expressions": []})
    check("空公式被拒", not empty.ok)
    none = await tool.call(ctx, {})
    check("完全没给公式时给出去法提示", not none.ok and "expressions" in (none.error or none.summary))

    print("\n===== 5) 只写左边 / 文字参数也能画 =====")
    partial = await tool.call(ctx, {"expressions": ["x^2+y^2-25"], "xMin": -7, "xMax": 7})
    check("`x^2+y^2-25` 也当成圆画出来", partial.ok and
          partial.ui["items"][0]["expressions"][0]["expr"] == "x^2+y^2-25")
    with_text = await tool.call(ctx, {
        "expressions": ["y=x^2"],
        "texts": [{"text": "顶点在原点", "at": [0, 3]}],
    })
    rows = with_text.ui["items"][0]["expressions"]
    check("文字作为单独一行加进去了",
          any(isinstance(r, dict) and r.get("text") == "顶点在原点" for r in rows),
          str(rows)[:120])

    print("\n===== 6) 精确度与画质（模型可以指定，学生也能改） =====")
    prec = await tool.call(ctx, {
        "expressions": ["x^2+y^2=16"], "xMin": -6, "xMax": 6,
        "decimals": 2, "quality": "ultra",
    })
    pc = prec.ui["items"][0]
    check("decimals 传到了前端", pc.get("decimals") == 2, str(pc.get("decimals")))
    check("quality 传到了前端", pc.get("quality") == "ultra", str(pc.get("quality")))
    check("摘要里说明了精确度", "2 位小数" in prec.summary, prec.summary[:90])
    bad_prec = await tool.call(ctx, {"expressions": ["y=x"], "decimals": 99, "quality": "xxx"})
    bc = bad_prec.ui["items"][0]
    check("越界的 decimals / 非法 quality 被忽略",
          bc.get("decimals") is None and bc.get("quality") is None,
          f"{bc.get('decimals')} / {bc.get('quality')}")
    zero_prec = await tool.call(ctx, {"expressions": ["y=x"], "decimals": 0})
    check("decimals=0（取整）也能用", zero_prec.ui["items"][0].get("decimals") == 0)
    check("精确度的 spec 也能过校验", validate_spec(prec.ui) == [])

    print("\n===== 7) plot_function 也升级成可编辑的计算器 =====")
    plot = get("plot_function")
    pres = await plot.call(ctx, {"series": [
        {"expr": "a*x^2+b", "label": "抛物线",
         "params": [{"name": "a", "value": 1, "min": -3, "max": 3},
                    {"name": "b", "value": 0, "min": -5, "max": 5}]},
    ], "xMin": -5, "xMax": 5, "title": "二次函数"})
    check("plot_function 也能用", pres.ok, pres.summary[:100] if not pres.ok else "")
    pcalc = pres.ui["items"][0]
    check("plot_function 产出的也是 calculator（学生能改公式）",
          pcalc.get("type") == "calculator", str(pcalc.get("type")))
    check("series 的参数范围被保留（a/b 滑块）",
          sorted(p["name"] for p in pcalc.get("params") or []) == ["a", "b"],
          str([p["name"] for p in pcalc.get("params") or []]))
    check("plot_function 的 spec 也能过校验", validate_spec(pres.ui) == [])

    print()
    if _failures == 0:
        print("全部通过 ✓")
    else:
        print(f"{_failures} 项失败 ✗")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
