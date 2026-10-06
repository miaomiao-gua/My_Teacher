#!/usr/bin/env python
"""查一个 exe 里到底带了哪些 Python 模块（排查「源码里好的、打包后少个模块」）。

用法：py -3.14 tools/peek_exe_modules.py dist_desktop/MyTeacher.exe kernel pet

为什么需要它：PyInstaller 是把 .pyc 打进 exe 里的一个 PYZ 压缩包，
`--add-data` 的文件能直接在包里看到，但**Python 模块看不到** ——
「exe 里少一个模块」这种故障（本地好好的、双击 exe 就 500）只能这样查。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist_desktop" / "MyTeacher.exe"
    prefixes = sys.argv[2:] or ["kernel", "desktop", "course"]
    if not exe.is_file():
        print("找不到 exe：" + str(exe))
        return 1

    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    car = CArchiveReader(str(exe))
    pyz_name = next((n for n in car.toc if str(n).lower().endswith(".pyz")), None)
    if not pyz_name:
        print("exe 里没有 PYZ")
        return 1

    # PYZ 是 exe 里的一段压缩数据：先落成临时文件，再按普通 PYZ 读
    import tempfile

    raw = car.extract(pyz_name)
    tmp = Path(tempfile.mkdtemp(prefix="pyz_")) / "PYZ.pyz"
    tmp.write_bytes(raw)
    pyz = ZlibArchiveReader(str(tmp))
    names_ = sorted(str(k) for k in pyz.toc)
    names = names_
    print(f"exe 里有 {len(names)} 个 Python 模块\n")

    hit_all = True
    for pre in prefixes:
        hits = [n for n in names if n == pre or n.startswith(pre + ".")]
        print(f"[{pre}] {len(hits)} 个模块")
        for n in hits[:40]:
            print("   " + n)
        if len(hits) > 40:
            print(f"   …（还有 {len(hits) - 40} 个）")
        if not hits:
            hit_all = False
        print()

    # 只列模块名，不报「体积」：PYZ 的 toc 里那个数字不是压缩后大小，
    # 我一开始按它算，出来「pygments 2849 MB」这种荒唐数字，会误导排查方向。
    return 0 if hit_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
