#!/usr/bin/env python
"""打一次真的「看屏幕」接口（服务端自己截屏），看它答得对不对、要多久。

用法：py -3.14 tools/try_vision.py ["问一句话"]
    不带问题 = 用设置里那句默认要求（等于"按快捷键"那条路）。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:5001"


def main() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 else ""
    print(f"提问：{question or '（用设置里的默认要求，等于按快捷键）'}")
    t0 = time.time()
    body = json.dumps({"question": question}).encode("utf-8")
    req = urllib.request.Request(BASE + "/agent/vision/ask", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"  失败：{type(exc).__name__}: {exc}")
        return 1
    print(f"  ok={data.get('ok')}  用时={time.time() - t0:.1f}s  模型={data.get('model')}")
    print("  回答：" + str(data.get("answer") or "")[:400])
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
