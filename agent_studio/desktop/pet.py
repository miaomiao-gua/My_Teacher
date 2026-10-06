"""桌宠窗口：一只趴在桌面上、会看你干活的角色。

用 tkinter（stdlib，零新依赖）实现。几个实现要点，都是踩过才明白的：

1. **透明**：`-transparentcolor` 只认「正好等于色键」的像素，抗锯齿边缘会渗出一圈品红边
   （探针截图确认过）。所以形象由 `pet_art.flatten_for_key()` 把边缘压成描边色再上屏。
2. **气泡和聊天要分开**：气泡是「一张图」（PIL 画好再贴，文字才平滑），
   聊天窗是普通窗口（有标题栏，能移动/关闭，背景不透明所以没有色边问题）。
3. tkinter 不是线程安全的：网络线程只往 queue 里塞，**所有 UI 更新都回到主线程**（after 轮询）。
4. 气泡说话很短（一句话），细节在主界面看 —— 否则桌宠会变成弹幕。
"""

from __future__ import annotations

import json
import os
import queue
import random
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from desktop import core
from desktop.pet_art import KEY, PALETTE, flatten_for_key, render_state

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
]

PALETTES = {
    "原色": {},
    "薄荷": {"body": "#d8f0e2", "body_dark": "#bde0cd", "belly": "#f2fbf6", "cap": "#2f6f6a"},
    "樱花": {"body": "#fbe3ea", "body_dark": "#f3cbd8", "belly": "#fff7fa", "cap": "#8a4a63"},
    "夜蓝": {"body": "#dfe6f7", "body_dark": "#c3cee8", "belly": "#f4f7ff", "cap": "#2c3860"},
}


