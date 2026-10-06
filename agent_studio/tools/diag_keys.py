#!/usr/bin/env python
"""诊断：config.json 里的密钥**真的**解开了吗？

踩过的坑：用 `bool(cfg.get('chat_api_key'))` 判断是错的 ——
解密失败时那个字段还是 `enc$...` 密文，照样是"真值"，于是看起来一切正常，
实际发出去的是一串密文（云端必然 401）。这里直接看：
  ① 拿到的值是不是还带着 enc$ 前缀（带前缀 = 没解开）
  ② 用真实解密函数逐字段试一遍
  ③ 顺带发一次 /agent/health，看云端链路实际通不通
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from kernel.config import CONFIG_PATH, load_app_config  # noqa: E402
from kernel.crypto_utils import decrypt_str, is_encrypted  # noqa: E402


def main() -> int:
    print(f"配置文件：{CONFIG_PATH}")
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.is_file() else {}
    enc = {k: v for k, v in raw.items() if is_encrypted(v)}
    print(f"磁盘上是密文的字段：{list(enc)}")

    print("\n逐个字段试真实解密：")
    for k, v in enc.items():
        try:
            plain = decrypt_str(v)
            print(f"  ✓ {k}: 解开（{len(plain)} 字符，看起来像 {plain[:6]}…）")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {k}: 解不开 —— {type(exc).__name__}: {exc}")

    cfg = load_app_config()
    still_enc = [k for k in enc if is_encrypted(cfg.get(k, ""))]
    print(f"\nload_app_config() 里**仍然是密文**的字段：{still_enc or '（无）'}")
    if still_enc:
        print("  → 这就是问题：内核会把密文当 key 发出去，云端必定 401。")
        print("  → 处理：在界面左下角「⚙ 设置 → 云端 API」重新填一次 API Key（会重新加密存好）。")

    try:
        r = httpx.get("http://127.0.0.1:5001/agent/health", timeout=30.0)
        data = r.json()
        print("\n云端链路实测：")
        for c in data.get("chain", []):
            print(f"  {c.get('label')}  ok={c.get('ok')}  {str(c.get('detail') or '')[:80]}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n（服务没在跑，跳过后端实测：{type(exc).__name__}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
