"""桌面版：把「本地服务 + 应用窗口 + 桌宠」装进一个 exe。

模块划分（每块只干一件事，方便单独测）：
- `core.py`     纯逻辑：端口、窗口参数、桌宠状态机、气泡文案、位置夹取（无 tkinter，可单测）
- `pet_art.py`  Pillow 现场画形象（含「压边」解决透明窗口的色边问题）
- `pet.py`      桌宠窗口（拖拽/菜单/气泡/小聊天窗/事件订阅）
- `shell.py`    启动器（单实例 → 起服务 → 开应用窗口 → 起桌宠）
- `build_exe.py` 打包：PyInstaller onefile，产出「艾琳老师.exe」

跑法（源码）：
    py -3.14 -m desktop.shell              # 从 agent_studio 目录
    py -3.14 agent_studio/desktop/shell.py --check   # 只自检，不起窗口
"""

from __future__ import annotations

__all__ = ["core", "pet", "pet_art", "shell"]
