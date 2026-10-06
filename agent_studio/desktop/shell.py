"""桌面版启动器：双击 exe 就起。

做的事（顺序有讲究）：
  1. 单实例检查 —— 已经在跑就把应用窗口提到前面，不再起第二个服务
  2. 挑一个空闲端口（优先 5001，被占就往后；不用随机端口，用户下次还能用同一个地址）
  3. **在后台线程里起 uvicorn**（复用 kernel.server:app，一行界面代码都不用改）
  4. 轮询 /agent/health 等它就绪（没就绪就报清楚，而不是开个空白窗口让人猜）
  5. 打开应用窗口：Edge/Chrome 的 `--app=` 模式（无地址栏，像个原生软件）
     —— 找不到浏览器就退回系统默认浏览器（多一条地址栏，但能用）
  6. 起桌宠：**桌面小窗里跑的就是 /agent/pet 那一页**（同一只 Live2D、同一套换脸与提醒），
     关掉小窗就一起收摊。位置/尺寸/置顶从 data/pet_window.json 读（网页里那两个开关写的）。

为什么要「服务跑在同一个进程里」而不是另起一个 exe：打包成单个 exe 时，
子进程会重新解包一遍自己（慢、还可能被杀软拦），同进程起线程最省事也最稳。

（历史：v1 的桌宠是自己用 Pillow 画的卡通猫头鹰。它和工作台里的 Live2D 不是同一个角色，
用户看到桌面上「两只不一样的玩偶」，于是统一成一只 —— 见 desktop/core.py 的窗口那段注释。）
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Optional

# 允许直接 `py -3.14 agent_studio/desktop/shell.py` 跑（也允许 `-m desktop.shell`）：
# 直接跑脚本时 __package__ 是空的，相对导入会炸，所以统一用「把项目根塞进 sys.path + 绝对导入」。
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from desktop import core          # noqa: E402


def _log(data_dir: Path, msg: str) -> None:
    stamp = time.strftime("%H:%M:%S")
    line = f"[{stamp}] {msg}"
    try:
        if sys.stdout is not None:          # 无控制台的窗口程序里 stdout 是 None
            print(line, flush=True)
    except Exception:
        pass
    try:
        p = data_dir / "logs"
        p.mkdir(parents=True, exist_ok=True)
        with (p / "desktop.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _msgbox(title: str, text: str) -> None:
    """启动失败时弹个框：窗口程序没有控制台，什么都不显示最让人抓狂"""
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)   # type: ignore[attr-defined]
        else:
            print(f"{title}: {text}", flush=True)
    except Exception:
        pass


def _setup_logging(data_dir: Path) -> None:
    """把日志写文件，并且**把 stdout/stderr 接到日志文件上**。

    打包成窗口程序（--windowed）时 sys.stdout / sys.stderr 都是 None：
    - print() 会抛 AttributeError；
    - uvicorn 的日志配置会去问 sys.stderr.isatty()，直接报
      「Unable to configure formatter 'default'」（实测就是这么挂的）。
    所以这里把两个流都指到日志文件，第三方库怎么打印都不会炸。
    """
    import logging
    try:
        p = data_dir / "logs"
        p.mkdir(parents=True, exist_ok=True)
        logfile = p / "desktop.log"
        if sys.stdout is None or sys.stderr is None:
            stream = open(logfile, "a", encoding="utf-8", buffering=1)   # noqa: SIM115
            if sys.stdout is None:
                sys.stdout = stream          # type: ignore[assignment]
            if sys.stderr is None:
                sys.stderr = stream          # type: ignore[assignment]
        logging.basicConfig(
            filename=str(p / "server.log"), level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s", encoding="utf-8",
        )
    except Exception:
        pass


def _pid_alive(pid: int) -> bool:
    """这个进程还在不在。

    用 Win32 的 OpenProcess 直接问系统，**不走 tasklist**：
    起子进程抓输出在受限环境里会失败（沙箱不给管道），而且慢；
    打包成 exe 之后每次双开都要问一次，越快越稳越好。
    """
    if not pid:
        return False
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            k = ctypes.windll.kernel32                     # type: ignore[attr-defined]
            h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                if k.GetExitCodeProcess(h, ctypes.byref(code)):
                    return code.value == STILL_ACTIVE
                return True
            finally:
                k.CloseHandle(h)
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _lock_path(data_dir: Path) -> Path:
    return data_dir / "desktop.lock"


def already_running(data_dir: Path) -> Optional[int]:
    """返回正在运行的 pid（单实例）。看到「进程还在」但端口不通，就当它已经死了。"""
    from desktop import core as _c
    try:
        info = core.load_pos(_lock_path(data_dir))
        pid = int(info.get("pid") or 0)
        port = int(info.get("port") or 0)
        if pid and _pid_alive(pid):
            if port and not _c.port_in_use("127.0.0.1", port):
                time.sleep(0.8)                     # 可能刚起来，再给一次机会
                if not _c.port_in_use("127.0.0.1", port):
                    return None
            return pid
    except Exception:
        pass
    return None


def _uvicorn_log_config(data_dir: Path) -> dict:
    """给 uvicorn 一份**明确的**日志配置（写文件、不带颜色）。

    必须这样做的原因：uvicorn 默认的 DefaultFormatter 会调 sys.stderr.isatty()，
    而窗口程序（--windowed）里 sys.stderr 是 None → 直接
    「AttributeError: 'NoneType' object has no attribute 'isatty'」，
    再被包装成「Unable to configure formatter 'default'」——很难猜的报错。
    给它一个纯文件的配置就彻底绕开了（不依赖任何流是否存在）。
    """
    p = data_dir / "logs"
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
        "handlers": {
            "file": {
                "class": "logging.FileHandler",
                "filename": str(p / "server.log"),
                "formatter": "plain",
                "encoding": "utf-8",
            }
        },
        "loggers": {
            "uvicorn": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["file"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
        },
        "root": {"handlers": ["file"], "level": "INFO"},
    }


def start_server(port: int, host: str = "127.0.0.1", log_level: str = "warning",
                 data_dir: Optional[Path] = None):
    """在后台线程里跑 uvicorn，返回 (server, thread)

    日志显式写成文件（见 `_uvicorn_log_config`）——窗口程序里没有控制台，
    uvicorn 自带的彩色 formatter 会直接报错。
    """
    import uvicorn
    from kernel.server import app as fastapi_app

    cfg = {"log_level": log_level}
    if data_dir is not None:
        cfg["log_config"] = _uvicorn_log_config(data_dir)
    config = uvicorn.Config(fastapi_app, host=host, port=port, **cfg)
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    t.start()
    return server, t


def wait_healthy(base: str, timeout: float = 45.0) -> bool:
    import httpx
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(base + "/agent/health", timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def open_app_window(base: str, url_path: str = "/agent/app", profile: Optional[Path] = None,
                    size=(1440, 920)) -> str:
    """打开应用窗口。返回 'app'（无地址栏）或 'browser'（回退）"""
    url = base + url_path
    browser = core.find_app_browser()
    if browser and profile is not None:
        args = core.app_window_args(browser, url, profile, size)
        if _spawn(args):
            return "app"
    webbrowser.open(url)
    return "browser"


def _spawn(args: list) -> bool:
    """把浏览器拉起来（分离进程，不跟着启动器一起被收走）"""
    try:
        creation = 0
        if os.name == "nt":
            creation = 0x00000008 | 0x08000000      # DETACHED_PROCESS | CREATE_NO_WINDOW
        subprocess.Popen(args, creationflags=creation, close_fds=True)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 桌面小窗：就是**同一只 Live2D**（/agent/pet?win=1），不再是 Pillow 画的那只
# ---------------------------------------------------------------------------
# 上一版桌面上有两只：工作台里的 Live2D 小玩偶 + 桌面上 Pillow 画的卡通猫头鹰，
# 而且它们**不是同一个角色**（一个是艾琳、一个是戴学士帽的猫头鹰）。
# 这一版统一成一只：小窗里跑的就是 /agent/pet 那一页，形象 / 动作 / 换脸全和网页共用。
# 位置和尺寸在启动时用 `--window-position` 给进去 —— Edge 的应用窗口不认网页层的
# window.moveTo()，只能这样。（见 desktop/core.py 的 pet_window_args 注释）

def pet_window_payload(base: str, data_dir: Path) -> dict:
    """算好「小窗该用哪个地址、放在哪儿、多大、要不要置顶」——纯数据，方便自检打印"""
    prefs = core.load_window_prefs(data_dir)
    sw, sh = _screen_size()
    x, y = prefs.get("x"), prefs.get("y")
    if x is None or y is None:
        x, y = core.default_pet_window_pos(sw, sh, prefs["w"], prefs["h"])
    x, y = core.clamp_to_screen(int(x), int(y), prefs["w"], prefs["h"], sw, sh)
    return {
        "url": core.pet_window_url(base),
        "pos": (x, y), "size": (prefs["w"], prefs["h"]),
        "always_top": prefs["always_top"], "hide_header": prefs["hide_header"],
        "screen": (sw, sh),
    }


def _screen_size() -> tuple:
    """屏幕逻辑尺寸。用 Win32 拿（tkinter 已经退役了，不为了这个再把 tk 拉起来）。"""
    try:
        if os.name == "nt":
            import ctypes
            user32 = ctypes.windll.user32          # type: ignore[attr-defined]
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
            return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
    except Exception:
        pass
    return 1920, 1080


def start_pet_window(base: str, data_dir: Path) -> dict:
    """起桌面小窗。返回它实际用的那些参数（自检和日志都要用）"""
    info = pet_window_payload(base, data_dir)
    browser = core.find_app_browser()
    profile = data_dir / "browser_pet"
    if browser:
        args = core.pet_window_args(browser, info["url"], profile,
                                    pos=info["pos"], size=info["size"],
                                    always_top=info["always_top"])
        if _spawn(args):
            info["mode"] = "app"
            return info
    webbrowser.open(info["url"])                   # 没浏览器就当普通网页开，能用
    info["mode"] = "browser"
    return info


def wait_pet_window_closed(base: str, timeout_absent: float = 45.0,
                           poll: float = 2.0) -> str:
    """等小窗关掉。

    怎么知道它关了：桌宠页会订阅 `/agent/pet/events`（SSE），页在 = 订阅数 ≥ 1。
    所以判据是「**先看到订阅出现** → 再看到订阅消失」。
    先等它出现是必须的：窗口刚起来那几秒订阅还没建上，直接看等于自己把自己骗了。
    """
    import httpx

    appeared = False
    deadline = time.time() + timeout_absent
    while time.time() < deadline:
        try:
            n = int(httpx.get(base + "/agent/pet/status", timeout=3).json().get("subscribers") or 0)
        except Exception:
            n = 0
        if n > 0:
            appeared = True
            break
        time.sleep(poll)
    if not appeared:
        return "absent"                            # 一直没来（浏览器没开成？）——交给调用方决定怎么办
    while True:
        try:
            n = int(httpx.get(base + "/agent/pet/status", timeout=3).json().get("subscribers") or 0)
            if n <= 0:
                return "closed"
        except Exception:
            pass                                   # 服务自己出问题不算小窗关了
        time.sleep(poll)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="艾琳老师 · 桌面版")
    ap.add_argument("--port", type=int, default=int(os.environ.get("AGENT_PORT", "5001")))
    ap.add_argument("--no-pet", action="store_true", help="不起桌宠小窗（排查窗口问题时用）")
    ap.add_argument("--no-window", action="store_true", help="不自动打开应用窗口")
    ap.add_argument("--data", help="指定数据目录（默认按 runtime_paths 的规则找）")
    ap.add_argument("--log-level", default="warning")
    ap.add_argument("--check", action="store_true", help="只做自检并打印结果（打包后验收用）")
    args = ap.parse_args(argv)

    if args.data:
        os.environ["AGENT_STUDIO_DATA"] = args.data

    import runtime_paths
    data_dir = runtime_paths.data_root()
    _setup_logging(data_dir)
    _log(data_dir, "=" * 56)
    _log(data_dir, f"艾琳老师 · 桌面版启动中（python {sys.version.split()[0]}）")
    _log(data_dir, f"数据目录：{data_dir}")
    _log(data_dir, f"资源目录：{runtime_paths.resource_root()}")
    _log(data_dir, f"打包运行：{runtime_paths.is_frozen()}")

    dup = already_running(data_dir)
    if dup:
        _log(data_dir, f"已经有一个在跑了（pid {dup}），直接把窗口叫到前面")
        port = int(core.load_pos(_lock_path(data_dir)).get("port") or args.port)
        base = f"http://127.0.0.1:{port}"
        if not args.no_window:
            open_app_window(base, profile=data_dir / "browser")
        return 0

    port = core.pick_free_port("127.0.0.1", args.port)
    base = f"http://127.0.0.1:{port}"
    if port != args.port:
        _log(data_dir, f"端口 {args.port} 被占用，改用 {port}")

    server = None
    if core.port_in_use("127.0.0.1", port):
        _log(data_dir, f"端口 {port} 已有服务（应该是别的实例），直接用它")
    else:
        server, _t = start_server(port, log_level=args.log_level, data_dir=data_dir)
        if not wait_healthy(base):
            note = (f"本地服务 45 秒内没起来。\n\n"
                    f"详细日志：{data_dir / 'logs' / 'desktop.log'}\n"
                    f"（也可能是端口被防火墙拦了：{base}）")
            _log(data_dir, "❌ " + note.replace("\n", " "))
            if not args.check:       # 自检模式别弹框：自动化跑的时候会卡在那儿等人点确定
                _msgbox("艾琳老师 启动失败", note)
            return 2
        _log(data_dir, f"服务就绪：{base}")

    core.save_pos(_lock_path(data_dir), {"pid": os.getpid(), "port": port, "base": base,
                                         "started": time.time()})

    if args.check:
        import httpx
        try:
            st = httpx.get(base + "/agent/pet/status", timeout=5).json()
        except Exception as exc:
            st = {"error": str(exc)}
        _log(data_dir, "自检结果：" + str(st))
        _log(data_dir, "浏览器：" + str(core.find_app_browser() or "（没有，会退回默认浏览器）"))
        # 小窗参数也打出来：验收时不用猜「它到底打算放哪儿、多大多小」
        try:
            _log(data_dir, "小窗参数：" + str(pet_window_payload(base, data_dir)))
        except Exception as exc:
            _log(data_dir, "小窗参数算不出来：" + str(exc))
        if server is not None:
            server.should_exit = True
        return 0

    opened_as = "skipped"
    if not args.no_window:
        opened_as = open_app_window(base, profile=data_dir / "browser")
        _log(data_dir, f"应用窗口已打开（{opened_as}{'：无地址栏' if opened_as == 'app' else '：退回了默认浏览器'}）")

    # 全局快捷键「看一眼屏幕」：跑在**主进程**的一条消息循环线程里，不创建任何窗口
    # （学生说过"别开那么多窗口"）。注册不上只写日志，不影响其它功能。
    try:
        from desktop import hotkey as hotkey_mod

        from kernel.config import load_app_config

        app_cfg = load_app_config()
        hot = str(app_cfg.get("vision_hotkey") or "ctrl+shift+t")
        enabled = str(app_cfg.get("vision_provider") or "local") != "off"
        watcher = hotkey_mod.start_watcher(base, data_dir, hot, enabled=enabled)
        _log(data_dir, f"看屏幕快捷键：{hot} —— " + ("已就绪（按一下她就会看你的屏幕）" if watcher else "没起来，见上面的日志"))
    except Exception as exc:  # noqa: BLE001
        _log(data_dir, f"快捷键线程启动失败：{type(exc).__name__}: {exc}")

    rc = 0
    if args.no_pet:
        _log(data_dir, "（--no-pet：不起桌宠）按 Ctrl+C 结束")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        # 桌面小窗 = 同一只 Live2D（/agent/pet?win=1）。
        # 关掉小窗就一起收摊：不然启动器会变成一个看不见的常驻进程。
        info = start_pet_window(base, data_dir)
        _log(data_dir, f"桌宠小窗已就位（{info['mode']}）：{info['url']} "
                       f"位置={info['pos']} 尺寸={info['size']} 置顶={info['always_top']}")
        try:
            how = wait_pet_window_closed(base)
            _log(data_dir, "桌宠小窗已关闭（" + how + "）" + ("——先不退出，等你手动结束" if how == "absent" else ""))
            if how == "absent":
                while True:
                    time.sleep(1)
        except KeyboardInterrupt:
            pass

    if server is not None:
        server.should_exit = True
        time.sleep(0.6)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
