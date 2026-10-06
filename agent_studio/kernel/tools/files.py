"""文件与运行工具 —— 「生成能跑起来的东西」。

为什么这对教学特别重要
----------------------
讲编程课，学生要的是「能运行、能改、能看到输出」；讲化学/物理，学生要的是
「能拖、能点、能看到现象变化」。这两种东西都不是文字能给的，必须让 AI 真的**造出文件**。

安全与边界
----------
- 所有文件写在**会话自己的工作目录**里（`data/agent/workspaces/<会话>/`），
  路径经过清洗，禁止 `../` 穿越到项目源码
- 运行代码走 `agent/sandbox.py` 的护栏：超时强杀 + AST 拦危险调用 + 输出截断
- 生成的文件名/内容都会进上下文，所以要控制体积（单文件上限 400KB）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .. import events
from ..registry import ToolContext, ToolResult, tool
from ..sandbox import kind_of, list_files as _list_files, run_python as _run_python
from ..session import safe_relpath

MAX_FILE_BYTES = 400_000
MODEL_OUTPUT_CHARS = 3000


def _record(ctx: ToolContext, relpath: str, kind: str) -> Dict[str, Any]:
    path = ctx.session.workspace / relpath
    size = path.stat().st_size if path.exists() else 0
    artifact = {
        "path": relpath,
        "name": Path(relpath).name,
        "kind": kind,
        "url": ctx.session.file_url(relpath),
        "bytes": size,
    }
    # 同一路径重复写入时更新记录，避免产物面板里堆一串同名文件
    existing = [a for a in ctx.session.artifacts if a.get("path") == relpath]
    if existing:
        existing[0].update(artifact)
    else:
        ctx.session.artifacts.append(artifact)
    return artifact


@tool(
    "write_file",
    "在工作目录里生成一个文件（HTML 仿真、Python 脚本、CSV 数据、Markdown 讲义等）。"
    "写 HTML 界面时必须**单文件自包含**：CSS/JS 全部内联，不引用任何外部链接。",
    {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "相对工作目录的文件名，如 acid_base_sim.html、titration.py（不要用绝对路径）",
            },
            "content": {"type": "string", "description": "完整文件内容"},
            "kind": {
                "type": "string",
                "description": "文件类型提示：html / python / javascript / csv / json / markdown / text",
            },
        },
        "required": ["path", "content"],
    },
    produces=["artifact"],
)
async def write_file(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    relpath = safe_relpath(str(args.get("path") or ""))
    content = args.get("content")
    if not isinstance(content, str) or not content.strip():
        return ToolResult.fail("content 不能为空。请把完整文件内容放进 content 字段。")
    payload = content.encode("utf-8")
    if len(payload) > MAX_FILE_BYTES:
        return ToolResult.fail(
            f"文件太大（{len(payload)} 字节，上限 {MAX_FILE_BYTES}）。请精简内容，或拆成多个文件。"
        )
    target = ctx.session.workspace / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"写入失败：{exc}")

    kind = kind_of(relpath)
    artifact = _record(ctx, relpath, kind)
    lines = content.count("\n") + 1

    # HTML 写完自动自检：把问题直接塞回模型上下文，它就能自己修——
    # 这是「多调试几轮」的工程实现（由内核驱动轮次，不靠人盯着）。
    problems: list = []
    warnings: list = []
    if kind == "html":
        from .web import dry_run, lint_html

        problems = await lint_html(content, ctx.session.workspace)
        if not problems:
            run_info = await dry_run(ctx.session.workspace / relpath, ctx.session.workspace)
            warnings = run_info.get("errors") or []

    if problems or warnings:
        listing = "\n".join(f"- {p}" for p in problems + warnings)
        return ToolResult(
            summary=(
                f"已写入 `{relpath}`（{len(payload)} 字节，{lines} 行），但**自检发现问题**，"
                f"必须先修好再展示给学生：\n{listing}\n"
                f"请用 write_file 重写 `{relpath}`（提交完整内容），然后调用 check_html 确认修好了。"
            ),
            artifacts=[artifact],
            data={**artifact, "problems": problems, "warnings": warnings},
            error="; ".join((problems + warnings)[:3]),
        )

    if kind == "html":
        # 自检通过 → **自动上台**。
        # 为什么不让模型自己调 show_artifact：实测它经常忘（正文里说「看看这个界面」就以为完事了），
        # 结果学生什么也看不到。这类「必须发生的事」不能靠模型记得，要由内核保证。
        # 自检没过的不会上台（坏界面留在工作目录里等模型修）。
        await ctx.push(
            events.ui(
                {
                    "title": Path(relpath).name,
                    "items": [
                        {
                            "type": "frame",
                            "src": ctx.session.file_url(relpath),
                            "height": 560,
                            "title": Path(relpath).name,
                        }
                    ],
                }
            )
        )
        hint = (
            "自检通过，**界面已经自动展示给学生了**（在沙箱里真跑起来了），不要在正文里复述界面内容；"
            "只有需要换尺寸/重新展示时才调用 `show_artifact`。"
        )
    elif kind == "python":
        hint = f"可以用 `run_python` 运行 `{relpath}` 里的代码验证它能跑通。"
    else:
        hint = "如需展示给学生，调用 `show_artifact`。"
    return ToolResult(
        summary=f"已写入 `{relpath}`（{len(payload)} 字节，{lines} 行）。{hint}",
        artifacts=[artifact],
        data=artifact,
    )


@tool(
    "run_python",
    "运行 Python 代码（用来验证生成的代码能跑通、做数值实验、演示输出）。"
    "返回真实的 stdout/stderr。注意：教学沙箱禁止进程创建、网络访问、删文件。",
    {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要运行的完整 Python 代码"},
            "filename": {
                "type": "string",
                "description": "保存成哪个文件（可选），如 titration.py；默认 _run.py",
            },
        },
        "required": ["code"],
    },
    produces=["artifact"],
)
async def run_python(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        return ToolResult.fail("code 不能为空。")
    filename = safe_relpath(str(args.get("filename") or "_run.py"))
    if not filename.endswith(".py"):
        filename += ".py"
    if filename.startswith("_"):
        filename = "_" + filename.lstrip("_")
    timeout = float(ctx.session.meta.get("python_timeout") or 20)
    result = await _run_python(code, ctx.session.workspace, timeout=timeout, filename=filename)

    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    ok = bool(result.get("ok"))
    parts = []
    if ok:
        parts.append("运行成功。")
    elif result.get("timed_out"):
        parts.append(f"运行超时（{timeout:.0f} 秒）已被强制结束。")
    else:
        parts.append(f"运行失败（退出码 {result.get('exit_code')}）。")
    if stdout:
        parts.append("stdout:\n" + stdout[:MODEL_OUTPUT_CHARS])
    if stderr:
        parts.append("stderr:\n" + stderr[:MODEL_OUTPUT_CHARS])
    if not stdout and not stderr:
        parts.append("（没有任何输出）")
    if not ok:
        parts.append("请根据上面的报错修改代码后重试一次；如果仍失败就用文字向学生解释。")

    artifacts = []
    script_path = ctx.session.workspace / filename
    if script_path.exists() and not filename.startswith("_"):
        artifacts.append(_record(ctx, filename, "python"))

    return ToolResult(
        ok=ok,
        summary="\n".join(parts),
        data={"stdout": stdout, "stderr": stderr, "exit_code": result.get("exit_code"), "ms": result.get("ms")},
        artifacts=artifacts,
    )


@tool(
    "list_files",
    "列出工作目录里已经生成的文件（在不确定文件名时使用）。",
    {"type": "object", "properties": {}, "required": []},
)
async def list_files_tool(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    files = _list_files(ctx.session.workspace)
    if not files:
        return ToolResult(summary="工作目录还是空的，还没有生成任何文件。", data={"files": []})
    listing = "\n".join(f"- {f['path']}（{f['kind']}, {f['bytes']} 字节）" for f in files)
    return ToolResult(summary="工作目录里的文件：\n" + listing, data={"files": files})


@tool(
    "read_file",
    "读取工作目录里某个文件的内容（改代码前先看一遍，或确认生成结果）。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "工作目录内的相对路径"},
            "max_chars": {"type": "integer", "description": "最多读取多少字符，默认 4000"},
        },
        "required": ["path"],
    },
)
async def read_file(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    relpath = safe_relpath(str(args.get("path") or ""))
    target = ctx.session.workspace / relpath
    if not target.exists() or not target.is_file():
        files = _list_files(ctx.session.workspace)
        return ToolResult.fail(
            f"没有 `{relpath}`。当前文件：{', '.join(f['path'] for f in files) or '（空）'}"
        )
    limit = int(args.get("max_chars") or 4000)
    limit = max(200, min(20000, limit))
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"读取失败：{exc}")
    truncated = len(text) > limit
    body = text[:limit]
    return ToolResult(
        summary=f"`{relpath}` 的内容" + ("（已截断）" if truncated else "") + "：\n" + body,
        data={"path": relpath, "chars": len(text), "truncated": truncated},
    )
