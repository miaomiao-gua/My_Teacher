"""打包成单个 exe：`MyTeacher.exe`。

用 PyInstaller onefile。为什么是 onefile 而不是 onedir：
数据目录的继承规则里有一条「exe 同目录的 data/」，而**把 exe 直接放在 agent_studio/ 里**
就能无缝接上现有的会话、API Key、课程 —— onefile 只有一个文件，放哪儿都行；
onedir 会多出一层目录，规则就得多绕一圈。

打包时最容易漏的三类东西（都踩过）：
1. **数据文件**：web/、plugins/、course/*.py、kernel/tools/dom_runner.js
   —— 它们不是 Python 模块，PyInstaller 看不见，必须 --add-data；
2. **运行时按路径导入的模块**：course/*.py 是靠 sys.path 动态 import 的，
   分析不出来，必须 --hidden-import（同时又 --add-data 一份在磁盘上，双保险）；
3. **uvicorn 的协议实现**：它是按字符串动态加载的，同样分析不出来。

跑法：py -3.14 desktop/build_exe.py            # 打包
      py -3.14 desktop/build_exe.py --check-only   # 只检查环境、不打包
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent                     # agent_studio/
ENTRY = HERE / "ailin_teacher.py"
NAME = "MyTeacher"          # exe 文件名（学生看到的就是这个；界面里的老师仍叫艾琳）
DIST = ROOT / "dist_desktop"
WORK = ROOT / "build_desktop"

# 运行时按路径导入 / 动态加载的模块。
# **不要**把 course/*.py 写进来：一旦 PyInstaller 去分析它们，就会顺着
# file_utils 的可选 OCR 依赖把 torch / cv2 / scipy 全套拖进来 ——
# 实测产物从 47MB 涨到 375MB、打包时间 99 秒变 584 秒。
# course/ 是用 --add-data 整目录带过去的，运行时由 course.py/lesson.py/materials.py
# 把它加进 sys.path 再 import，够用且干净。
HIDDEN = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # 读 Word / PDF 的库：course/file_utils.py 里是可选导入（缺了不崩），
    # 但既然机器上有，就带上 —— 找教材、读讲义要用。
    "docx",
    "pypdf",
]

# 明确排除的重家伙：这些只被「可选 OCR / 数据分析」用到，
# 带上就是几百 MB，而且教学场景根本用不到（缺了也只是那句提示）。
EXCLUDES = [
    "torch", "torchvision", "torchaudio", "easyocr", "pytesseract", "paddleocr",
    "cv2", "scipy", "matplotlib", "pandas", "sklearn", "seaborn", "numba",
    "onnxruntime", "transformers", "IPython", "notebook", "nbformat",
    # numpy 是**实测**发现的漏网之鱼：`course/file_utils.py` 的 OCR 分支里
    # `import numpy`，PyInstaller 只看代码就会把它整个打进来，白占 8.4 MB
    # （numpy 2.1 MB + numpy.libs 6.3 MB，见 tools/peek_exe_size.py 的输出）。
    # 那一段的前置条件是 easyocr（已排除），运行到那儿之前就会 return，所以安全。
    "numpy",
]

# 数据文件：(源, exe 里的相对目录)
DATAS = [
    (ROOT / "web", "web"),
    (ROOT / "plugins", "plugins"),
    (ROOT / "course", "course"),
    (ROOT / "kernel" / "tools" / "dom_runner.js", "kernel/tools"),
]


def make_icon(out: Path) -> Path:
    """图标直接用桌宠的形象画一个（多尺寸 .ico，任务栏和资源管理器都清楚）"""
    sys.path.insert(0, str(ROOT))
    from desktop.pet_art import render_state
    img = render_state("happy")
    # 加个圆角底，小尺寸下也看得清
    from PIL import Image, ImageDraw
    canvas = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((8, 8, 504, 504), radius=96, fill=(246, 231, 198, 255),
                        outline=(122, 90, 46, 255), width=10)
    big = img.resize((440, 500), Image.LANCZOS)
    canvas.paste(big, (36, 20), big)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 MyTeacher 桌面版")
    ap.add_argument("--check-only", action="store_true")
    ap.add_argument("--clean", action="store_true", help="先删掉上次的 build/dist")
    ap.add_argument("--copy-to-project", action="store_true", default=True,
                    help="打包完把 exe 复制到 agent_studio/（数据继承最直观）")
    args = ap.parse_args()

    print(f"项目根：{ROOT}")
    print(f"Python：{sys.version.split()[0]} ({sys.executable})")
    try:
        import PyInstaller                            # noqa: F401
        import PyInstaller.__main__ as pi_main
        print(f"PyInstaller：{PyInstaller.__version__}")
    except Exception as exc:
        print(f"❌ 没有 PyInstaller：{exc}")
        return 1
    missing = [str(src) for src, _ in DATAS if not src.exists()]
    if missing:
        print("❌ 这些要打包的东西不存在：" + ", ".join(missing))
        return 1
    if not ENTRY.exists():
        print(f"❌ 缺入口脚本：{ENTRY}")
        return 1

    icon = make_icon(WORK / "ailin.ico")
    print(f"图标：{icon}")

    if args.check_only:
        print("（--check-only）环境没问题，可以打包。")
        return 0

    if args.clean:
        shutil.rmtree(DIST, ignore_errors=True)
        shutil.rmtree(WORK / "pyi", ignore_errors=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onefile", "--windowed",
        # 每次都 --clean：PyInstaller 会复用上次的分析缓存，改了代码却打出旧版
        # （实测踩过：crash.log 里的源码行号和当前文件对不上，白查半天）
        "--clean",
        "--name", NAME,
        "--icon", str(icon),
        "--distpath", str(DIST),
        "--workpath", str(WORK / "pyi"),
        "--specpath", str(WORK),
        "--log-level", "WARN",
        "--paths", str(ROOT),
    ]
    for src, dest in DATAS:
        cmd += ["--add-data", f"{src}{os.pathsep}{dest}"]
    for mod in HIDDEN:
        cmd += ["--hidden-import", mod]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    cmd.append(str(ENTRY))

    print("\n开始打包（1~3 分钟）…")
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if proc.returncode != 0:
        print(f"❌ 打包失败（退出码 {proc.returncode}）")
        return proc.returncode

    exe = DIST / f"{NAME}.exe"
    if not exe.is_file():
        print(f"❌ 没找到产物 {exe}")
        return 1
    size_mb = exe.stat().st_size / 1024 / 1024
    print(f"\n✅ 打包完成（{time.time() - t0:.0f} 秒）：{exe}  {size_mb:.1f} MB")

    if args.copy_to_project:
        target = ROOT / f"{NAME}.exe"
        # 先删旧的：如果上一份还在运行，文件被占用 → 复制会失败。
        # 这里**必须把失败喊出来**：我有一次没注意它悄悄失败了，
        # 结果拿着项目目录里那份旧 exe 查了半天 bug（新版在 dist_desktop 里）。
        try:
            if target.exists():
                target.unlink()
            shutil.copy2(exe, target)
            print(f"   已复制到 {target}（放在这里 = 直接接上现有 data/）")
        except Exception as exc:
            print(f"   ⚠️ 复制到 {target} 失败：{exc}")
            print(f"      → 请手动复制，或先关掉正在运行的那一份；"
                  f"现在项目目录里的可能还是旧版！")
    print(f"   自检：`{NAME}.exe --check`（端口/数据目录/浏览器会写进 data/logs/desktop.log）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
