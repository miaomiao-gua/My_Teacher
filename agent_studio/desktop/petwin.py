"""真·透明桌宠窗口：用 Windows 的**分层窗口**（per-pixel alpha）贴一只 Live2D 上去。

为什么不用 tkinter：
tkinter 的透明只有 `-transparentcolor`（色键）—— 只有**完全等于**那个颜色的像素才透明，
角色抗锯齿的边缘会糊上一圈色边。分层窗口（`UpdateLayeredWindow` + `AC_SRC_ALPHA`）
是**逐像素 alpha**，边缘干净，这才是桌宠该有的样子。

几个关键点（都是 Windows 上的硬知识）：
1. 位图必须**预乘 alpha**（B,G,R 都乘 a/255），否则半透明区域会发亮/发白。
2. 透明的地方要**点得穿**：`WM_NCHITTEST` 返回 `HTTRANSPARENT`，
   判断依据就是那一帧在该点的 alpha —— 这样学生点角色旁边的桌面不会被挡住。
3. `WS_EX_TOOLWINDOW` 让它不出现在 Alt+Tab 里（桌宠不该占一个任务栏位置）。
4. 帧率自己控制：抓不到帧时就用**上次那帧**，绝不让窗口闪白。
"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

# ---- Win32 常量 ----
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
SW_SHOWNA = 8
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
HTTRANSPARENT = -1
HTCLIENT = 1
WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_NCHITTEST = 0x0084
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_MOUSEMOVE = 0x0200
WM_RBUTTONUP = 0x0205
WM_COMMAND = 0x0111
WM_APP = 0x8000
WM_QUIT_APP = WM_APP + 1
PM_REMOVE = 0x0001


def premultiply(bgra: bytes) -> bytes:
    """把 BGRA（非预乘）转成预乘 alpha —— UpdateLayeredWindow 要的就是这个。

    纯函数：单测直接喂几个已知像素就能验（不透明=原样，半透明=各通道乘 a/255）。
    """
    out = bytearray(len(bgra))
    for i in range(0, len(bgra), 4):
        b, g, r, a = bgra[i], bgra[i + 1], bgra[i + 2], bgra[i + 3]
        if a == 255:
            out[i:i + 4] = bytes((b, g, r, a))
        elif a == 0:
            out[i:i + 4] = b"\x00\x00\x00\x00"
        else:
            out[i] = (b * a) // 255
            out[i + 1] = (g * a) // 255
            out[i + 2] = (r * a) // 255
            out[i + 3] = a
    return bytes(out)


def hit_test(alpha_at: Callable[[int, int], int], x: int, y: int, threshold: int = 24) -> int:
    """鼠标在这个点上要不要"点得穿"。

    `alpha_at(x, y)` 返回该点透明度（0~255）。透明的地方返回 HTTRANSPARENT（点得穿），
    角色本体返回 HTCLIENT（能抓住拖）。
    """
    try:
        a = alpha_at(x, y)
    except Exception:
        return HTCLIENT
    return HTCLIENT if a >= threshold else HTTRANSPARENT


class LivePetWindow:
    """一只浮在桌面上的透明桌宠。`frame_getter()` 要返回 PIL RGBA 图像。"""

    def __init__(self, frame_getter: Callable[[], Any], pos: Tuple[int, int],
                 size: Tuple[int, int], *, on_open_app: Optional[Callable[[], None]] = None,
                 on_quit: Optional[Callable[[], None]] = None,
                 on_look_screen: Optional[Callable[[], None]] = None,
                 on_chat: Optional[Callable[[], None]] = None,
                 data_dir: Optional[Path] = None, fps: float = 10.0,
                 title: str = "MyTeacherPet") -> None:
        self.frame_getter = frame_getter
        self.pos = pos
        self.size = size
        self.on_open_app = on_open_app or (lambda: None)
        self.on_quit = on_quit or (lambda: None)
        self.on_look_screen = on_look_screen or (lambda: None)
        self.on_chat = on_chat or (lambda: None)
        self.data_dir = data_dir
        self.interval = max(40, int(1000.0 / max(1.0, fps)))
        self.title = title

        self.hwnd: Optional[int] = None
        self._bgra: bytes = b""
        self._alpha_bytes: bytes = b""
        self._w, self._h = size
        self._dragging = False
        self._drag_off = (0, 0)
        self._menu_ids: list[Tuple[int, str, Callable[[], None]]] = []
        self._running = threading.Event()
        self._ready = threading.Event()
        self.last_frame_ms = 0.0
        self.frame_count = 0
        self.last_error = ""

    # ---------------- 帧 ----------------
    def _compose(self) -> Any:
        """拿一帧（失败就用上次的），转成预乘 BGRA。"""
        t0 = time.time()
        img = None
        try:
            img = self.frame_getter()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
        if img is None:
            return False
        try:
            from PIL import Image, ImageChops

            if img.mode != "RGBA":
                img = img.convert("RGBA")
            if img.size != (self._w, self._h):
                self._w, self._h = img.size
            # ★ 预乘 alpha 必须用 Pillow 的 C 实现（ImageChops.multiply = 通道 × alpha / 255）。
            #   我第一版写了个 Python 逐像素循环 —— 340×460 就是 15 万次迭代，
            #   一帧 200ms+，桌宠直接变成幻灯片（抓帧本身只要 55ms，白白浪费）。
            a = img.getchannel("A")
            prem = Image.merge("RGBA", (ImageChops.multiply(img.getchannel("R"), a),
                                        ImageChops.multiply(img.getchannel("G"), a),
                                        ImageChops.multiply(img.getchannel("B"), a),
                                        a))
            self._alpha_bytes = a.tobytes()
            self._bgra = prem.tobytes("raw", "BGRA")
            self.last_frame_ms = (time.time() - t0) * 1000
            self.frame_count += 1
            return True
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"预处理帧失败：{exc}"
            return False

    def alpha_at(self, x: int, y: int) -> int:
        """这一点的 alpha（给点穿判断用）"""
        try:
            if x < 0 or y < 0 or x >= self._w or y >= self._h:
                return 0
            return self._alpha_bytes[y * self._w + x]
        except Exception:
            return 255

    def _paint(self) -> None:
        """把当前 BGRA 贴到分层窗口上。"""
        if not self.hwnd or not self._bgra:
            return
        _t0 = time.time()
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        class BITMAPINFO(ctypes.Structure):
            _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]

        screen_dc = user32.GetDC(0)
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self._w
        bmi.bmiHeader.biHeight = -self._h          # 负 = 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0            # BI_RGB
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0,
                                      ctypes.byref(bits), None, 0)
        try:
            ctypes.memmove(bits, self._bgra, len(self._bgra))
            old = gdi32.SelectObject(mem_dc, hbmp)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            size = wintypes.SIZE(self._w, self._h)
            src = wintypes.POINT(0, 0)
            dst = wintypes.POINT(self.pos[0], self.pos[1])
            user32.UpdateLayeredWindow(self.hwnd, screen_dc, ctypes.byref(dst),
                                       ctypes.byref(size), mem_dc, ctypes.byref(src),
                                       0, ctypes.byref(blend), ULW_ALPHA)
            self.paint_ms = (time.time() - _t0) * 1000
            gdi32.SelectObject(mem_dc, old)
        finally:
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(mem_dc)
            user32.ReleaseDC(0, screen_dc)

    # ---------------- 菜单 ----------------
    def _build_menu(self) -> None:
        """右键菜单（Win32 原生菜单，不用 tkinter —— 这个窗口里没有 tk）"""
        self._menu_ids = [
            (1, "打开工作台", self.on_open_app),
            (2, "跟艾琳说句话…", self.on_chat),
            (3, "看一眼屏幕", self.on_look_screen),
            (4, "回到右下角", self.reset_pos),
            (5, "退出", self.on_quit),
        ]

    def _popup_menu(self) -> None:
        user32 = ctypes.windll.user32
        hmenu = user32.CreatePopupMenu()
        for mid, label, _fn in self._menu_ids:
            user32.AppendMenuW(hmenu, 0x0000, mid, label)
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(hmenu, 0x0100, pt.x, pt.y, 0, self.hwnd, None)  # TPM_RETURNCMD
        user32.DestroyMenu(hmenu)
        for mid, _label, fn in self._menu_ids:
            if mid == cmd:
                try:
                    fn()
                except Exception:
                    pass
                break

    def reset_pos(self) -> None:
        user32 = ctypes.windll.user32
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        self.move(sw - self._w - 40, sh - self._h - 90)

    def move(self, x: int, y: int) -> None:
        self.pos = (int(x), int(y))
        if self.hwnd:
            ctypes.windll.user32.SetWindowPos(self.hwnd, -1, self.pos[0], self.pos[1],
                                              self._w, self._h, 0x0010)   # SWP_NOACTIVATE
        self._paint()

    # ---------------- 消息循环 ----------------
    def run(self) -> None:
        """在当前线程里跑窗口 + 消息循环（阻塞）。"""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        # 每个显示器独立 DPI：不然 150% 缩放下这张位图会被系统再拉一遍，糊
        try:
            ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)   # PER_MONITOR_AWARE_V2
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass

        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM)

        # ★ 必须显式声明原型：64 位下 lparam/handle 是 64 位，
        #   而 ctypes 默认按 c_int 传 —— 会报 "int too long to convert"
        #   （我第一版就是这样，窗口能用但每次消息都往 stderr 刷一堆异常）。
        LRESULT = ctypes.c_longlong
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = LRESULT
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user32.GetMessageW.restype = ctypes.c_int
        user32.TranslateMessage.argtypes = [ctypes.c_void_p]
        user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.SetTimer.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.UINT, ctypes.c_void_p]
        user32.GetCursorPos.argtypes = [ctypes.c_void_p]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.TrackPopupMenu.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
        user32.TrackPopupMenu.restype = ctypes.c_int
        user32.AppendMenuW.argtypes = [wintypes.HANDLE, wintypes.UINT, ctypes.c_uint, wintypes.LPCWSTR]
        user32.CreatePopupMenu.restype = wintypes.HANDLE
        user32.DestroyMenu.argtypes = [wintypes.HANDLE]

        self._wndproc_ref = None

        def wndproc(hwnd, msg, wparam, lparam):  # noqa: ANN001
            if msg == WM_NCHITTEST:
                # 透明的地方点得穿：坐标是屏幕坐标，要换成窗口内坐标
                x = ctypes.c_short(lparam & 0xFFFF).value - self.pos[0]
                y = ctypes.c_short((lparam >> 16) & 0xFFFF).value - self.pos[1]
                return hit_test(self.alpha_at, x, y)
            if msg == WM_LBUTTONDOWN:
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                self._dragging = True
                self._drag_off = (pt.x - self.pos[0], pt.y - self.pos[1])
                return 0
            if msg == WM_MOUSEMOVE and self._dragging:
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                self.move(pt.x - self._drag_off[0], pt.y - self._drag_off[1])
                return 0
            if msg == WM_LBUTTONUP:
                self._dragging = False
                self._save_pos()
                return 0
            if msg == WM_LBUTTONDBLCLK:
                try:
                    self.on_open_app()
                except Exception:
                    pass
                return 0
            if msg == WM_RBUTTONUP:
                self._popup_menu()
                return 0
            if msg == WM_TIMER:
                if self._compose():
                    self._paint()
                return 0
            if msg == WM_QUIT_APP:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        proc = WNDPROC(wndproc)
        self._wndproc_ref = proc                     # 必须留引用，否则回调被回收 → 崩

        class WNDCLASSEX(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                        ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                        ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                        ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]

        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.style = 0
        wc.lpfnWndProc = proc
        wc.hInstance = hinst
        wc.lpszClassName = self.title
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            self.last_error = f"RegisterClassEx 失败：{ctypes.get_last_error()}"
            return

        ex_style = WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
        hwnd = user32.CreateWindowExW(ex_style, self.title, self.title, WS_POPUP,
                                      self.pos[0], self.pos[1], self._w, self._h,
                                      None, None, hinst, None)
        if not hwnd:
            self.last_error = f"CreateWindowEx 失败：{ctypes.get_last_error()}"
            return
        self.hwnd = hwnd
        self._build_menu()
        user32.ShowWindow(hwnd, SW_SHOWNA)
        # 先画一帧再开计时器，避免窗口先闪一个空白
        if self._compose():
            self._paint()
        user32.SetTimer(hwnd, 1, self.interval, None)
        self._running.set()
        self._ready.set()

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._running.clear()

    def start_in_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run, daemon=True, name="pet-window")
        t.start()
        self._ready.wait(timeout=8.0)
        return t

    def stop(self) -> None:
        try:
            if self.hwnd:
                ctypes.windll.user32.PostMessageW(self.hwnd, WM_QUIT_APP, 0, 0)
        except Exception:
            pass

    def _save_pos(self) -> None:
        if not self.data_dir:
            return
        try:
            from desktop import core

            core.save_pos(self.data_dir / "pet_state.json",
                          {"x": self.pos[0], "y": self.pos[1], "screen": [self._w, self._h]})
        except Exception:
            pass
