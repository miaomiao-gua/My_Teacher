"""运行时路径：把「只读资源」和「可写数据」分开 —— 打包成 exe 的关键前提。

为什么必须有这个文件
--------------------
打包后代码所在目录是 PyInstaller 的**临时解包目录**（只读，每次启动还可能换名字）。
如果运行时数据还写在代码目录旁边，就会出现最糟糕的那种失败：**学生关一次程序，
历史对话、API Key、生成的课程全没了** —— 而且不报错，就是「变空了」。

以前有四处各写各的路径（`kernel/config.py`、`kernel/crypto_utils.py`、`course/file_utils.py`
…），打包时只要漏改一处，就会出现「密钥在 A 目录、配置在 B 目录」这种诡异现象
（加密的 API Key 解不开，云端一路 401）。所以统一收到这里：

- `resource_root()`：只读资源（web/、plugins/、course/、dom_runner.js）——打包后指向解包目录
- `data_root()`：可写数据（会话/工作区/课程/密钥/提醒）——**优先 exe 同目录的 data/**（便携）

选中的目录会打印一次，出问题时一眼能看出它到底用了哪个。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent          # agent_studio/（源码运行时）
_DATA_MARKERS = ("config.json", "settings.json", "folders.json", "sessions", "lessons")
_chosen: Path | None = None
_announced = False


def _say(msg: str) -> None:
    """打印一行诊断。

    **打包成无控制台的窗口程序时 sys.stdout 是 None**，直接 print 会抛
    AttributeError 把启动流程打断 —— 所以这里必须兜住（踩过这个坑的都知道多难查）。
    """
    try:
        if sys.stdout is not None:
            print(msg, flush=True)
    except Exception:
        pass


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打出来的 exe 里"""
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """只读资源根目录（web/、plugins/、course/ 都挂在它下面）"""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        return Path(sys.executable).resolve().parent
    return _ROOT


def exe_dir() -> Path:
    """exe 所在目录（源码运行时就是项目目录 agent_studio/）"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return _ROOT


def looks_like_data(p: Path) -> bool:
    """这个目录像不像「已经在用的数据目录」——用来优先继承用户已有的数据"""
    try:
        if not p.is_dir():
            return False
        return any((p / m).exists() for m in _DATA_MARKERS)
    except Exception:
        return False


def _writable(p: Path) -> bool:
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def _candidates() -> list[tuple[str, Path]]:
    """按优先级列出候选数据目录：先「已有数据」的，再能写的"""
    here = exe_dir()
    out: list[tuple[str, Path]] = []
    env = (os.environ.get("AGENT_STUDIO_DATA") or "").strip()
    if env:
        out.append(("环境变量 AGENT_STUDIO_DATA", Path(env).expanduser()))
    # ① exe 同目录的 data/（把 exe 放在 agent_studio/ 里就是项目现有的 data/）
    out.append(("exe 同目录的 data/", here / "data"))
    # ② 再往上一层找 agent_studio/data（把 exe 放在 dist/ 之类的子目录时）
    out.append(("上级的 agent_studio/data", here.parent / "agent_studio" / "data"))
    out.append(("上级的 data/", here.parent / "data"))
    # ③ 兜底：%APPDATA%
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    out.append(("%APPDATA%\\AgentStudio\\data", Path(appdata) / "AgentStudio" / "data"))
    return out


def data_root() -> Path:
    """可写数据目录。优先级见 `_candidates()`；结果会缓存并打印一次。"""
    global _chosen, _announced
    if _chosen is not None:
        return _chosen

    # 环境变量是**显式指定**，必须无条件优先（哪怕目录是空的、还没建）——
    # 否则「我想让数据放这儿」会被「旁边有个旧 data/」悄悄顶掉（测试实测踩到）。
    env = (os.environ.get("AGENT_STUDIO_DATA") or "").strip()
    if env:
        p = Path(env).expanduser()
        if _writable(p):
            _chosen = p
            why = "环境变量 AGENT_STUDIO_DATA（显式指定）"
        else:
            _chosen = None
    else:
        _chosen = None

    if _chosen is None:
        cands = [c for c in _candidates() if not c[0].startswith("环境变量")]
        # 第一轮：已经在用的数据目录（继承现有会话/密钥 —— 换机器或换目录时才不会「变空」）
        for label, p in cands:
            if looks_like_data(p) and _writable(p):
                _chosen, why = p, f"{label}（已有数据，直接继承）"
                break
        else:
            # 第二轮：第一个能写的地方
            for label, p in cands:
                if _writable(p):
                    _chosen, why = p, f"{label}（新建）"
                    break
            else:                                 # 理论上不会走到（%APPDATA% 一般能写）
                _chosen, why = Path.cwd() / "data", "当前工作目录（兜底，可能没有写权限）"

    if not _announced:
        _announced = True
        _say(f"[paths] 数据目录：{_chosen}  ← {why}")
        if is_frozen():
            _say(f"[paths] 只读资源目录：{resource_root()}")
    return _chosen


def describe() -> dict:
    """给 /agent/version 之类的接口用：一眼看出 exe 到底在用哪个目录"""
    return {
        "frozen": is_frozen(),
        "resource_root": str(resource_root()),
        "exe_dir": str(exe_dir()),
        "data_root": str(data_root()),
        "candidates": [f"{label} → {p}" + ("（有数据）" if looks_like_data(p) else "") for label, p in _candidates()],
    }


def reset_cache() -> None:
    """清掉「已选中的数据目录」缓存。

    只在测试里用：要验不同 exe 位置/环境变量下的选择结果，不能靠进程第一次的缓存。
    """
    global _chosen, _announced
    _chosen = None
    _announced = True          # 测试里别再往 stdout 刷那行说明
