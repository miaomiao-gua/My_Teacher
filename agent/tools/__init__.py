"""内置工具集合。

导入本包即完成内置工具注册；`load_builtin_tools()` 幂等，可重复调用。
每个子模块用 `@tool` 装饰器把自己的能力登记进注册表——**新增能力只需要新建一个文件**，
不需要改内核、不需要改提示词（schema 会自动进入模型的工具列表）。
"""

from __future__ import annotations

_LOADED = False


def load_builtin_tools() -> None:
    global _LOADED
    # 导入即注册。分模块是为了「加能力只加文件」：不动内核、不动提示词，
    # 新工具的 schema 会自动进入模型的工具列表。
    from . import calc, files, lesson, mathviz, ui, web  # noqa: F401

    _LOADED = True


__all__ = ["load_builtin_tools"]
