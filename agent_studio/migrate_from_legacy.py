#!/usr/bin/env python
"""从老项目迁移配置（一次性）：把 API Key 等搬到本项目自己的 data/config.json。

为什么要单独一步：本项目是独立项目，**不读老项目的配置**（否则两个项目互相牵扯，
哪天老项目删了、换机器、换了 .secret_key，这边就莫名其妙连不上模型）。
迁移时用老项目的密钥解密、再用本项目的密钥加密，搬完两边就彻底解耦。

用法：py -3.14 migrate_from_legacy.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from kernel.config import CONFIG_PATH, LEGACY_DATA_DIR, LEGACY_SERVER_DIR  # noqa: E402

# 需要搬过来的字段（模型、密钥、连图、TTS 相关；学籍/课程数据不搬）
KEEP_KEYS = (
    "chat_api_key", "chat_base_url", "chat_model", "chat_provider",
    "cloud_api_key", "cloud_base_url", "cloud_model",
    "siliconflow_api_key", "siliconflow_model",
    "ollama_base_url", "ollama_model", "ollama_temperature", "ollama_num_ctx", "ollama_num_predict",
    "vision_enabled", "vision_base_url", "vision_model", "vision_api_key",
    "tts_provider", "tts_cloud_base_url", "tts_cloud_voice", "tts_cloud_model", "voice_enabled",
)


def main() -> int:
    legacy_config = LEGACY_DATA_DIR / "config.json"
    if not legacy_config.exists():
        print(f"找不到老项目配置：{legacy_config}")
        print("（如果你本来就是新装的，直接手工创建 data/config.json 填 chat_api_key 即可）")
        return 1

    raw = json.loads(legacy_config.read_text(encoding="utf-8"))

    # 1) 用老项目的 crypto_utils + data/.secret_key 解密
    decrypted = dict(raw)
    encrypted_fields = [k for k, v in raw.items() if isinstance(v, str) and v.startswith("enc$")]
    if encrypted_fields:
        sys.path.insert(0, str(LEGACY_SERVER_DIR))
        import crypto_utils as legacy_crypto  # type: ignore

        decrypted = legacy_crypto.open_dict(raw, encrypted_fields)
        failed = [k for k in encrypted_fields if not decrypted.get(k)]
        if failed:
            print(f"⚠️ 这些字段解密失败（老项目 .secret_key 可能已更换）：{failed}")
        else:
            print(f"✓ 已用老项目密钥解密 {len(encrypted_fields)} 个字段")

    # 2) 挑出需要的字段
    payload = {k: decrypted[k] for k in KEEP_KEYS if decrypted.get(k)}
    if not payload:
        print("老配置里没有可迁移的字段。")
        return 1

    # 3) 用本项目的密钥重新加密后落盘
    from kernel.crypto_utils import seal_dict

    secret_fields = [k for k in payload if k.endswith("api_key")]
    payload = seal_dict(payload, secret_fields)

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✓ 已写入 {CONFIG_PATH}")
    print("  迁移的字段：" + "、".join(sorted(payload.keys())))
    print("  密钥字段已用本项目自己的 data/.secret_key 加密（明文只在你迁移的这一刻存在于内存里）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
