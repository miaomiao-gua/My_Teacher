"""kernel 内部的路径入口 —— 真正实现在项目顶层的 `runtime_paths.py`。

为什么要多这一层：`runtime_paths` 是顶层的（`course/` 里那些老模块也要用），
而 kernel 内部一律用包内相对导入更稳（打包后不会因为 sys.path 顺序而出问题）。
所以这里做一次转发，kernel 里统一写 `from .paths import ...` / `from ..paths import ...`。
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from runtime_paths import data_root, describe, exe_dir, is_frozen, resource_root  # noqa: E402,F401

__all__ = ["data_root", "resource_root", "exe_dir", "is_frozen", "describe"]