def _font(size: int):
    from PIL import ImageFont
    for p in FONT_CANDIDATES:
        try:
            if Path(p).is_file():
                return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def render_bubble(text: str, *, max_width: int = 250, font_size: int = 13,
                  palette: Optional[dict] = None) -> "Any":
    """把一句话画成一个带小尾巴的气泡图（返回值是 PIL.Image，RGB + 色键底）"""
    from PIL import Image, ImageDraw

    pal = dict(PALETTE)
    pal.update(palette or {})
    font = _font(font_size)
    # 手动折行：先按字符量，逐字加宽（中文没有词边界，按宽度折最稳）
    lines: List[str] = []
    cur = ""
    tmp = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if tmp.textlength(cur + ch, font=font) > max_width:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    lines = lines[:6]

    lh = font_size + 7
    tw = int(max([tmp.textlength(l, font=font) for l in lines] + [40]))
    pad_x, pad_y = 14, 10
    w = tw + pad_x * 2
    h = lh * len(lines) + pad_y * 2
    tail_h = 10
    img = Image.new("RGB", (w, h + tail_h), (255, 0, 255))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=12, fill=(255, 255, 255),
                        outline=(201, 201, 201), width=1)
    # 小尾巴（指向下方的桌宠）
    d.polygon([(w // 2 - 10, h - 2), (w // 2 + 10, h - 2), (w // 2, h + tail_h - 1)],
              fill=(255, 255, 255), outline=(201, 201, 201))
    d.line([(w // 2 - 9, h - 1), (w // 2 + 9, h - 1)], fill=(255, 255, 255), width=2)
    for i, l in enumerate(lines):
        d.text((pad_x, pad_y + i * lh), l, font=font, fill=(27, 27, 27))
    return img


@dataclass
class PetConfig:
    api_base: str
    data_dir: Path
    on_open_app: Callable[[], None]
    on_quit: Callable[[], None]
    pet_scale: float = 1.0
    palette_name: str = "原色"
    hotkey: str = "ctrl+alt+s"          # 看一眼屏幕的全局快捷键

    @property
    def hotkey_label(self) -> str:
        """给人看的写法：ctrl+alt+s → Ctrl+Alt+S"""
        parts = [p.strip() for p in str(self.hotkey or "").split("+") if p.strip()]
        return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in parts) or "未设置"


HOTKEY_ID = 0xA31E          # 随便选的固定 id：只给自己用，避免和别的程序撞


class PetApp:
    def __init__(self, cfg: PetConfig) -> None:
        self.cfg = cfg
        self.pos_file = cfg.data_dir / "pet_state.json"
        self.saved = core.load_pos(self.pos_file)
        self.signals = core.PetSignals()
        self.events: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.chat_lines: "queue.Queue[str]" = queue.Queue()
        self.sprite_cache: Dict[str, Any] = {}
        self.palette_name = str(self.saved.get("palette") or cfg.palette_name)
        self._stop = threading.Event()

        self.root = tk.Tk()
        self.root.withdraw()                     # 主窗不用，只要事件循环
        try:
            self.root.title("艾琳老师")
        except Exception:
            pass

        self.ww = int((core.PET_W + core.PET_MARGIN * 2) * cfg.pet_scale)
        self.hh = int((core.PET_H + core.PET_MARGIN * 2) * cfg.pet_scale)
        self._build_window()
        self._build_menu()
        self._build_sprites()

        x, y = self._initial_pos()
        self.win.geometry(f"{self.ww}x{self.hh}+{x}+{y}")

        self.state = "idle"
        self.blink_at = time.time() + random.uniform(2, 5)
        self.bubble: Optional[tk.Toplevel] = None
        self.bubble_hide_at = 0.0
        self.chat_win: Optional[tk.Toplevel] = None
        self.chat_text = None
        self.chat_entry = None
        self._last_state_write = 0.0

        threading.Thread(target=self._event_loop, daemon=True, name="pet-events").start()
        self.root.after(150, self._tick)
        # 起手打个招呼（顺便证明它活着）
        self.root.after(900, lambda: self.say("我在呢，双击我打开工作台"))

    def look_at_screen(self, question: str = "") -> None:
        """「看一眼屏幕」：截屏在**服务端**做（它就在同一台机器上），这里只发一个请求。

        答案会通过桌宠总线回到气泡里（服务端 publish），所以这里不用等结果。
        全局快捷键在 desktop/hotkey.py（主进程里的消息循环线程，不开窗口）。
        """
        self.say("我看看屏幕上是什么…", 30)

        def job() -> None:
            try:
                import httpx

                httpx.post(f"{self.cfg.api_base}/agent/vision/ask",
                           json={"question": question}, timeout=180.0)
            except Exception as exc:  # noqa: BLE001
                self.events.put({"type": "alert", "data": {"text": f"看屏幕失败：{exc}"}})

        threading.Thread(target=job, daemon=True, name="pet-look").start()

    # ---------------- 窗口 ----------------
    def _build_window(self) -> None:
        w = tk.Toplevel(self.root)
        w.overrideredirect(True)
        try:
            w.attributes("-topmost", True)
        except Exception:
            pass
        try:
            w.attributes("-transparentcolor", KEY)
        except Exception:
            w.configure(bg="#f3f3f3")            # 不支持透明就退化成小方块，至少能用
        w.configure(bg=KEY)
        self.win = w
        self.canvas = tk.Canvas(w, width=self.ww, height=self.hh, bg=KEY,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Button-3>", self._menu)
        self.canvas.bind("<Double-Button-1>", lambda e: self.cfg.on_open_app())

    def _build_menu(self) -> None:
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="打开工作台", command=self.cfg.on_open_app)
        m.add_command(label="跟艾琳说句话…", command=self.open_chat)
        m.add_command(label=f"看一眼屏幕（{self.cfg.hotkey_label}）", command=self.look_at_screen)
        m.add_separator()
        m.add_command(label="20 分钟后提醒我", command=lambda: self.set_reminder(20))
        m.add_command(label="1 小时后提醒我", command=lambda: self.set_reminder(60))
        m.add_separator()
        m.add_command(label="回到右下角", command=self.reset_pos)
        pal_menu = tk.Menu(m, tearoff=0)
        for name in PALETTES:
            pal_menu.add_command(label=name, command=lambda n=name: self.set_palette(n))
        m.add_cascade(label="换个配色", menu=pal_menu)
        m.add_separator()
        m.add_command(label="隐藏 10 分钟", command=self.hide_for_a_while)
        m.add_command(label="退出", command=self.quit)
        self.menu = m

    def _build_sprites(self) -> None:
        from PIL import ImageTk
        pal = PALETTES.get(self.palette_name, {})
        self.sprite_cache.clear()
        for st in ["idle", "blink", "happy", "think", "sleepy", "alert"]:
            img = render_state(st, palette=pal)
            if self.cfg.pet_scale != 1.0:
                img = img.resize((int(img.width * self.cfg.pet_scale),
                                  int(img.height * self.cfg.pet_scale)))
            flat = flatten_for_key(img)
            self.sprite_cache[st] = ImageTk.PhotoImage(flat)
        self.img_id = self.canvas.create_image(self.ww // 2, self.hh // 2,
                                               image=self.sprite_cache["idle"])

    # ---------------- 位置 ----------------
    def _initial_pos(self):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = self.saved.get("x")
        y = self.saved.get("y")
        if x is None or y is None:
            x, y = core.default_position(sw, sh, self.ww, self.hh)
        x, y = core.clamp_to_screen(int(x), int(y), self.ww, self.hh, sw, sh)
        return x, y

    def reset_pos(self) -> None:
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = core.default_position(sw, sh, self.ww, self.hh)
        self.win.geometry(f"+{x}+{y}")
        self._save_pos()

    def _save_pos(self) -> None:
        self.saved = core.save_pos(self.pos_file, {
            "x": self.win.winfo_x(), "y": self.win.winfo_y(),
            "palette": self.palette_name,
        })

    # ---------------- 鼠标 ----------------
    def _press(self, e) -> None:
        self._drag_from = (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y())
        self._moved = 0
        self.signals.last_touch = time.time()
        self.signals.awake_after = time.time() + 5

    def _drag(self, e) -> None:
        if not getattr(self, "_drag_from", None):
            return
        sx, sy, wx, wy = self._drag_from
        dx, dy = e.x_root - sx, e.y_root - sy
        self._moved = max(self._moved, abs(dx) + abs(dy))
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        nx, ny = core.clamp_to_screen(wx + dx, wy + dy, self.ww, self.hh, sw, sh)
        self.win.geometry(f"+{nx}+{ny}")

    def _release(self, e) -> None:
        moved = getattr(self, "_moved", 0)
        self._drag_from = None
        if moved is None or moved < 6:
            # 没拖动 = 点一下 → 打开工作台，并开心一下（有反馈才像活的）
            self.signals.happy_until = time.time() + 1.6
            self.cfg.on_open_app()
        else:
            self._save_pos()

    def _menu(self, e) -> None:
        self.signals.last_touch = time.time()
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    # ---------------- 气泡 ----------------
    def say(self, text: str, seconds: float = 6.0) -> None:
        text = (text or "").strip()
        if not text:
            return
        try:
            from PIL import ImageTk
            img = render_bubble(text, palette=PALETTES.get(self.palette_name, {}))
            self._bubble_img = ImageTk.PhotoImage(img)      # 必须留引用，否则会被回收
        except Exception:
            return
        if self.bubble is None or not self.bubble.winfo_exists():
            b = tk.Toplevel(self.root)
            b.overrideredirect(True)
            try:
                b.attributes("-topmost", True)
                b.attributes("-transparentcolor", KEY)
            except Exception:
                pass
            b.configure(bg=KEY)
            c = tk.Canvas(b, width=img.width, height=img.height, bg=KEY,
                          highlightthickness=0, bd=0)
            c.pack()
            c.bind("<Button-1>", lambda e: self.cfg.on_open_app())
            self.bubble = b
            self._bubble_canvas = c
            self._bubble_item = c.create_image(0, 0, anchor="nw", image=self._bubble_img)
        else:
            self._bubble_canvas.itemconfigure(self._bubble_item, image=self._bubble_img)
            self._bubble_canvas.configure(width=img.width, height=img.height)
            self.bubble.geometry(f"{img.width}x{img.height}")
        # 气泡放在头顶上方，尽量不出屏
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        bx = self.win.winfo_x() + self.ww // 2 - img.width // 2
        by = self.win.winfo_y() - img.height - 2
        bx = max(4, min(sw - img.width - 4, bx))
        by = max(4, by)
        self.bubble.geometry(f"{img.width}x{img.height}+{bx}+{by}")
        self.bubble.deiconify()
        self.bubble.lift()
        self.bubble_hide_at = time.time() + seconds

    def _hide_bubble(self) -> None:
        if self.bubble is not None and self.bubble.winfo_exists():
            self.bubble.withdraw()

    def hide_for_a_while(self, minutes: float = 10) -> None:
        self.win.withdraw()
        self._hide_bubble()
        self.root.after(int(max(1, minutes) * 60 * 1000), self._show_again)

    def _show_again(self) -> None:
        self.win.deiconify()
        self.say("我回来啦")

    def set_palette(self, name: str) -> None:
        if name not in PALETTES:
            return
        self.palette_name = name
        self._build_sprites()
        self._save_pos()
        self.say(f"换成「{name}」啦")

    # ---------------- 提醒 ----------------
    def set_reminder(self, minutes: int) -> None:
        try:
            import httpx
            r = httpx.post(f"{self.cfg.api_base}/agent/reminder",
                           json={"minutes": minutes, "text": "该复习啦"}, timeout=6)
            ok = r.status_code == 200
        except Exception:
            ok = False
        self.say(f"{minutes} 分钟后提醒你" if ok else "提醒没定上（服务没连上？）")

    # ---------------- 事件订阅 ----------------
    def _event_loop(self) -> None:
        """后台线程：订阅 /agent/pet/events，把事件塞进 queue（UI 只读 queue）"""
        import httpx
        while not self._stop.is_set():
            try:
                with httpx.stream("GET", f"{self.cfg.api_base}/agent/pet/events",
                                  timeout=httpx.Timeout(60.0, read=None)) as r:
                    for line in r.iter_lines():
                        if self._stop.is_set():
                            return
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            try:
                                self.events.put(json.loads(line[5:].strip()))
                            except Exception:
                                pass
            except Exception:
                time.sleep(3.0)          # 服务还没起来 / 断了 → 稍后重连

    # ---------------- 心跳 ----------------
    def _tick(self) -> None:
        if self._stop.is_set():
            return
        now = time.time()
        # 1) 事件 → 表情 + 气泡
        drained = 0
        while drained < 20:
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                break
            drained += 1
            self._handle_event(ev, now)
        # 2) 聊天流的内容
        while True:
            try:
                piece = self.chat_lines.get_nowait()
            except queue.Empty:
                break
            if self.chat_text is not None:
                try:
                    self.chat_text.configure(state="normal")
                    self.chat_text.insert("end", piece)
                    self.chat_text.see("end")
                    self.chat_text.configure(state="disabled")
                except Exception:
                    pass
        # 3) 表情切换
        blink_at = self.blink_at
        if now > blink_at and random.random() < 0.5:
            self.blink_at = now + random.uniform(2.5, 6.5)
            blink_at = now + 0.13
            self._blink_until = blink_at
        st = core.pet_state(now, self.signals, blink_at=getattr(self, "_blink_until", 0.0))
        if st != self.state:
            self.state = st
            try:
                self.canvas.itemconfigure(self.img_id, image=self.sprite_cache[st])
            except Exception:
                pass
        # 4) 小动作：常态下轻轻上下浮动（打盹时不动，看得出来它在睡）
        if st in ("idle", "happy"):
            import math
            dy = math.sin(now * 2.2) * 2.0 * self.cfg.pet_scale
            self.canvas.coords(self.img_id, self.ww // 2, self.hh // 2 + dy)
        else:
            self.canvas.coords(self.img_id, self.ww // 2, self.hh // 2)
        # 5) 气泡到点收起来
        if self.bubble_hide_at and now > self.bubble_hide_at:
            self.bubble_hide_at = 0.0
            self._hide_bubble()
        # 6) 状态落盘（桌面壳/自检靠它确认桌宠活着、在哪、什么表情）
        if now - self._last_state_write > 2.0:
            self._last_state_write = now
            core.save_pos(self.pos_file, {
                "pid": os.getpid(), "x": self.win.winfo_x(), "y": self.win.winfo_y(),
                "state": st, "ts": now, "palette": self.palette_name,
                "visible": bool(self.win.winfo_viewable()),
                # 记下 tk 眼里的屏幕尺寸：高分屏上这是「逻辑像素」，跟物理分辨率不一样，
                # 截图脚本要靠这两个数算缩放（否则按逻辑坐标去截物理屏会偏）。
                "screen": [self.root.winfo_screenwidth(), self.root.winfo_screenheight()],
                "pet_size": [self.ww, self.hh],
            })
        self.root.after(120, self._tick)

    def _handle_event(self, ev: Dict[str, Any], now: float) -> None:
        t = str(ev.get("type") or "")
        if t == "run.start":
            self.signals.thinking = True
        elif t == "run.end":
            self.signals.thinking = False
            self.signals.happy_until = now + 3.5
        elif t == "quiz":
            self.signals.happy_until = now + 4.0
        elif t == "alert":
            self.signals.alert_until = now + 6.0
        elif t == "reminder":
            self.signals.alert_until = now + 8.0
        msg = core.bubble_text_for(ev)
        if msg:
            self.say(msg)

    # ---------------- 小聊天窗 ----------------
    def open_chat(self) -> None:
        if self.chat_win is not None and self.chat_win.winfo_exists():
            self.chat_win.deiconify()
            self.chat_win.lift()
            return
        w = tk.Toplevel(self.root)
        w.title("跟艾琳老师说句话")
        w.geometry("380x300+%d+%d" % (max(0, self.win.winfo_x() - 260), self.win.winfo_y()))
        try:
            w.attributes("-topmost", True)
        except Exception:
            pass
        txt = tk.Text(w, wrap="word", height=12, font=("Microsoft YaHei UI", 10))
        txt.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        txt.configure(state="disabled")
        row = tk.Frame(w)
        row.pack(fill="x", padx=8, pady=8)
        entry = tk.Entry(row, font=("Microsoft YaHei UI", 10))
        entry.pack(side="left", fill="x", expand=True)
        send = tk.Button(row, text="发送")
        send.pack(side="left", padx=(6, 0))
        self.chat_win, self.chat_text, self.chat_entry = w, txt, entry
        self._chat_session: Optional[str] = None

        def do_send(_evt=None) -> None:
            msg = entry.get().strip()
            if not msg:
                return
            entry.delete(0, "end")
            self._append_chat(f"我：{msg}\n")
            threading.Thread(target=self._send_chat, args=(msg,), daemon=True).start()

        send.configure(command=do_send)
        entry.bind("<Return>", do_send)
        self._append_chat("（消息会接在你当前打开的对话里。想看得更全就点桌宠打开工作台。）\n\n")

    def _append_chat(self, text: str) -> None:
        self.chat_lines.put(text)

    def _pick_session(self) -> Optional[str]:
        """挑「最近更新的那个对话」——桌宠不知道主界面开着哪一条，用最近的在用对话最合理"""
        try:
            import httpx
            data = httpx.get(f"{self.cfg.api_base}/agent/sessions", timeout=8).json()
            best = None
            for subj in data.get("tree") or []:
                for book in subj.get("textbooks") or []:
                    for s in book.get("sessions") or []:
                        t = float(s.get("updated_at") or 0)
                        if best is None or t > best[0]:
                            best = (t, s.get("id"))
            return best[1] if best else None
        except Exception:
            return None

    def _send_chat(self, msg: str) -> None:
        import httpx
        sid = self._chat_session or self._pick_session()
        self._chat_session = sid
        body: Dict[str, Any] = {"message": msg}
        if sid:
            body["session_id"] = sid
        try:
            with httpx.stream("POST", f"{self.cfg.api_base}/agent/chat", json=body,
                              timeout=httpx.Timeout(600.0, read=None)) as r:
                for line in r.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except Exception:
                        continue
                    if ev.get("type") == "text":
                        self._append_chat(ev.get("delta") or "")
                    elif ev.get("type") == "error":
                        self._append_chat("\n[出错] " + str(ev.get("message"))[:200] + "\n")
                    elif ev.get("type") == "run.end":
                        self._append_chat("\n")
        except Exception as exc:
            self._append_chat(f"\n[连不上服务：{exc}]\n")

    # ---------------- 生命周期 ----------------
    def run(self) -> None:
        self.root.mainloop()

    def quit(self) -> None:
        self._stop.set()
        try:
            self._save_pos()
        except Exception:
            pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass
        self.cfg.on_quit()
