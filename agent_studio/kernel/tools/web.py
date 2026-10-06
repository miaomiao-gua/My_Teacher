"""网页产物自检工具 —— 让 AI 生成的界面「写完就知道能不能跑」。

为什么必须有这个工具
--------------------
实测第一版模型生成的酸碱盐仿真，HTML 里有一行
`document.getElementById('x') = overlay;`——语法错误，整个 `<script>` 解析失败，
页面上一个按钮都不工作。但模型**看不见**这件事：它写完文件就以为成功了，
学生点开却是一片死界面。

这类错误有两个特征：**机器可判定**、**模型自己看不出来**。
所以正确的做法不是写更多提示词求它「仔细检查」，而是给它一个检查工具，
并且让 `write_file` 写完 HTML 后**自动**跑一遍，把问题直接塞回模型上下文——
它就能自己修（这就是「多调试几轮」的工程实现：轮次由内核驱动，不靠人盯着）。

检查手段（按可靠性排序）
------------------------
1. `node --check`：真语法检查，能精确报出错误行与原因（本机有 Node）
2. 括号配平：没有 Node 时的兜底
3. 已知坑模式：给函数调用结果赋值、无效十六进制颜色、外部资源引用（沙箱里取不到）
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List

from ..paths import resource_root
from ..registry import ToolContext, ToolResult, tool
from ..sandbox import run_command
from ..session import safe_relpath

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
_HEX_RE = re.compile(r"#([0-9a-fA-F]{1,8})\b")
_EXTERNAL_RE = re.compile(r"""(?:src|href)\s*=\s*["'](https?:)?//""", re.I)


