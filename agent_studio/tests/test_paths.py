#!/usr/bin/env python
"""路径解析自测：打包成 exe 之后数据**不能丢**。

这是整个桌面版最容易出、也最难发现的一类 bug：exe 里代码跑在只读的临时解包目录，
数据要是跟着写进去，学生关一次程序，历史对话 / API Key / 生成的课程就全没了，
而且**不报错**，就是「变空了」。所以这里把选择规则钉死：

  1. 环境变量 AGENT_STUDIO_DATA 最优先
  2. exe 同目录的 data/（已经把 exe 放在 agent_studio/ 里 → 正好继承现有数据）
  3. 上级的 agent_studio/data（exe 被放进 dist/ 之类子目录时）
  4. %APPDATA%\\AgentStudio\\data（兜底）
  另外还验一条不变量：**config / crypto_utils / course.file_utils 必须用同一个数据目录**
  —— 分家的话加密的 API Key 会解不开（症状是云端一路 401，但健康检查还报「配置齐全」）。

跑法：py -3.14 tests/test_paths.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

import runtime_paths                                    # noqa: E402

_failures = 0

# 临时目录**不放在 %TEMP%**：本机的沙箱不允许在平台临时目录下新建子目录
# （PermissionError WinError 5），而且在工作区里建也便于出问题时自己去看。
SCRATCH = ROOT / "data" / "_pathtest"


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


def make_data_dir(p: Path, marker: str = "config.json") -> Path:
    p.mkdir(parents=True, exist_ok=True)
    (p / marker).write_text("{}", encoding="utf-8")
    return p


def main() -> int:
    real_exe_dir = runtime_paths.exe_dir
    real_env = os.environ.get("AGENT_STUDIO_DATA")
    real_appdata = os.environ.get("APPDATA")

    shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)

    try:
        tmp = SCRATCH

        print("===== 1) 环境变量优先 =====")
        envdir = make_data_dir(tmp / "envdata")
        os.environ["AGENT_STUDIO_DATA"] = str(envdir)
        runtime_paths.reset_cache()
        runtime_paths.exe_dir = lambda: tmp / "somewhere"
        check("AGENT_STUDIO_DATA 指定谁就用谁", runtime_paths.data_root() == envdir,
              str(runtime_paths.data_root()))

        print("\n===== 2) exe 同目录已有 data/ → 继承（现有数据不能丢）=====")
        os.environ.pop("AGENT_STUDIO_DATA", None)
        app = tmp / "agent_studio"
        have = make_data_dir(app / "data", "settings.json")
        (have / "sessions").mkdir(exist_ok=True)
        runtime_paths.reset_cache()
        runtime_paths.exe_dir = lambda: app
        check("选中 exe 同目录的 data/", runtime_paths.data_root() == have, str(runtime_paths.data_root()))
        check("认得出这是个「在用的」数据目录", runtime_paths.looks_like_data(have))

        print("\n===== 3) exe 在 dist/ 子目录 → 找上级的 agent_studio/data =====")
        dist = tmp / "proj" / "agent_studio" / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        parent_data = make_data_dir(tmp / "proj" / "agent_studio" / "data", "folders.json")
        runtime_paths.reset_cache()
        runtime_paths.exe_dir = lambda: dist
        check("往上找到 agent_studio/data", runtime_paths.data_root() == parent_data,
              str(runtime_paths.data_root()))

        print("\n===== 4) 全新机器（哪都没有）→ 在 exe 旁边新建 =====")
        # 注意要放在一个**独立的空目录树**里：否则上一节留下的 agent_studio/data
        # 会被「往上找」那条规则命中（那个行为本身是对的，是测试场景串了）
        fresh_root = tmp / "case4"
        fresh = fresh_root / "somewhere"
        fresh.mkdir(parents=True, exist_ok=True)
        os.environ["APPDATA"] = str(tmp / "appdata")
        runtime_paths.reset_cache()
        runtime_paths.exe_dir = lambda: fresh
        chosen = runtime_paths.data_root()
        check("在 exe 同目录新建 data/", chosen == fresh / "data", str(chosen))
        check("真的建出来了", chosen.is_dir())

        print("\n===== 5) 候选顺序里有 %APPDATA% 兜底 =====")
        labels = [label for label, _ in runtime_paths._candidates()]
        check("最后一个候选是 %APPDATA%", "APPDATA" in labels[-1], "；".join(labels))

        print("\n===== 6) 只读资源目录 =====")
        runtime_paths.exe_dir = real_exe_dir
        runtime_paths.reset_cache()
        check("源码运行时 = 项目目录", runtime_paths.resource_root() == ROOT, str(runtime_paths.resource_root()))
        old_frozen, old_meipass = getattr(sys, "frozen", None), getattr(sys, "_MEIPASS", None)
        try:
            sys.frozen = True                                  # type: ignore[attr-defined]
            sys._MEIPASS = str(ROOT / "_fake_bundle")           # type: ignore[attr-defined]
            check("打包后 = 解包目录", runtime_paths.resource_root() == ROOT / "_fake_bundle",
                  str(runtime_paths.resource_root()))
        finally:
            if old_frozen is None:
                delattr(sys, "frozen")
            else:
                sys.frozen = old_frozen
            if old_meipass is None:
                delattr(sys, "_MEIPASS")
            else:
                sys._MEIPASS = old_meipass
        runtime_paths.reset_cache()

        print("\n===== 7) 关键不变量：三处数据目录必须一致 =====")
        from kernel import config
        from kernel import crypto_utils
        import file_utils                                       # course/file_utils.py
        check("config.DATA_DIR 走统一解析", config.DATA_DIR == runtime_paths.data_root(), str(config.DATA_DIR))
        check("密钥文件与 config 同目录（否则密文解不开）",
              crypto_utils.SECRET_FILE.parent == config.DATA_DIR,
              f"{crypto_utils.SECRET_FILE.parent} vs {config.DATA_DIR}")
        check("课程层的数据目录也一致（否则教案/课程会写到别处）",
              Path(file_utils.DATA_DIR).resolve() == config.DATA_DIR.resolve(),
              f"{file_utils.DATA_DIR} vs {config.DATA_DIR}")
        check("前端资源目录存在（打包要带上）",
              config.CLIENT_DIR.is_dir() and (config.CLIENT_DIR / "index.html").exists(),
              str(config.CLIENT_DIR))
        check("插件资源目录存在", config.PLUGINS_DIR.is_dir(), str(config.PLUGINS_DIR))
        check("课程模块目录存在（course/*.py 是运行时按路径导入的）",
              (config.ROOT / "course" / "file_utils.py").exists())

        from kernel.tools.web import DOM_RUNNER
        check("HTML 自检用的 dom_runner.js 能按资源根找到", DOM_RUNNER.exists(), str(DOM_RUNNER))

        print("\n===== 8) /agent/version 能报出实际用的目录 =====")
        info = runtime_paths.describe()
        check("describe() 里有 frozen / data_root / candidates",
              all(k in info for k in ("frozen", "data_root", "resource_root", "candidates")), str(list(info)))
    finally:
        # 恢复环境（免得影响同进程后续导入）
        if real_env is None:
            os.environ.pop("AGENT_STUDIO_DATA", None)
        else:
            os.environ["AGENT_STUDIO_DATA"] = real_env
        if real_appdata is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = real_appdata
        runtime_paths.exe_dir = real_exe_dir
        runtime_paths.reset_cache()
        shutil.rmtree(SCRATCH, ignore_errors=True)

    print()
    if _failures == 0:
        print("全部通过 ✓")
    else:
        print(f"{_failures} 项失败 ✗")
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
