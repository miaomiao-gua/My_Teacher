"""沙箱 —— 让 AI 生成的代码能真的跑起来，同时别把机器搞坏。

先说清楚边界（诚实比安全声明更重要）
------------------------------------
Windows 上没有容器，**这里不是真正的安全沙箱**，它是一组「防手滑」的护栏：
- 独立工作目录：代码在会话自己的目录里跑，文件名由模型给，不碰项目源码
- 超时强杀：默认 20 秒，超时按进程树杀掉（Windows 用 taskkill /T /F）
- AST 静态检查：拦掉进程创建、网络、文件系统删除、动态执行等明显危险调用
- 输出截断：stdout/stderr 各留 8000 字，避免一个死循环把内存打满

要真正隔离需要容器或独立用户权限，那是部署阶段的事。当前阶段（本机单用户教学）
这套护栏足够，且**报错信息会回灌给模型**，让它自己改代码重试。
"""

from __future__ import annotations

import ast
import asyncio
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_OUTPUT = 8000

# 禁止导入的模块：进程/网络/系统级能力（教学场景用不到，出事代价却很大）
BANNED_MODULES = {
    "subprocess", "ctypes", "socket", "multiprocessing", "shutil", "pty",
    "ftplib", "smtplib", "telnetlib", "http", "urllib", "requests",
    "importlib", "pickle", "marshal", "webbrowser", "winreg", "ctypes.util",
}

# 禁止调用的属性：破坏性文件操作与动态执行
BANNED_ATTRS = {
    "system", "popen", "remove", "unlink", "rmdir", "removedirs", "rmtree",
    "rename", "replace", "chmod", "chown", "kill", "terminate", "fork",
    "spawn", "execv", "execve", "execl", "eval", "exec", "compile", "__import__",
}


def check_python(code: str) -> List[str]:
    """静态检查，返回问题列表（空 = 通过）。语法错误也算问题。"""
    problems: List[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"语法错误：第 {exc.lineno} 行 {exc.msg}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in BANNED_MODULES:
                    problems.append(f"禁止导入模块 `{alias.name}`")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_MODULES:
                problems.append(f"禁止导入模块 `{node.module}`")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if name in BANNED_ATTRS:
                problems.append(f"禁止调用 `{name}()`")
    # 去重保序
    seen = set()
    uniq = []
    for p in problems:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def _kill_tree(pid: int) -> None:
    """超时后按进程树强杀（Windows 上 proc.kill() 杀不掉孙子进程）。"""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        else:
            os.killpg(os.getpgid(pid), 9)
    except Exception:
        pass


def _run_sync(
    argv: List[str],
    workdir: Path,
    timeout: float,
    env: Dict[str, str],
    stdin_data: str,
) -> Dict[str, Any]:
    """同步执行（在线程里调用）。

    为什么不用 asyncio 的子进程：在 Windows 上 `asyncio.create_subprocess_exec`
    配管道会直接 `PermissionError [WinError 5]`（实测本机如此），
    而 `subprocess` + 线程在任何环境下都稳。代价是拿不到逐行流式输出——
    但我们本来也是等全部跑完再回灌给模型，没有损失。
    """
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    started = time.time()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(workdir),
            env=env,
            stdin=subprocess.PIPE if stdin_data else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs,
        )
    except Exception as exc:
        return {
            "ok": False, "exit_code": -1, "stdout": "",
            "stderr": f"启动进程失败：{exc}",
            "ms": int((time.time() - started) * 1000), "timed_out": False,
        }

    timed_out = False
    try:
        out, err = proc.communicate(input=(stdin_data.encode("utf-8") if stdin_data else None), timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc.pid)
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = b"", b""
    except Exception as exc:  # noqa: BLE001
        _kill_tree(proc.pid)
        out, err = b"", str(exc).encode("utf-8")

    stdout = (out or b"").decode("utf-8", "replace")
    stderr = (err or b"").decode("utf-8", "replace")
    if len(stdout) > MAX_OUTPUT:
        stdout = stdout[:MAX_OUTPUT] + "\n…（输出过长已截断）"
    if len(stderr) > MAX_OUTPUT:
        stderr = stderr[:MAX_OUTPUT] + "\n…（输出过长已截断）"
    if timed_out:
        stderr += f"\n（已超时 {timeout:.0f} 秒，进程树已强制结束）"

    return {
        "ok": (not timed_out) and proc.returncode == 0,
        "exit_code": proc.returncode if proc.returncode is not None else -1,
        "stdout": stdout,
        "stderr": stderr,
        "ms": int((time.time() - started) * 1000),
        "timed_out": timed_out,
    }


async def run_command(
    argv: List[str],
    workdir: Path,
    timeout: float = 20,
    env_extra: Optional[Dict[str, str]] = None,
    stdin_data: str = "",
) -> Dict[str, Any]:
    """异步接口：跑一条命令，返回 {ok, exit_code, stdout, stderr, ms, timed_out}。"""
    workdir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.pop("PYTHONPATH", None)
    if env_extra:
        env.update(env_extra)
    return await asyncio.to_thread(_run_sync, argv, workdir, timeout, env, stdin_data)


async def run_python(
    code: str, workdir: Path, timeout: float = 20, filename: str = "_run.py"
) -> Dict[str, Any]:
    """把代码写成文件再执行。

    为什么要落盘而不是 `-c`：学生要能看到「代码变成了一个文件」，
    而且报错行号能对上文件，回灌给模型时它更容易改对。
    """
    problems = check_python(code)
    if problems:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "安全护栏拦下了这段代码：\n- " + "\n- ".join(problems)
            + "\n（教学场景不允许进程创建、网络访问、删除文件、动态执行。请改写代码。）",
            "ms": 0,
            "timed_out": False,
            "blocked": True,
        }
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / filename
    script.write_text(code, encoding="utf-8")
    result = await run_command([sys.executable, "-X", "utf8", "-u", script.name], workdir, timeout)
    result["script"] = script.name
    return result


def list_files(workdir: Path, limit: int = 200) -> List[Dict[str, Any]]:
    """列出工作目录里的产物（前端文件面板用）。"""
    if not workdir.exists():
        return []
    items: List[Dict[str, Any]] = []
    for path in sorted(workdir.rglob("*")):
        if not path.is_file() or path.name.startswith("_") or path.name.endswith(".tmp"):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append(
            {
                "path": str(path.relative_to(workdir)).replace("\\", "/"),
                "name": path.name,
                "bytes": stat.st_size,
                "mtime": int(stat.st_mtime),
                "kind": kind_of(path.name),
            }
        )
        if len(items) >= limit:
            break
    return items


def kind_of(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {
        ".html": "html", ".htm": "html",
        ".py": "python", ".js": "javascript", ".css": "css",
        ".json": "json", ".csv": "csv", ".md": "markdown", ".txt": "text",
        ".png": "image", ".jpg": "image", ".jpeg": "image", ".svg": "image", ".gif": "image",
        ".pdf": "pdf",
    }.get(ext, "file")