def _line_of(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def _balance(code: str) -> List[str]:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: List[str] = []
    in_str = None
    escaped = False
    for ch in code:
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in "\"'`":
            in_str = ch
            continue
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                return [f"括号不配平：多出一个 `{ch}`"]
            stack.pop()
    if stack:
        return [f"括号不配平：缺少闭合的 `{stack[-1]}`"]
    return []


async def _node_check(code: str, workdir: Path) -> Dict[str, Any]:
    """用 node --check 做真语法检查。返回 {available, ok, line, message}。"""
    node = shutil.which("node")
    if not node:
        return {"available": False}
    workdir.mkdir(parents=True, exist_ok=True)
    tmp = workdir / f"_lint_{int(time.time() * 1000) % 100000}.js"
    try:
        tmp.write_text(code, encoding="utf-8")
        result = await run_command([node, "--check", tmp.name], workdir, timeout=15)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    if result.get("ok"):
        return {"available": True, "ok": True}
    stderr = (result.get("stderr") or "").strip()
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    # node 的 stderr 最后一行常只是版本号（Node.js v24.x），必须挑出真正的错误描述，
    # 否则回灌给模型的是「第 320 行：Node.js v24.19.0」这种没用的信息。
    message = "语法检查失败"
    for ln in lines:
        if re.match(r"^(SyntaxError|ReferenceError|TypeError|RangeError|Error)\b", ln):
            message = ln
            break
    else:
        if lines:
            message = lines[0][:200]
    line_no = None
    for ln in lines[:4]:
        m = re.search(r":(\d+)\s*$", ln)
        if m:
            line_no = int(m.group(1))
            break
    return {"available": True, "ok": False, "line": line_no, "message": message}


def _pattern_checks(code: str, html: str) -> List[str]:
    problems: List[str] = []
    # 给「函数调用结果」赋值：`document.getElementById('x') = y`
    for m in re.finditer(r"getElementById\s*\([^)]*\)\s*=[^=]", code):
        problems.append(f"把值赋给了 getElementById(...) 的结果（第 {_line_of(html, html.find(m.group(0)))} 行附近）"
                        "——应该先取到元素再改它的属性")
    # 无效十六进制颜色（合法长度：3/4/6/8）
    for m in _HEX_RE.finditer(code):
        if len(m.group(1)) not in (3, 4, 6, 8):
            problems.append(f"无效的颜色值 `#{m.group(1)}`（十六进制颜色必须是 3/4/6/8 位）")
    return problems[:6]


FATAL_MARKERS = ("JS 语法错误", "把值赋给了", "括号不配平")


def is_fatal(problem: str) -> bool:
    """这个问题是不是「展示出去一定是坏的」——是的话就该拦住不让学生看到。

    只拦确定性的坏：语法错误、给函数调用结果赋值、括号不配平。
    颜色写错位数、引用外网这类问题照样提示，但不拦（可能只是不够完美）。
    """
    return any(marker in problem for marker in FATAL_MARKERS)


async def lint_html(text: str, workdir: Path) -> List[str]:
    """检查一段 HTML（含内联 JS），返回问题列表（空 = 没查出问题）。"""
    problems: List[str] = []

    for m in _EXTERNAL_RE.finditer(text):
        problems.append(f"引用了外部资源（第 {_line_of(text, m.start())} 行）——沙箱里的页面取不到外网，"
                        "请把 CSS/JS 全部内联")
        break  # 报一次就够，避免刷屏

    blocks = list(_SCRIPT_RE.finditer(text))
    if not blocks:
        return problems

    for idx, m in enumerate(blocks):
        attrs, code = m.group(1), m.group(2)
        if "src=" in attrs.lower():
            continue
        offset = _line_of(text, m.start(2)) - 1
        node = await _node_check(code, workdir)
        if node.get("available"):
            if not node.get("ok"):
                line = node.get("line")
                where = f"第 {offset + line} 行" if line else "（行号未识别）"
                problems.append(f"JS 语法错误 @ {where}：{node.get('message')}")
                continue
        else:
            for p in _balance(code):
                problems.append(f"JS 语法问题（第 {offset + 1} 行起的脚本）：{p}")
        for p in _pattern_checks(code, text):
            problems.append(p)

    # 去掉重复项
    seen = set()
    uniq = []
    for p in problems:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq[:10]


_BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

DOM_RUNNER = resource_root() / "kernel" / "tools" / "dom_runner.js"


def _browser_path() -> str | None:
    for cand in _BROWSER_CANDIDATES:
        if Path(cand).exists():
            return cand
    for name in ("msedge", "chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


async def dry_run(html_path: Path, workdir: Path, timeout: float = 30) -> Dict[str, Any]:
    """用 Node + 桩 DOM 把页面跑一遍（加载 → 延迟回调 → 挨个触发交互回调）。

    **这是尽力而为的检查**：它能抓「初始化就抛异常」「某个按钮回调抛异常」，
    但受限于桩环境，走不到依赖具体数据的分支。所以它只出**警告**，不拦展示；
    真正拦展示的是 `lint_html` 的静态检查（实测它抓到了第一版仿真第 181 行的致命写法）。

    为什么不用 headless 浏览器：实测受限环境下浏览器拿不到 DOM 输出（进程被拦），
    依赖它就等于检查时灵时不灵——宁可要一个弱的但永远可用的检查。
    """
    node = shutil.which("node")
    if not node or not DOM_RUNNER.exists():
        return {"available": False, "errors": []}
    result = await run_command([node, str(DOM_RUNNER), str(html_path.resolve())], workdir, timeout=timeout)
    out = (result.get("stdout") or "").strip()
    marker = "AUCHECK|"
    if marker not in out:
        return {"available": False, "errors": []}
    raw = out.split(marker, 1)[1].strip()
    if raw == "OK" or not raw:
        return {"available": True, "ok": True, "errors": []}
    return {"available": True, "ok": False, "errors": [e.strip() for e in raw.split(";;") if e.strip()]}


@tool(
    "check_html",
    "检查生成的网页能不能正常跑（JS 语法、常见坑、外部依赖）。写 HTML 界面后、"
    "展示给学生之前调用它：它能报出「第几行什么错」，据此修改后重新检查。",
    {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "工作目录内的 HTML 文件名"}},
        "required": ["path"],
    },
)
async def check_html(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    relpath = safe_relpath(str(args.get("path") or ""))
    target = ctx.session.workspace / relpath
    if not target.exists():
        from ..sandbox import list_files

        files = [f["path"] for f in list_files(ctx.session.workspace)]
        return ToolResult.fail(f"没有 `{relpath}`。当前文件：{', '.join(files) or '（空）'}")
    text = target.read_text(encoding="utf-8", errors="replace")

    # 两类检查分工明确：
    #   lint_html  → 静态（语法 + 高发错误写法），**能拦展示**
    #   dry_run    → 真跑一遍（加载/延迟/交互回调），受桩环境限制，只出**警告**
    problems = await lint_html(text, ctx.session.workspace)
    run_info = await dry_run(target, ctx.session.workspace)
    warnings = run_info.get("errors") or []

    if not problems:
        notes = ""
        if run_info.get("available") and not warnings:
            notes = "，并用 Node 跑了一遍（加载、延迟回调、按钮回调都没报错）"
        elif warnings:
            notes = "，但跑起来发现这些运行时问题（必须修）：\n" + "\n".join(f"- {w}" for w in warnings)
        verdict = "自检通过" if not warnings else "静态检查通过，但运行时报错"
        return ToolResult(
            ok=not warnings,
            summary=(
                f"`{relpath}` {verdict}：JS 语法没问题、没有外部依赖{notes}。"
                + ("" if warnings else "可以调用 show_artifact 展示了。")
                + (f"请修好上面这些问题，用 write_file 重写 `{relpath}` 后再调用 check_html。" if warnings else "")
            ),
            data={"path": relpath, "problems": [], "warnings": warnings, "dry_run": run_info},
        )

    listing = "\n".join(f"- {p}" for p in problems)
    extra = ""
    if warnings:
        extra = "\n跑起来还发现：\n" + "\n".join(f"- {w}" for w in warnings)
    return ToolResult(
        ok=False,
        summary=(
            f"`{relpath}` 自检发现 {len(problems)} 个问题，**必须修好再展示给学生**：\n{listing}{extra}\n"
            "请用 write_file 重写这个文件（改完整内容，不要只贴片段），然后再调用 check_html 确认。"
        ),
        data={"path": relpath, "problems": problems, "warnings": warnings, "dry_run": run_info},
        error="; ".join(problems[:3]),
    )
