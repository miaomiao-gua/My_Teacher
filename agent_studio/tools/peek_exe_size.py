#!/usr/bin/env python
"""看看 exe 里到底装了什么、各占多少 MB（回答「是不是太大了」要用数据，不用猜）。

用法：py -3.14 tools/peek_exe_size.py [exe路径]
      默认看 dist_desktop/MyTeacher.exe，其次项目里的那两份。

原理：PyInstaller 的单文件 exe 其实是个压缩包（CArchive），里面还有一个 PYZ
（Python 模块的压缩包）。两层都读出来，按顶层包名汇总，就知道是谁把体积撑起来的。
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = [
    ROOT / "dist_desktop" / "MyTeacher.exe",
    ROOT / "MyTeacher.exe",
    ROOT / "dist_desktop" / "艾琳老师.exe",
    ROOT / "艾琳老师.exe",
]


def human(n: float) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else next((p for p in CANDIDATES if p.is_file()), None)
    if not exe or not exe.is_file():
        print("找不到 exe：" + str(exe))
        return 1

    print(f"分析：{exe}  （{human(exe.stat().st_size)}）\n")
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    car = CArchiveReader(str(exe))
    toplevel = car.toc
    pkg = defaultdict(int)
    total = 0
    for name, entry in toplevel.items():
        try:
            size = entry[1] if isinstance(entry, (list, tuple)) else 0
        except Exception:
            size = 0
        total += size
        top = name.replace("\\", "/").split("/")[0]
        pkg[top] += size

    pyz = None
    for name, entry in toplevel.items():
        if str(name).endswith(".pyz") or str(name).lower().endswith("pyz.pyz"):
            pyz = name
    print("【打包进去的大件（未压缩口径）】")
    for name, size in sorted(pkg.items(), key=lambda kv: -kv[1])[:22]:
        print(f"  {human(size):>10}  {name}")
    print(f"  {'-' * 10}\n  {human(total):>10}  合计（含 Python 运行时/库文件）\n")

    # PYZ：Python 模块（按顶层包汇总，看谁是代码大头）
    if pyz:
        try:
            z = ZlibArchiveReader(str(exe), pyz)
            mp = defaultdict(int)
            for mod, entry in z.toc.items():
                size = entry[1] if isinstance(entry, (list, tuple)) and len(entry) > 1 else 0
                top = str(mod).replace("\\", "/").split("/")[0].split(".")[0]
                mp[top] += size or 0
            print("【Python 模块（PYZ，压缩前口径）】")
            for name, size in sorted(mp.items(), key=lambda kv: -kv[1])[:18]:
                print(f"  {human(size):>10}  {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"（PYZ 读不出来：{type(exc).__name__}: {exc}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
