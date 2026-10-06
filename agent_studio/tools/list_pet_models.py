#!/usr/bin/env python
"""看一眼形象清单扫出来什么（排查「换形象」里少了/多了哪个模型时用）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel import pet_models as pm  # noqa: E402


def main() -> int:
    items = pm.scan_models(force=True)
    print(f"扫到 {len(items)} 个形象：\n")
    for it in items:
        print(f"  id={it['id']:<30} 名字={it['name'][:24]:<26} "
              f"动作={it['motion_groups'] or '（无，靠程序演绎）'} "
              f"表情={len(it['expressions'])} {it['size_mb']}MB")
        print(f"      目录：{it['dir']}")
        print(f"      网址：{it['url']}")
    print(f"\n默认形象：{pm.default_model_id()}")
    print(f"当前记住的形象：{pm.current_model_id()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
