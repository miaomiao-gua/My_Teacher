"""双击 exe 的入口。

单独一个文件，是为了给 PyInstaller 一个干净的入口（它的 sys.path[0] 就是本目录，
所以 `desktop` / `kernel` 都能直接 import）。

窗口程序没有控制台：这里兜住所有异常 —— 写日志 + 弹一个说明框，
而不是让用户双击完看着桌面什么都没发生。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _bootstrap() -> int:
    from desktop.shell import main
    return main()


def _report(exc: BaseException) -> None:
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        import runtime_paths
        data_dir = runtime_paths.data_root()
        log_dir = data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "crash.log").write_text(text, encoding="utf-8")
        where = str(log_dir / "crash.log")
    except Exception:
        where = "（日志也写不出来，可能是权限问题）"
    try:
        if "--check" in sys.argv:      # 自检模式只写日志，不弹框
            raise RuntimeError("check mode")
        from desktop.shell import _msgbox
        _msgbox("艾琳老师 · 启动失败",
                f"启动时出了点问题：\n\n{type(exc).__name__}: {exc}\n\n详细信息在：\n{where}")
    except Exception:
        pass
    try:
        if sys.stderr is not None:
            sys.stderr.write(text)
    except Exception:
        pass


if __name__ == "__main__":
    try:
        raise SystemExit(_bootstrap())
    except SystemExit:
        raise
    except BaseException as exc:            # noqa: BLE001 —— 窗口程序必须兜住一切
        _report(exc)
        raise SystemExit(3)
