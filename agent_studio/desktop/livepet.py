"""桌面版桌宠 = Live2D 帧（无头 Edge 渲染）+ 逐像素透明的分层窗口。

为什么这么绕（学生选的就是这条"B 方案"）：
浏览器窗口不可能只显示角色（总有底、有边框），所以反过来 ——
**让无头 Edge 渲染 /agent/pet?frames=1，用 CDP 抓带 alpha 的 PNG，
再由我们的分层窗口贴到桌面上**。这样桌面上就是一只真·透明、没有窗口框的角色。

这个文件负责三件事：
1. 把 Live2D 帧和**气泡**合成到一张图上（气泡是 RGBA 画的，边缘干净）；
2. 订阅桌宠总线：有事件就把那句话显示在气泡里；
3. 抓不到帧时**降级**：用 Pillow 画的那只（pet_art）+ 简单眨眼，至少桌宠还在。

帧率：抓一帧 492×335 大约 30~100ms，所以 10fps 是现实值；抓不动就自动降帧，
绝不把机器拖卡（学生要拿这台机器学习）。
"""

from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from . import frames as frames_mod
from . import petwin

BUBBLE_SECONDS = 12.0
BUBBLE_MAX_W = 260


def render_bubble_rgba(text: str, *, max_width: int = BUBBLE_MAX_W,
                       font_size: int = 15, pad: int = 12) -> Any:
    """画一个**带 alpha** 的气泡（分层窗口用；不要用色键那版，会有色边）。

    返回 RGB 气球 + 尾巴的 PIL RGBA 图。手动折行（中文没有词边界，按宽度折最稳）。
    """
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc",
              "C:/Windows/Fonts/simhei.ttf"):
        try:
            if Path(p).is_file():
                font = ImageFont.truetype(p, font_size)
                break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    lines, cur = [], ""
    for ch in str(text or "").replace("\r", ""):
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if font.getlength(trial) > max_width - pad * 2 and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = trial
    lines.append(cur)
    lines = lines[:6] or [""]

    line_h = font_size + 7
    w = int(max(font.getlength(ln) for ln in lines)) + pad * 2
    h = line_h * len(lines) + pad * 2
    tail = 11
    img = Image.new("RGBA", (max(60, w), h + tail), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=14, fill=(255, 255, 255, 246),
                        outline=(31, 46, 77, 60), width=1)
    d.polygon([(w // 2 - 9, h - 2), (w // 2 + 9, h - 2), (w // 2, h + tail - 1)],
              fill=(255, 255, 255, 246))
    y = pad
    for ln in lines:
        d.text((pad, y), ln, font=font, fill=(24, 35, 58, 255))
        y += line_h
    return img


class LivePet:
    """把「Live2D 帧 + 气泡 + 透明窗口」拼成一只桌宠。"""

    def __init__(self, base: str, data_dir: Path, pos: Tuple[int, int],
                 size: Tuple[int, int] = (320, 430), *,
                 on_open_app: Optional[Callable[[], None]] = None,
                 on_chat: Optional[Callable[[], None]] = None,
                 on_quit: Optional[Callable[[], None]] = None,
                 fps: float = 10.0) -> None:
        self.base = base
        self.data_dir = data_dir
        self.pos = pos
        self.size = size
        self.on_open_app = on_open_app or (lambda: None)
        self.on_chat = on_chat or (lambda: None)
        self.on_quit = on_quit or (lambda: None)
        self.on_look_screen = self._ask_screen
        self.source: Optional[frames_mod.FrameSource] = None
        self.mode = "starting"            # live2d | pillow | failed
        self.bubble_text = ""
        self.bubble_until = 0.0
        self.events: "queue.Queue[dict]" = queue.Queue()
        self._last_frame: Any = None
        self._pillow_state = "idle"
        self._blink_at = time.time() + 3.0
        self.fps = fps
        self.window: Optional[petwin.LivePetWindow] = None
        self.last_note = ""

    # ---------------- 帧来源 ----------------
    def start_source(self) -> None:
        url = f"{self.base}/agent/pet?frames=1"
        src = frames_mod.FrameSource(url, size=self.size,
                                     profile=self.data_dir / "browser_frames", fps=self.fps)
        if src.start():
            self.source = src
            self.mode = "live2d"
            self.last_note = f"Live2D 帧：{src.size[0]}×{src.size[1]}"
        else:
            self.mode = "pillow"
            self.last_note = f"退回 Pillow 形象（{src.last_error}）"
        _log(self.data_dir, "桌宠形象：" + self.last_note)

    def _pillow_frame(self) -> Any:
        """降级用的静态形象（带眨眼/高兴表情），至少桌宠还在。"""
        from . import pet_art

        now = time.time()
        state = self._pillow_state
        if now > self._blink_at:
            self._blink_at = now + 3.5
            state = "blink"
        if now < getattr(self, "_happy_until", 0):
            state = "happy"
        return pet_art.render_state(state if state in pet_art.STATES else "idle")

    def frame(self) -> Any:
        """合成这一帧：角色 + （有气泡时）气泡。"""
        base = None
        if self.source is not None:
            base = self.source.grab()
            if base is None:
                # 抓帧线程要等模型加载（约 3~4 秒）才开始出帧 ——
                # **这期间"还没有帧"不算失败**（我第一版就是把这个当成失败，
                # 结果桌宠刚起来就自己降级成 Pillow 了，日志里还写着"Live2D 帧：340×460"，很误导）。
                misses = getattr(self, "_misses", 0) + 1
                self._misses = misses
                warming = self.source.frames == 0
                limit = 300 if warming else 25          # 预热给 ~30 秒，跑起来后再断 2.5 秒才算坏
                if misses > limit:
                    _log(self.data_dir, "抓帧一直拿不到，改用 Pillow 形象：" + self.source.last_error)
                    try:
                        self.source.stop()
                    except Exception:
                        pass
                    self.source = None
                    self.mode = "pillow"
                else:
                    return self._last_frame or self._pillow_frame()
            else:
                self._misses = 0
        if base is None:
            base = self._last_frame if self._was_live2d else self._pillow_frame()
            if self.source is None and self.mode != "pillow":
                self.mode = "pillow"
        else:
            self._was_live2d = True

        if self.bubble_text and time.time() < self.bubble_until:
            try:
                from PIL import Image

                bub = render_bubble_rgba(self.bubble_text)
                w = max(base.width, bub.width)
                canvas = Image.new("RGBA", (w, base.height + bub.height), (0, 0, 0, 0))
                canvas.alpha_composite(bub, ((w - bub.width) // 2, 0))
                canvas.alpha_composite(base, ((w - base.width) // 2, bub.height))
                self._last_frame = base
                return canvas
            except Exception as exc:  # noqa: BLE001
                _log(self.data_dir, f"画气泡失败：{type(exc).__name__}: {exc}")
        self._last_frame = base
        return base

    # ---------------- 事件 ----------------
    def say(self, text: str, seconds: float = BUBBLE_SECONDS) -> None:
        text = str(text or "").strip()
        if not text:
            return
        self.bubble_text = text
        self.bubble_until = time.time() + seconds

    def _ask_screen(self) -> None:
        """看一眼屏幕：截图交给服务端（服务端自己截），答案会通过总线回到气泡。"""
        self.say("我看看屏幕上是什么…", 25)

        def job() -> None:
            try:
                import httpx

                httpx.post(f"{self.base}/agent/vision/ask", json={}, timeout=240.0)
            except Exception as exc:  # noqa: BLE001
                self.say(f"看屏幕失败：{exc}", 10)

        threading.Thread(target=job, daemon=True, name="pet-look").start()

    def watch_bus(self) -> None:
        """订阅桌宠总线：事件里的 text 就是气泡要说的话（服务端一份文案）。"""
        def loop() -> None:
            while True:
                try:
                    import httpx

                    with httpx.stream("GET", f"{self.base}/agent/pet/events", timeout=None) as r:
                        for line in r.iter_lines():
                            if not line or not line.startswith("data:"):
                                continue
                            try:
                                import json

                                ev = json.loads(line[5:].strip())
                            except Exception:
                                continue
                            text = str(ev.get("text") or "").strip()
                            if text:
                                self.say(text)
                            if str(ev.get("type")) == "reminder":
                                self._happy_until = time.time() + 6
                except Exception:
                    time.sleep(3.0)

        threading.Thread(target=loop, daemon=True, name="pet-bus").start()

    # ---------------- 跑起来 ----------------
    def chat_window(self) -> None:
        """「跟艾琳说句话…」：一个小的 tkinter 聊天窗（气泡里也会显示她的回答）。"""
        import tkinter as tk

        if getattr(self, "_chat", None) is not None and self._chat.winfo_exists():
            self._chat.deiconify()
            self._chat.lift()
            return
        w = tk.Toplevel()
        w.title("跟艾琳老师说句话")
        w.geometry("380x320+%d+%d" % (max(0, self.pos[0] - 400), max(0, self.pos[1] - 60)))
        try:
            w.attributes("-topmost", True)
        except Exception:
            pass
        txt = tk.Text(w, wrap="word", height=12, font=("Microsoft YaHei UI", 10))
        txt.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        txt.insert("end", "（说话的声音会出现在桌宠的气泡里；她也记住了这个对话）\n")
        txt.configure(state="disabled")
        row = tk.Frame(w)
        row.pack(fill="x", padx=8, pady=8)
        entry = tk.Entry(row, font=("Microsoft YaHei UI", 10))
        entry.pack(side="left", fill="x", expand=True)
        entry.focus_set()

        def append(piece: str) -> None:
            try:
                txt.configure(state="normal")
                txt.insert("end", piece)
                txt.see("end")
                txt.configure(state="disabled")
            except Exception:
                pass

        def send(_ev: Any = None) -> None:
            text = entry.get().strip()
            if not text:
                return
            entry.delete(0, "end")
            append("我：" + text + "\n艾琳：")
            threading.Thread(target=self._ask, args=(text, append), daemon=True).start()

        btn = tk.Button(row, text="发送", command=send)
        btn.pack(side="left", padx=(6, 0))
        entry.bind("<Return>", send)
        self._chat, self._chat_text = w, txt

    def _ask(self, text: str, append: Callable[[str], None]) -> None:
        """走和网页版同一条 agent 链路；回答同时进聊天窗和气泡。"""
        import json

        import httpx

        try:
            sid = None
            try:
                data = httpx.get(f"{self.base}/agent/sessions", timeout=10.0).json()
                best = None
                for s in data.get("tree") or []:
                    for b in s.get("textbooks") or []:
                        for x in b.get("sessions") or []:
                            if not best or (x.get("updated_at") or 0) > (best.get("updated_at") or 0):
                                best = x
                sid = (best or {}).get("id")
            except Exception:
                pass
            body = {"message": text}
            if sid:
                body["session_id"] = sid
            buf = ""
            with httpx.stream("POST", f"{self.base}/agent/chat", json=body, timeout=None) as r:
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    if ev.get("type") == "text":
                        piece = str((ev.get("data") or {}).get("delta") or "")
                        buf += piece
                        append(piece)
                        self.say(buf[-160:], 12)          # 气泡跟着说
                    elif ev.get("type") == "notice":
                        msg = str((ev.get("data") or {}).get("message") or "")
                        if msg:
                            append("\n（" + msg + "）\n")
            append("\n")
            if not buf:
                self.say("（这次没答上来）", 8)
        except Exception as exc:  # noqa: BLE001
            append(f"\n(连不上服务：{exc})\n")

    def run(self) -> None:
        self.start_source()
        self.watch_bus()
        self.window = petwin.LivePetWindow(
            self.frame, self.pos, self.size,
            on_open_app=self.on_open_app, on_quit=self.on_quit,
            on_look_screen=self._ask_screen, on_chat=self.chat_window,
            data_dir=self.data_dir, fps=self.fps, title="MyTeacherPet")
        self.say("我在呢，右键我试试", 8)
        try:
            self.window.run()                      # 阻塞在消息循环里
        finally:
            try:
                if self.source:
                    self.source.stop()
            except Exception:
                pass


def _log(data_dir: Path, msg: str) -> None:
    from . import core

    core.log_line(data_dir, msg)
