#!/usr/bin/env python
"""打包后的 exe 端到端自检：**双击那种形态**下，功能是不是真的都在。

为什么必须单独验一遍：源码里跑得好好的，打包后经常少东西 ——
最常见的就是「动态导入没被 PyInstaller 看见」（比如 win32com 的 SAPI、
uvicorn 的协议实现、docx/pypdf 这些可选依赖）。这类故障的表现是
**双击 exe 之后某个功能静默失效**，而开发者自己跑源码永远碰不到。

它会：启动 exe → 等它就绪（自动找端口）→ 逐个打接口 → 关掉 exe → 出结论。
用法：py -3.14 tools/verify_exe.py [exe路径]
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 默认用**用户实际双击的那份**（agent_studio\MyTeacher.exe，旁边就是 data/）。
# ★ exe 的「项目根」是**按它自己所在目录**算的（config.py 的 LEGACY_ROOT），
#   所以放错地方会看不到 client/ 里的模型：dist_desktop 里那份只扫到 3 个形象，
#   agent_studio 里这份能扫到 7 个。要验行为，就验用户点的那份。
EXE_DEFAULT = ROOT / "MyTeacher.exe"

fails = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global fails
    if not ok:
        fails += 1
    print(("  [OK]   " if ok else "  [FAIL] ") + name + (f"  ->  {detail}" if detail else ""))


def find_port(exe: Path, timeout: float = 60.0) -> int | None:
    """找出 exe 实际用的端口。

    两条路，先看 `data/desktop.lock`（里面有 port，最可靠），再看日志里那行「服务就绪」。
    ★ 注意：exe 有**单实例保护** —— 如果已经有一个在跑，新的这个会直接退出、
      日志一行都不写。以前就是被这个坑了：脚本等了 60 秒说"启动失败"，
      其实是我自己刚才手动开的那个还活着。所以这里也检查"是不是已经有实例在跑"。
    """
    import json as _json

    lock = ROOT / "data" / "desktop.lock"
    log = ROOT / "data" / "logs" / "desktop.log"
    start = log.stat().st_size if log.is_file() else 0
    t0 = time.time()
    deadline = t0 + timeout
    while time.time() < deadline:
        time.sleep(1.2)
        # ① 锁文件里直接有端口（且是这次启动写的）
        try:
            if lock.is_file():
                info = _json.loads(lock.read_text(encoding="utf-8"))
                port = int(info.get("port") or 0)
                if port and float(info.get("started") or 0) >= t0 - 5:
                    return port
        except Exception:
            pass
        # ② 日志里那行 http://127.0.0.1:PORT
        try:
            if log.is_file():
                text = log.read_text(encoding="utf-8", errors="replace")[start:]
                for token in reversed(text.replace("：", " ").split()):
                    if "127.0.0.1:" in token:
                        return int(token.split("127.0.0.1:")[1].strip("/ \t\r\n"))
        except Exception:
            pass
    return None


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) > 1 else EXE_DEFAULT
    if not exe.is_file():
        print(f"找不到 exe：{exe}")
        return 1

    import httpx

    print(f"启动 {exe.name}（{exe.stat().st_size / 1048576:.1f} MB）…")
    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    try:
        port = find_port(exe)
        if not port:
            print("  [FAIL] 等了 60 秒没看到它报端口 —— 启动就失败了？看 data/logs/desktop.log")
            return 1
        base = f"http://127.0.0.1:{port}"
        print(f"它就绪了：{base}\n")

        def get(path: str, **kw):
            try:
                return httpx.get(base + path, timeout=30.0, **kw)
            except Exception as exc:  # noqa: BLE001
                return exc

        r = get("/agent/health")
        ok = hasattr(r, "status_code") and r.status_code == 200
        check("健康检查", ok, f"HTTP {getattr(r, 'status_code', '?')}")
        tools = r.json().get("tools") if ok else 0
        check("工具都注册了（源码里是 34 个）", tools >= 34, f"{tools} 个")
        # ★ 这条是"密钥能不能解开"的函数级验证：解不开时云端链路会 ok=False
        #   （exe 里曾经报过「密钥解密失败」，所以必须在这里盯住）
        chain = r.json().get("chain") if ok else []
        cloud = [c for c in chain if c.get("provider") == "openai"]
        if cloud:
            check("★ exe 里能解开 API Key（云端链路通）", bool(cloud[0].get("ok")),
                  f"{cloud[0].get('label')} ok={cloud[0].get('ok')} {str(cloud[0].get('detail') or '')[:60]}")

        r = get("/agent/settings/all")
        check("设置中心接口（模型/Key/人格/桌宠/语音）",
              hasattr(r, "status_code") and r.status_code == 200 and "persona" in r.text,
              f"HTTP {getattr(r, 'status_code', '?')}")

        r = get("/agent/tts/voices")
        check("语音接口能列出系统嗓子（pywin32 带进来了）",
              hasattr(r, "status_code") and r.status_code == 200 and "windows" in r.text,
              f"HTTP {getattr(r, 'status_code', '?')}")

        # ★ 这一条最容易在打包后挂：win32com 是动态导入，PyInstaller 常常看不到
        try:
            resp = httpx.post(base + "/agent/tts/say", timeout=90.0,
                              json={"text": "打包后的桌面版，本机语音能用。",
                                    "override": {"provider": "windows", "windows_voice": 0}})
            good = resp.status_code == 200 and resp.content[:4] == b"RIFF" and len(resp.content) > 2000
            check("★ 在 exe 里用 Windows 本机嗓子合成（离线语音）", good,
                  f"HTTP {resp.status_code} {len(resp.content)}B {resp.content[:4]}")
        except Exception as exc:  # noqa: BLE001
            check("★ 在 exe 里用 Windows 本机嗓子合成（离线语音）", False, f"{type(exc).__name__}: {exc}")

        for path, name in (("/agent/pet", "桌宠独立页(路由)"),
                           ("/static/js/pet_core.js", "桌宠核心 JS"),
                           ("/static/css/pet.css", "桌宠样式"),
                           ("/static/js/agent_ui.js?v=0", "工作台渲染器")):
            r = get(path)
            check(f"{name} 能取到", hasattr(r, "status_code") and r.status_code == 200,
                  f"HTTP {getattr(r, 'status_code', '?')}")

        r = get("/agent/live2d/models")
        models = []
        if hasattr(r, "status_code") and r.status_code == 200:
            models = r.json().get("models") or []
        # 数量随 exe 放哪儿而变（项目根按 exe 位置算），所以只要求"扫得动"
        check("形象清单（扫盘在 exe 里也能跑）", len(models) > 0, f"{len(models)} 个：{exe.parent.name}\\ 里跑")
        r = get("/agent/plugins")
        pl = {}
        if hasattr(r, "status_code") and r.status_code == 200:
            raw = r.json().get("plugins")
            items = list(raw.values()) if isinstance(raw, dict) else (raw or [])
            pl = {p.get("name"): p for p in items if isinstance(p, dict)}
        for name in ("acid_base_lab",):
            p = pl.get(name) or {}
            check(f"插件 {name} 在 exe 里加载成功（不能 import 老包 agent）",
                  bool(p.get("loaded")),
                  ("工具=" + str(p.get("tools"))) if p.get("loaded") else (p.get("error") or "没这个插件"))

        r = get("/agent/tools")
        names = [t.get("name") for t in (r.json().get("tools") or [])] if hasattr(r, "status_code") and r.status_code == 200 else []
        for must in ("render_ui", "graph_calculator", "file_conversation", "set_reminder"):
            check(f"关键工具在：{must}", must in names)

        print("\n" + ("exe 自检全部通过 [OK]" if fails == 0 else f"exe 自检失败 {fails} 项 [FAIL]"))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        # exe 会另开一个 Edge 应用窗口，一起收掉（只关它自己的 profile）
        try:
            subprocess.run(["taskkill", "/F", "/IM", "msedge.exe", "/FI",
                            "WINDOWTITLE eq *MyTeacher*"], capture_output=True, timeout=10)
        except Exception:
            pass
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
