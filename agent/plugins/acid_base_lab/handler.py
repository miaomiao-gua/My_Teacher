"""酸碱盐虚拟实验室插件 —— 工具实现。

一个插件 = 一个目录（清单 + 处理函数 + 前端界面），见 `agent/plugins.py` 的说明。
这个处理函数很薄：校验参数 → 拼界面 URL → 返回 UI 规格 + 一句给模型看的说明。

**为什么参数要走 URL**：界面在沙箱 iframe 里跑（拿不到宿主的任何东西），
所以初始条件只能通过地址栏参数传进去，界面自己读 `location.search`。
"""

from __future__ import annotations

import urllib.parse
from typing import Any, Dict

try:
    from agent.registry import ToolContext, ToolResult
except ImportError:  # 插件是被按路径加载的，兜底把项目根加进 sys.path
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from agent.registry import ToolContext, ToolResult  # type: ignore

PLUGIN_NAME = "acid_base_lab"
UI_ENTRY = "ui/index.html"

VALID_INDICATORS = {
    "phenolphthalein": "酚酞",
    "litmus": "石蕊",
    "methyl_orange": "甲基橙",
    "methyl_red": "甲基红",
    "none": "不加指示剂",
}

# 给学生的一句话实验任务（不同初始指示剂，观察重点不同）
TASK_HINTS = {
    "phenolphthalein": "先加碱，看溶液什么时候变红；再加酸把它变回无色，体会酚酞的变色范围（pH 8.2~10）。",
    "litmus": "对比酸性和碱性下石蕊的颜色（红 / 紫 / 蓝），找出它变色最明显的 pH 区间。",
    "methyl_orange": "观察甲基橙在酸性（红）到碱性（黄）之间的过渡色，注意它在中性附近就变黄了。",
    "methyl_red": "注意甲基红的变色范围（pH 4.4~6.2）比甲基橙更靠酸性一侧。",
    "none": "先不加指示剂，只看 pH 数值与液面变化，感受中和反应的等当点在哪里。",
}


async def run(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    indicator = str(args.get("indicator") or "phenolphthalein").strip()
    if indicator not in VALID_INDICATORS:
        indicator = "phenolphthalein"

    try:
        conc = float(args.get("conc") or 0.1)
    except (TypeError, ValueError):
        conc = 0.1
    conc = min(max(conc, 0.001), 2.0)          # 钳制到合理范围，避免界面出现荒谬数值

    focus = str(args.get("focus") or "").strip()
    task = focus or TASK_HINTS.get(indicator, "")

    query = urllib.parse.urlencode(
        {"indicator": indicator, "conc": f"{conc:g}", "task": task},
        quote_via=urllib.parse.quote,
    )
    # 插件资源由 server 挂载：/agent/plugins/<plugin>/<path>
    src = f"/agent/plugins/{PLUGIN_NAME}/{UI_ENTRY}?{query}"

    spec = {
        "title": "酸碱盐虚拟实验室",
        "items": [
            {
                "type": "callout",
                "tone": "info",
                "title": "本实验要观察什么",
                "content": task or "动手加酸加碱，观察 pH 与颜色的变化。",
            },
            # 高度给足：实验台是主舞台，窗口太小没法做实验
            # （渲染器里另有「放大」按钮可铺满整屏，学生想全屏做实验随时能放大）
            {"type": "frame", "src": src, "height": 720, "title": "酸碱盐虚拟实验室"},
        ],
    }

    return ToolResult(
        summary=(
            f"已打开「酸碱盐虚拟实验室」（初始指示剂：{VALID_INDICATORS[indicator]}，"
            f"浓度 {conc:g} mol/L）。学生现在可以自己选指示剂、逐滴加酸加碱、看 pH 与颜色变化、"
            "记录数据。**不要复述界面内容**，直接引导学生动手："
            f"{task} 学生操作完再问他观察到了什么。"
        ),
        ui=spec,
        data={"plugin": PLUGIN_NAME, "indicator": indicator, "conc": conc, "focus": focus},
        stage={"emotion": "joy"},
    )
