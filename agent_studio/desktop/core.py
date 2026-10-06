"""桌面版的纯逻辑：端口、窗口参数、桌宠状态机、气泡文案、位置记忆。

**这里一行 tkinter 都不写** —— 目的是让这些逻辑能在没有显示器的环境里跑单测
（窗口本身只能靠截图看，所以把「会出错的判断」全部挤到这一层来验）。
"""

from __future__ import annotations

import glob
import json
import os
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 桌宠的尺寸（逻辑像素；实际显示会按系统缩放比例放大）
PET_W, PET_H = 128, 148
PET_MARGIN = 10          # 画布留边，给小动作（上下浮动）用


# ---------------------------------------------------------------------------
# 端口 / 单实例
# ---------------------------------------------------------------------------

def port_in_use(host: str, port: int) -> bool:
    """这个端口是不是被占了。

    两种判法都要用（实测教训）：
    - **能连上** → 一定有服务在监听；
    - **绑不上** → 也被占着（哪怕对方没在监听）。只靠 connect 会漏：
      监听队列被占满时 connect 会失败，于是「有人占着」被误判成「空闲」，
      启动器就会拿到一个根本绑不上的端口。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.35)
        if s.connect_ex((host, port)) == 0:
            return True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True


def pick_free_port(host: str = "127.0.0.1", preferred: int = 5001, tries: int = 40) -> int:
    """优先用 5001（老书签/习惯不变），被占了就往后找。

    刻意不选「让系统随机分配」：端口写死在界面上、用户下次还能用同一个地址打开。
    """
    for p in range(preferred, preferred + tries):
        if not port_in_use(host, p):
            return p
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


# ---------------------------------------------------------------------------
# 应用窗口（Edge 应用模式，无地址栏）
# ---------------------------------------------------------------------------

_EDGE_PATHS = [
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe",
]


def find_app_browser() -> Optional[str]:
    """找一个能用 `--app=` 的浏览器（Edge 优先，Chrome 也行）"""
    for tpl in _EDGE_PATHS:
        p = os.path.expandvars(tpl)
        if "*" in p:
            hits = glob.glob(p)
            if hits:
                return hits[0]
        elif Path(p).is_file():
            return p
    return None


def app_window_args(browser: str, url: str, profile_dir: Path,
                    size: Tuple[int, int] = (1440, 920)) -> List[str]:
    """应用模式窗口的参数：没有地址栏/标签栏，看起来就是个桌面软件。

    单独给一个 user-data-dir：不跟用户平时浏览的浏览器配置搅在一起
    （否则「关闭窗口」可能把他正在看的网页一起收了）。
    """
    return [
        browser,
        f"--app={url}",
        f"--window-size={size[0]},{size[1]}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,DefaultBrowserSettingEnforcement",
    ]


# ---------------------------------------------------------------------------
# 桌宠状态机
# ---------------------------------------------------------------------------

@dataclass
class PetSignals:
    """影响表情的信号（都由外部喂进来，方便单测）"""
    thinking: bool = False                 # 老师正在干活（run 进行中）
    happy_until: float = 0.0               # 开心到什么时候（答对/交卷）
    alert_until: float = 0.0               # 警觉到什么时候（需要确认/出错）
    last_touch: float = field(default_factory=time.time)   # 最后一次鼠标碰它
    awake_after: float = 0.0               # 被戳醒之后保持清醒到什么时候


def pet_state(now: float, sig: PetSignals, *, blink_at: float = 0.0,
              idle_sleep_after: float = 90.0) -> str:
    """决定此刻应该显示哪个表情。

    优先级：警觉 > 开心 > 思考 > 眨眼 > 打盹 > 常态。
    「打盹」只在真的没人理它一段时间后出现（默认 90 秒），不打扰干活。
    """
    if now < sig.alert_until:
        return "alert"
    if now < sig.happy_until:
        return "happy"
    if sig.thinking:
        return "think"
    if now < sig.awake_after:
        return "idle"
    if now < blink_at:                      # 眨眼是极短的一下
        return "blink"
    if now - max(sig.last_touch, sig.awake_after) > idle_sleep_after:
        return "sleepy"
    return "idle"


def bubble_text_for(event: Dict[str, Any], max_len: int = 90) -> str:
    """把一个后端事件翻成桌宠该说的一句话（纯函数，好测）。

    说得**短**是有意的：气泡只放一句，细节在主界面里看。
    """
    t = str(event.get("type") or "")
    d = event.get("data") or {}
    if t == "run.start":
        return "老师在想了…"
    if t == "run.end":
        n = d.get("steps")
        if d.get("reason") == "error":
            return "这次没跑起来，去主界面看看？"
        return f"讲完了（用了 {n} 步）" if n else "讲完了"
    if t == "tool.result":
        name = str(d.get("name") or "")
        if name in ("graph_calculator", "geometry_board", "data_lab", "solve_equation") and d.get("ok"):
            return "画好了，去工作台看"
        if name == "prepare_course" and d.get("ok"):
            return "课备好了！"
        if name == "show_artifact" and d.get("ok"):
            return "界面做好啦"
        if not d.get("ok"):
            return f"「{name}」没成功，我再试试"
        return ""
    if t == "quiz":
        pct = d.get("percent")
        if pct is None:
            return "交卷啦，我看看"
        return f"交卷：{pct} 分" + ("，不错！" if float(pct) >= 60 else "，我们再过一遍")
    if t == "reminder":
        return str(d.get("text") or "该复习啦")
    if t == "notice":
        return str(d.get("text") or "")[:max_len]
    return ""


def parse_reminder_minutes(text: str) -> Optional[int]:
    """把「20 分钟后提醒我」这类话里的分钟数抠出来（桌宠右键菜单用固定档位，这里是兜底）"""
    import re
    m = re.search(r"(\d{1,4})\s*分钟", text or "")
    if m:
        return max(1, min(24 * 60, int(m.group(1))))
    m = re.search(r"(\d{1,2})\s*(小时|钟头)", text or "")
    if m:
        return max(1, min(24 * 60, int(m.group(1)) * 60))
    return None


# ---------------------------------------------------------------------------
# 位置记忆
# ---------------------------------------------------------------------------

def clamp_to_screen(x: int, y: int, w: int, h: int,
                    screen_w: int, screen_h: int, margin: int = 4) -> Tuple[int, int]:
    """别让桌宠被拖到屏幕外面找不回来（多显示器拔掉之后尤其容易发生）"""
    x = max(-w + margin + 20, min(screen_w - 20, int(x)))
    y = max(0, min(screen_h - 20, int(y)))
    return x, y


def default_position(screen_w: int, screen_h: int, w: int = PET_W, h: int = PET_H) -> Tuple[int, int]:
    """默认落在右下角（不挡视线，又能一眼看到）"""
    return max(0, screen_w - w - 40), max(0, screen_h - h - 90)


def load_pos(path: Path) -> Dict[str, Any]:
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_pos(path: Path, patch: Dict[str, Any]) -> Dict[str, Any]:
    data = load_pos(path)
    data.update(patch or {})
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass
    return data
