"""全局快捷键：按一下就"看一眼屏幕"。

为什么单独一个文件、而且是**线程**而不是一个窗口：
- 学生要的是"按快捷键她就能看屏幕"，这需要**全局**热键 —— 网页/Browser 里做不到
  （页面只有获得焦点时才知道按键）。所以必须有个原生部分。
- 但这个原生部分**不该多开窗口**（学生明确说过"别开那么多"）。所以它只是一条
  消息循环线程，跑在主进程里，连一个窗口都不创建。
- 看完的答案走**桌宠总线**（服务端 publish），所以网页版小玩偶和桌面小窗的气泡
  会同时显示同一句话 —— 一份文案，两处显示。

失败不致命：注册不上（被别的软件占了 / 设置里写错了）就写一行日志，
菜单里还有「看一眼屏幕」可以手点。
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import core

WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001


def _grab_and_ask(base: str, data_dir: Path, question: str = "") -> dict:
    """截屏 → 交给能看图的模型 → 返回结果。**在后台线程里跑**，不阻塞热键循环。"""
    from kernel import vision

    try:
        png = vision.grab_screen()
        b64, meta = vision.prepare_image(png)
        core.log_line(data_dir, f"看屏幕：{meta['from']} → {meta['to']}，{meta['kb']}KB")
    except Exception as exc:  # noqa: BLE001
        core.log_line(data_dir, f"截屏失败：{type(exc).__name__}: {exc}")
        return {"ok": False, "answer": f"截屏失败了：{exc}"}

    try:
        result = asyncio.run(vision.ask(b64, question))
    except Exception as exc:  # noqa: BLE001
        result = {"ok": False, "answer": f"看图失败：{exc}"}

    # 推给桌宠总线：网页版小玩偶 + 桌面小窗的气泡都会显示这句话
    try:
        import httpx

        text = str(result.get("answer") or "")[:120]
        httpx.post(f"{base}/agent/pet/notice", json={"text": text}, timeout=10.0)
    except Exception:
        pass
    core.log_line(data_dir, "看屏幕结果：" + str(result.get("answer"))[:80].replace("\n", " "))
    return result


def start_watcher(base: str, data_dir: Path, hotkey: str, enabled: bool = True) -> Optional[threading.Thread]:
    """注册全局热键并起一条消息循环线程。返回线程（没起来就返回 None）。"""
    if not enabled:
        core.log_line(data_dir, "看屏幕快捷键：设置里关着，不注册")
        return None
    parsed = core.parse_hotkey(hotkey)
    if not parsed:
        core.log_line(data_dir, f"快捷键「{hotkey}」看不懂（要像 ctrl+alt+s），已跳过")
        return None
    mods, vk, hid = parsed
    import os

    if os.name != "nt":
        core.log_line(data_dir, "不是 Windows，跳过全局快捷键")
        return None

    state: dict[str, Any] = {"registered": False, "busy": False}

    def loop() -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        if not user32.RegisterHotKey(None, hid, mods, vk):
            core.log_line(data_dir,
                          f"快捷键 {core.hotkey_label(hotkey)} 注册失败（多半被别的软件占了）"
                          "；可以右键小玩偶 → 看一眼屏幕，或在设置里换一个")
            return
        state["registered"] = True
        core.log_line(data_dir, f"快捷键已就绪：{core.hotkey_label(hotkey)} → 看一眼屏幕")

        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                        ("time", wintypes.DWORD), ("pt_x", wintypes.LONG), ("pt_y", wintypes.LONG)]

        msg = MSG()
        try:
            while True:
                # GetMessage 会阻塞，不占 CPU；收到热键就干活
                got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if got in (0, -1):        # WM_QUIT / 出错
                    break
                if msg.message == WM_HOTKEY and int(msg.wParam) == hid:
                    if state["busy"]:
                        continue          # 上一次还没看完，别叠着截屏
                    state["busy"] = True

                    def job() -> None:
                        try:
                            _grab_and_ask(base, data_dir)
                        finally:
                            state["busy"] = False

                    threading.Thread(target=job, daemon=True, name="pet-look").start()
        finally:
            try:
                user32.UnregisterHotKey(None, hid)
            except Exception:
                pass

    t = threading.Thread(target=loop, daemon=True, name="pet-hotkey")
    t.start()
    time.sleep(0.4)                 # 给它一点时间注册，好让日志先出现
    state["registered"] = state["registered"]
    return t


def encode_for_test(image_bytes: bytes) -> Any:
    """给测试用的薄封装：把一张图压成模型要的 base64。"""
    from kernel import vision

    return vision.prepare_image(image_bytes)
