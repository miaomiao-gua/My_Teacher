"""三个交互小工具：可拖的几何画板 / 数据统计台 / 方程求解器。

为什么是这三件（而不是更多）
--------------------------
它们对应课堂上**学生最需要自己动手**的三类内容：
- 几何：拖一拖顶点，边长/角度/面积跟着变 → 自己发现勾股定理、内角和、面积关系
- 统计：改一个数，平均数动了、中位数没动 → 一眼看懂「为什么要有中位数」
- 代数：改系数，判别式、根、因式分解当场重算 → 把「解方程」变成可试验的东西

设计原则与 `graph_calculator` 一致：**模型只写数据，界面自己会算、能拖、能改**。
所以这里校验得严（名字引用、数值范围都查），但模型不需要算任何东西 ——
计算结果由前端当场算，服务端也顺手算一遍放进 summary，这样模型讲课时引用的
数字和界面上显示的**永远是同一个**（不会它说 3、界面显示 2.99）。
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Tuple

from ..registry import ToolContext, ToolResult, tool

# ---------------------------------------------------------------------------
# 共用的数学小工具（与 agent_ui.js 里那套**必须一致**，改一边就要改另一边）
# ---------------------------------------------------------------------------


def _gcd(a: int, b: int) -> int:
    a, b = abs(int(a)), abs(int(b))
    while b:
        a, b = b, a % b
    return a or 1


def simplify_sqrt(m: int) -> Tuple[int, int]:
    """√m = k√n（n 不含平方因子）"""
    if m < 0:
        return 0, 0
    k, n = 1, int(round(m))
    i = 2
    while i * i <= n:
        while n % (i * i) == 0:
            n //= i * i
            k *= i
        i += 1
    return k, n


def frac_text(num: int, den: int) -> str:
    """约分后的分数写法（分母 1 时只写分子）"""
    if not den:
        return str(num)
    sign = -1 if (num < 0) != (den < 0) else 1
    num, den = abs(int(num)), abs(int(den))
    g = _gcd(num, den)
    num, den = num // g, den // g
    if den == 1:
        return str(sign * num)
    return ("-" if sign < 0 else "") + f"{num}/{den}"


def _num(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except Exception:
        return default


# ---------------------------------------------------------------------------
# 一、可拖动的几何画板
# ---------------------------------------------------------------------------

_SAFE_NAME = re.compile(r"^[A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff'′]{0,9}$")
_MAX_POINTS = 14


@tool(
    "geometry_board",
    "可拖动的几何画板：给出点和连线，学生在图上**拖动顶点**，边长、角度、面积、周长"
    "当场跟着变。讲勾股定理、三角形内角和、面积、相似、四边形性质时用它 —— "
    "让学生自己拖出规律，比你讲十遍管用。"
    "（要「一步步板书作图」的效果请用 draw_board；只要静态图也可以用它。）",
    {
        "type": "object",
        "properties": {
            "points": {
                "type": "array",
                "description": ("顶点：[{\"name\":\"A\",\"at\":[0,0],\"fixed\":true},{\"name\":\"B\",\"at\":[4,0]},"
                                "{\"name\":\"C\",\"at\":[0,3]}]。name 是图上显示的名字，fixed=true 的点拖不动"
                                "（比如直角顶点或坐标系原点）。"),
                "items": {"type": "object"},
            },
            "segments": {
                "type": "array",
                "description": "连线：[[\"A\",\"B\"],[\"B\",\"C\"]]，可以只连一部分（比如只画两条直角边）",
                "items": {"type": "array"},
            },
            "polygons": {
                "type": "array",
                "description": "多边形（会填充并给出面积/周长/内角）：[[\"A\",\"B\",\"C\"]]。按顶点顺序写。",
                "items": {"type": "array"},
            },
            "circles": {
                "type": "array",
                "description": ("圆：[{\"center\":\"A\",\"through\":\"B\",\"label\":\"⊙A\"}] "
                                "或 [{\"center\":[0,0],\"r\":3}]。through 指定圆上一点，拖动它半径就变。"),
                "items": {"type": "object"},
            },
            "task": {"type": "string", "description": "给学生的一句话任务，如「拖动 C 点，看看 a²+b² 和 c² 什么时候相等」"},
            "showSquares": {"type": "boolean", "description": "额外显示每边的平方（勾股定理用），默认 false"},
            "height": {"type": "integer", "description": "画布高度（像素），默认 400"},
            "title": {"type": "string", "description": "标题"},
        },
        "required": ["points"],
    },
    produces=["ui"],
)
async def geometry_board(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    raw_points = args.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        return ToolResult.fail(
            "points 不能为空。至少给两个点，例如 "
            "[{\"name\":\"A\",\"at\":[0,0],\"fixed\":true},{\"name\":\"B\",\"at\":[4,0]}]"
        )
    if len(raw_points) > _MAX_POINTS:
        return ToolResult.fail(f"点太多了（{len(raw_points)} 个，上限 {_MAX_POINTS} 个），请精简。")

    problems: List[str] = []
    points: List[Dict[str, Any]] = []
    for i, p in enumerate(raw_points):
        if not isinstance(p, dict):
            problems.append(f"points[{i}] 必须是对象")
            continue
        name = str(p.get("name") or "").strip() or chr(65 + i)
        if not _SAFE_NAME.match(name):
            problems.append(f"points[{i}] 的名字 `{name}` 不合适（用字母或中文，1~10 个字符）")
            continue
        at = p.get("at")
        if not (isinstance(at, (list, tuple)) and len(at) == 2):
            problems.append(f"points[{i}]（{name}）缺少 at 坐标，形如 [3,4]")
            continue
        entry: Dict[str, Any] = {"name": name, "at": [_num(at[0]), _num(at[1])]}
        if p.get("fixed"):
            entry["fixed"] = True
        if p.get("color"):
            entry["color"] = str(p["color"])
        points.append(entry)
    if problems:
        return ToolResult.fail("几何画板的点有问题：\n- " + "\n- ".join(problems[:6]))
    if len(points) < 2:
        return ToolResult.fail("至少要两个有效的点。")

    names = {p["name"] for p in points}

    def clean_links(raw: Any, what: str, need: int) -> List[List[str]]:
        out: List[List[str]] = []
        if not isinstance(raw, list):
            return out
        for i, link in enumerate(raw):
            if not isinstance(link, (list, tuple)) or len(link) < need:
                problems.append(f"{what}[{i}] 要形如 " + "[\"A\",\"B\"]" if need == 2 else f"{what}[{i}] 至少三个点")
                continue
            nm = [str(x).strip() for x in link]
            missing = [x for x in nm if x not in names]
            if missing:
                problems.append(f"{what}[{i}] 用到了不存在的点：{', '.join(missing)}（已定义的只有 {', '.join(sorted(names))}）")
                continue
            if len(set(nm)) != len(nm):
                problems.append(f"{what}[{i}] 里有重复的点")
                continue
            out.append(nm)
        return out

    segments = clean_links(args.get("segments"), "segments", 2)
    polygons = clean_links(args.get("polygons"), "polygons", 3)

    circles: List[Dict[str, Any]] = []
    raw_circles = args.get("circles")
    if isinstance(raw_circles, list):
        for i, c in enumerate(raw_circles):
            if not isinstance(c, dict):
                problems.append(f"circles[{i}] 必须是对象")
                continue
            entry = {}
            if isinstance(c.get("center"), str):
                if c["center"] not in names:
                    problems.append(f"circles[{i}] 的圆心 `{c['center']}` 不是已定义的点")
                    continue
                entry["center"] = c["center"]
            elif isinstance(c.get("center"), (list, tuple)) and len(c["center"]) == 2:
                entry["center"] = [_num(c["center"][0]), _num(c["center"][1])]
            else:
                problems.append(f"circles[{i}] 的 center 要么是点名（\"A\"），要么是坐标 [0,0]")
                continue
            if c.get("through") is not None:
                if str(c["through"]) not in names:
                    problems.append(f"circles[{i}] 的 through `{c['through']}` 不是已定义的点")
                    continue
                entry["through"] = str(c["through"])
            elif c.get("r") is not None:
                r = _num(c["r"], 0)
                if r <= 0:
                    problems.append(f"circles[{i}] 的半径要大于 0")
                    continue
                entry["r"] = r
            else:
                problems.append(f"circles[{i}] 要有 through（圆上一点）或 r（半径）")
                continue
            if c.get("label"):
                entry["label"] = str(c["label"])
            circles.append(entry)

    if problems:
        return ToolResult.fail("几何画板有问题，改好再画：\n- " + "\n- ".join(problems[:6]))
    if not segments and not polygons and not circles:
        return ToolResult.fail("只给了点，没有任何连线/多边形/圆，学生看不出要观察什么。请补上 segments 或 polygons。")

    spec: Dict[str, Any] = {
        "type": "geometry",
        "points": points,
        "height": int(args.get("height") or 400),
    }
    if segments:
        spec["segments"] = segments
    if polygons:
        spec["polygons"] = polygons
    if circles:
        spec["circles"] = circles
    if args.get("title"):
        spec["title"] = str(args["title"])
    if args.get("task"):
        spec["task"] = str(args["task"])
    if args.get("showSquares"):
        spec["showSquares"] = True

    # 顺手算一遍关键的测量值放进 summary：模型讲课时引用的数字要跟界面一致
    facts: List[str] = []
    by = {p["name"]: p["at"] for p in points}
    for s in segments[:4]:
        a, b = by[s[0]], by[s[1]]
        facts.append(f"|{s[0]}{s[1]}| = {math.hypot(b[0] - a[0], b[1] - a[1]):.2f}")
    for ps in polygons[:2]:
        pts = [by[x] for x in ps]
        area = 0.0
        per = 0.0
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            area += x1 * y2 - x2 * y1
            per += math.hypot(x2 - x1, y2 - y1)
        facts.append(f"{''.join(ps)} 面积 = {abs(area) / 2:.2f}、周长 = {per:.2f}")

    return ToolResult(
        summary=(
            f"几何画板已就绪：{len(points)} 个点、{len(segments)} 条边、{len(polygons)} 个多边形"
            f"{'、' + str(len(circles)) + ' 个圆' if circles else ''}。"
            + ("当前测量：" + "；".join(facts) + "。" if facts else "")
            + "**学生可以直接拖顶点**，边长角度面积会跟着变，所以别说「你去拖拖看」之后就复述数值 —— "
            "直接讲「为什么会这样」。"
        ),
        ui={"title": str(args.get("title") or "几何画板"), "items": [spec]},
        data={"points": len(points), "segments": len(segments), "polygons": len(polygons),
              "measurements": facts},
    )


# ---------------------------------------------------------------------------
# 二、数据统计台
# ---------------------------------------------------------------------------


@tool(
    "data_lab",
    "数据统计台：给一串数，界面会算出平均数/中位数/众数/极差/方差/标准差并画图；"
    "**学生可以改任意一个数**，统计量和图形当场变。"
    "讲平均数与中位数的区别、方差的含义、数据分布时最有用（改一个极端值，平均数动、中位数不动）。",
    {
        "type": "object",
        "properties": {
            "values": {
                "type": "array",
                "description": "数据：[150,152,149,155] 或 [\"150\",\"152\"] 都行；最多 60 个",
                "items": {},
            },
            "labels": {"type": "array", "description": "每个数据的名字（可选）：[\"甲\",\"乙\",…]", "items": {"type": "string"}},
            "chart": {"type": "string", "description": "图形：bars（柱状，默认）/ line（折线）/ dot（点图，看分布最好）"},
            "title": {"type": "string", "description": "标题"},
            "height": {"type": "integer", "description": "画布高度（像素），默认 300"},
        },
        "required": ["values"],
    },
    produces=["ui"],
)
async def data_lab(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    raw = args.get("values")
    if isinstance(raw, str):
        raw = [x for x in re.split(r"[,，\s]+", raw) if x.strip()]
    if not isinstance(raw, list) or not raw:
        return ToolResult.fail("values 不能为空，例如 [150, 152, 149, 155]")
    if len(raw) > 60:
        return ToolResult.fail(f"数据太多了（{len(raw)} 个，上限 60 个）。")

    values: List[float] = []
    for i, v in enumerate(raw):
        try:
            f = float(v)
        except Exception:
            return ToolResult.fail(f"values[{i}] 不是数字：{v!r}")
        if not math.isfinite(f):
            return ToolResult.fail(f"values[{i}] 不是有限数")
        values.append(f)

    labels = []
    if isinstance(args.get("labels"), list):
        labels = [str(x) for x in args["labels"]][: len(values)]
    chart = str(args.get("chart") or "bars").strip().lower()
    if chart not in ("bars", "line", "dot"):
        chart = "bars"

    spec: Dict[str, Any] = {
        "type": "datalab",
        "values": values,
        "chart": chart,
        "height": int(args.get("height") or 300),
    }
    if labels:
        spec["labels"] = labels
    if args.get("title"):
        spec["title"] = str(args["title"])

    # 服务端也算一遍（summary 里给模型用；界面上的数字是前端算的，规则一致）
    s = sorted(values)
    n = len(s)
    total = sum(s)
    mean = total / n
    median = s[(n - 1) // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    counts: Dict[float, int] = {}
    for v in s:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.values())
    modes = sorted(k for k, c in counts.items() if c == best) if best > 1 else []
    var = sum((x - mean) ** 2 for x in s) / n

    def f2(x: float) -> str:
        return f"{x:.2f}".rstrip("0").rstrip(".")

    mode_text = "、".join(f2(m) for m in modes) if modes else "没有（每个数只出现一次）"
    return ToolResult(
        summary=(
            f"数据统计台已就绪：{n} 个数据。当前结果：平均数 {f2(mean)}、中位数 {f2(median)}、"
            f"众数 {mode_text}、极差 {f2(s[-1] - s[0])}、方差 {f2(var)}、标准差 {f2(math.sqrt(var))}。"
            "**学生可以改任意一个数看统计量怎么变**（改极端值最能说明平均数的弱点），"
            "引用数字时用上面这些，不要自己心算。"
        ),
        ui={"title": str(args.get("title") or "数据统计台"), "items": [spec]},
        data={"n": n, "mean": mean, "median": median, "mode": modes, "range": s[-1] - s[0],
              "variance": var, "std": math.sqrt(var), "sum": total, "min": s[0], "max": s[-1]},
    )


# ---------------------------------------------------------------------------
# 三、方程求解器
# ---------------------------------------------------------------------------


@tool(
    "solve_equation",
    "方程求解器：给系数，界面把**过程**一步步展开（判别式代入、求根公式、因式分解、顶点、韦达定理）"
    "并画出抛物线，**学生可以改系数**看结果怎么变。讲一元一次/一元二次方程、判别式、"
    "因式分解、抛物线与 x 轴交点时用它。结果会一并返回给你，引用请用返回的数字。",
    {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "description": "quadratic（一元二次，默认）或 linear（一元一次 ax+b=0）"},
            "a": {"type": "number", "description": "二次项系数（quadratic）或 x 的系数（linear）"},
            "b": {"type": "number", "description": "一次项系数（quadratic）或常数项（linear）"},
            "c": {"type": "number", "description": "常数项（quadratic 用）"},
            "title": {"type": "string", "description": "标题"},
            "height": {"type": "integer", "description": "图像高度（像素），默认 320"},
        },
        "required": ["a", "b"],
    },
    produces=["ui"],
)
async def solve_equation(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    kind = str(args.get("kind") or "quadratic").strip().lower()
    if kind not in ("linear", "quadratic"):
        kind = "quadratic"
    a = _num(args.get("a"), 0.0)
    b = _num(args.get("b"), 0.0)
    c = _num(args.get("c"), 0.0)

    facts: List[str] = []
    if kind == "linear":
        if a == 0:
            note = "a=0 且 b=0：任何 x 都成立（无穷多解）" if b == 0 else "a=0 且 b≠0：无解"
            facts.append(note)
        else:
            facts.append(f"x = {-b / a:.4f}（精确：{frac_text(int(round(-b)), int(round(a)))}）")
    elif a == 0:
        facts.append("a=0：这不是二次方程")
    else:
        D = b * b - 4 * a * c
        facts.append(f"Δ = {D:.4f}")
        if D < 0:
            facts.append("Δ<0，没有实数根")
        else:
            if D == 0:
                facts.append(f"两个相等实根 x = {-b / (2 * a):.4f}")
            else:
                k, nn = simplify_sqrt(int(round(D)))
                if nn == 1:
                    r1, r2 = (-b + k) / (2 * a), (-b - k) / (2 * a)
                    facts.append(f"两根 x₁ = {r1:.4f}，x₂ = {r2:.4f}"
                                 f"（精确：{frac_text(int(round(-b + k)), int(round(2 * a)))}、"
                                 f"{frac_text(int(round(-b - k)), int(round(2 * a)))}）")
                else:
                    facts.append(f"两根 = (-b ± {k}√{nn}) / (2a) ≈ "
                                 f"{(-b + math.sqrt(D)) / (2 * a):.4f}、{(-b - math.sqrt(D)) / (2 * a):.4f}")
        vx = -b / (2 * a)
        facts.append(f"顶点 ({vx:.4f}, {a * vx * vx + b * vx + c:.4f})，韦达：x₁+x₂={-b / a:.4f}、x₁x₂={c / a:.4f}")

    spec: Dict[str, Any] = {
        "type": "solver",
        "kind": kind,
        "a": a,
        "b": b,
        "height": int(args.get("height") or 320),
    }
    if kind == "quadratic":
        spec["c"] = c
    if args.get("title"):
        spec["title"] = str(args["title"])

    return ToolResult(
        summary=(
            f"方程求解器已就绪（{'一元二次' if kind == 'quadratic' else '一元一次'}）。当前结果："
            + "；".join(facts)
            + "。**学生可以改系数**，过程会重算；引用数字请用上面这些，不要自己心算。"
        ),
        ui={"title": str(args.get("title") or "方程求解器"), "items": [spec]},
        data={"kind": kind, "a": a, "b": b, "c": c, "facts": facts},
    )
