"""把浏览器里渲染的 Live2D 抓成**带透明通道**的 RGBA 帧。

为什么这么做（而不是让浏览器窗口自己透明）：Windows 上不可能让一个浏览器窗口
"只显示角色"——窗口总是有底、有边框。所以反过来：
**让 Edge 在无头模式下渲染，用 CDP 把画面抓出来（带 alpha），
再由我们自己的分层窗口贴到桌面上**。这样桌面上的东西就是真·透明的一只角色。

关键技术点（都实测过）：
1. `Emulation.setDefaultBackgroundColorOverride` 把页面底色设成**全透明**，
   然后 `Page.captureScreenshot(format='png')` 返回的 PNG **真的带 alpha**
   （这是 Playwright `omitBackground` 的同一招）。
2. 无头 Edge 要能跑 WebGL（Live2D 需要）：加 `--use-angle=swiftshader`
   `--enable-unsafe-swiftshader`，否则画布是空的。
3. 帧率靠"抓帧循环"控制。**实测（2026-10-07，340×460）**：
   抓一帧 `Page.captureScreenshot(fromSurface=True, optimizeForSpeed=True)` 中位 **69ms**，
   加上 base64 + PNG 解码（52ms）合计约 120ms → 约 8fps；
   空载时抓帧线程实测 74~93ms/帧（9~13fps），**机器有负载时（学生在开游戏）会掉到 274ms/帧（3fps）**。
   页面自己渲染是 70fps，所以瓶颈在"抓 + 解"，不在 Live2D。
   抓不动就降帧，不要卡死整机。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

DEFAULT_SIZE = (320, 430)


class FrameSource:
    """起一个无头 Edge 渲染桌宠页，然后按需吐 RGBA 帧。

    用法：
        src = FrameSource(url, size=(320, 430))
        src.start()                 # 起浏览器 + 建立 CDP 连接
        img = src.grab()            # PIL RGBA Image（拿不到就返回 None）
        src.stop()
    """

    def __init__(self, url: str, size: Tuple[int, int] = DEFAULT_SIZE,
                 profile: Optional[Path] = None, browser: Optional[str] = None,
                 fps: float = 12.0) -> None:
        self.url = url
        self.size = size
        self.profile = profile
        self.browser = browser
        self.fps = fps
        self.port = 0
        self.proc: Any = None
        self._ws: Any = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._id = 0
        self.last_error = ""
        self.frames = 0
        self.avg_ms = 0.0
        self.latest: Any = None
        self._stopping = False
        self._ready = threading.Event()
        self._thread: Any = None
        self._task: Any = None

    # ---------------- 启动 / 关闭 ----------------
    def start(self, timeout: float = 30.0) -> bool:
        import subprocess
        from desktop import core

        if not self.browser:
            self.browser = core.find_app_browser()
        if not self.browser:
            self.last_error = "没找到 Edge/Chrome"
            return False
        self.port = core.pick_free_port("127.0.0.1", 9400)
        profile = str(self.profile or (Path(self.url and ".") / "browser_frames"))
        args = [
            self.browser,
            "--headless=new",                     # 无窗口：学生看不到任何东西
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={profile}",
            f"--window-size={self.size[0]},{self.size[1]}",
            "--use-angle=swiftshader",            # 软件 WebGL（无头下没有真 GPU）
            "--enable-unsafe-swiftshader",
            "--disable-extensions", "--no-first-run", "--no-default-browser-check",
            "--hide-scrollbars", "--force-device-scale-factor=1",
            "--disable-features=Translate,DefaultBrowserSettingEnforcement",
            "about:blank",
        ]
        try:
            self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, close_fds=True)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"起浏览器失败：{exc}"
            return False

        # 等调试端口起来
        import httpx

        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(0.4)
            try:
                httpx.get(f"http://127.0.0.1:{self.port}/json/version", timeout=2.0)
                break
            except Exception:
                continue
        else:
            self.last_error = "调试端口没起来"
            return False

        # ★ 事件循环必须在**自己的线程里常驻**（`run_forever`），不能一次一个 run_until_complete：
        #   websockets 的保活/ping 是需要循环一直转的，只在我们抓帧时转一会儿，
        #   它就会以为对面死了 → 第二次抓帧永久挂住（实测：只抓到 1 帧就卡死）。
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._loop_main, daemon=True, name="pet-frames")
        self._thread.start()
        if not self._ready.wait(timeout=timeout):
            self.last_error = self.last_error or "抓帧线程没就绪"
            return False
        return not self.last_error

    # ---------------- 事件循环线程 ----------------
    def _loop_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect())
            self._task = self._loop.create_task(self._capture_loop())
            self._loop.run_forever()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _connect(self) -> None:
        import httpx
        import websockets

        t = httpx.put(f"http://127.0.0.1:{self.port}/json/new?" + self.url, timeout=10.0).json()
        ws_url = t.get("webSocketDebuggerUrl")
        self._ws = await websockets.connect(ws_url, max_size=64 * 1024 * 1024)
        await self._send("Page.enable")
        await self._send("Runtime.enable")
        # 视口定死成我们要的尺寸：`--window-size` 在无头模式下不一定生效
        # （实测第一次抓出来是 492×335，不是我要的 320×430），必须用 Emulation 覆盖。
        await self._send("Emulation.setDeviceMetricsOverride",
                         {"width": self.size[0], "height": self.size[1],
                          "deviceScaleFactor": 1, "mobile": False})
        # ★ 把页面底色设成全透明：抓出来的 PNG 才有 alpha
        await self._send("Emulation.setDefaultBackgroundColorOverride",
                         {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
        await asyncio.sleep(2.5)          # 等模型加载（Live2D 有 moc/贴图要读）
        self._ready.set()

    async def _send(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """发一条 CDP 命令并等回复（只在抓帧线程的事件循环里调用）。"""
        self._id += 1
        mid = self._id
        await self._ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=30.0)
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result") or {}

    async def _capture_loop(self) -> None:
        """不停地抓帧，存进 self.latest。窗口那头只管取最新的一张，互不阻塞。"""
        interval = 1.0 / max(1.0, self.fps)
        from PIL import Image

        while not self._stopping:
            t0 = time.time()
            try:
                result = await self._send("Page.captureScreenshot",
                                          {"format": "png", "fromSurface": True,
                                           "optimizeForSpeed": bool(getattr(self, "optimize_for_speed", True))})
                raw = base64.b64decode(result.get("data") or "")
                img = Image.open(io.BytesIO(raw)).convert("RGBA")
                self.latest = img
                self.frames += 1
                ms = (time.time() - t0) * 1000
                self.avg_ms = ms if not self.avg_ms else (self.avg_ms * 0.8 + ms * 0.2)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"抓帧失败：{type(exc).__name__}: {exc}"
                await asyncio.sleep(0.5)
            spent = time.time() - t0
            if spent < interval:
                await asyncio.sleep(interval - spent)

    # ---------------- 取帧 ----------------
    def grab(self) -> Any:
        """返回**最新**那张 RGBA 帧（PIL Image）。不阻塞 —— 抓帧在别的线程里循环。

        同一张图会被复用几次（窗口重绘比抓帧快时），所以调用方**不要改它**。
        """
        return self.latest

    def stop(self) -> None:
        self._stopping = True
        # ★ 先把抓帧任务取消掉再关循环。不取消的话，循环带着一个 pending task 结束，
        #   Python 会打一行 `Task was destroyed but it is pending!`（看着像崩溃，实际只是噪音）。
        if self._loop and self._task is not None:
            try:
                self._loop.call_soon_threadsafe(self._task.cancel)
                time.sleep(0.15)          # 给循环一点时间把取消跑完
            except Exception:
                pass
            self._task = None
        try:
            if self._loop and self._ws:
                fut = asyncio.run_coroutine_threadsafe(self._ws.close(), self._loop)
                fut.result(timeout=5)
        except Exception:
            pass
        try:
            if self._loop:
                self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass
        try:
            if self.proc:
                self.proc.terminate()
                self.proc.wait(timeout=8)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self._ws = None
        self._loop = None
