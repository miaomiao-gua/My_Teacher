"""Crypto —— 敏感配置字段（API key / 密钥）的对称加密存储

背景：config.json（全局 + 每用户）中保存了云端 API key 等敏感信息，
此前为明文落盘，一旦磁盘被读取（备份外泄 / 主机被入侵后 dump / 调试日志）
即直接泄露密钥。本模块把这类字段加密后再写盘：

- 算法：Fernet（AES-128-CBC + HMAC-SHA256，cryptography 库）
- 密钥来源（按优先级）：
  1. 环境变量 MY_TEACHER_SECRET_KEY（任意长度，内部用 sha256 派生 32 字节）
  2. data/.secret_key 文件（首次自动生成，POSIX 下 chmod 600）
- 加密值格式：`enc$<base64(fernet token)>`，通过前缀识别，支持旧明文懒迁移

使用方约定：所有 *key 字段落盘前调用 seal_dict 加密，读取后 open_dict 解密。
"""

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SECRET_FILE = DATA_DIR / ".secret_key"

# 加密值前缀：enc$ + Fernet token
ENC_PREFIX = "enc$"

_cached_fernet: Fernet | None = None


def _derive_key(material: str) -> bytes:
    """把任意长度密钥材料派生为 32 字节 urlsafe base64（Fernet key 格式）。"""
    return base64.urlsafe_b64encode(hashlib.sha256(material.encode("utf-8")).digest())


def _load_secret() -> bytes:
    """加载/生成加密主密钥。返回 Fernet 格式 key（32 字节 urlsafe base64）。"""
    env = os.getenv("MY_TEACHER_SECRET_KEY", "").strip()
    if env:
        return _derive_key(env)
    if SECRET_FILE.exists():
        try:
            key = SECRET_FILE.read_bytes().strip()
            if key:
                # 统一为标准 Fernet key（44 字节 urlsafe base64）：
                # - 文件里若已是标准格式（44 字节 base64）→ 解码再编码还原一致；
                # - 若手动放入 32 字节原始 key → 补上 base64 编码。
                try:
                    decoded = base64.urlsafe_b64decode(key)
                    if len(decoded) == 32:
                        return base64.urlsafe_b64encode(decoded)
                except Exception:
                    pass
                if len(key) == 32:
                    return base64.urlsafe_b64encode(key)
                return key
        except Exception:
            pass
    # 生成新密钥并持久化
    key = Fernet.generate_key()
    try:
        SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        SECRET_FILE.write_bytes(key)
        if os.name != "nt":
            try:
                os.chmod(SECRET_FILE, 0o600)
            except Exception:
                pass
    except Exception:
        pass
    return key


def _fernet() -> Fernet:
    global _cached_fernet
    if _cached_fernet is None:
        _cached_fernet = Fernet(_load_secret())
    return _cached_fernet


def is_encrypted(value) -> bool:
    """判断值是否为已加密的密文。"""
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt_str(plain: str) -> str:
    """加密单个字符串；空值/非字符串原样返回（保持幂等）。"""
    if not plain or not isinstance(plain, str):
        return plain
    try:
        token = _fernet().encrypt(plain.encode("utf-8"))
    except Exception:
        return plain
    return ENC_PREFIX + token.decode("ascii")


def decrypt_str(token: str) -> str:
    """解密单个字符串；非密文原样返回；密文损坏/密钥不匹配返回空串（不抛异常）。"""
    if not is_encrypted(token):
        return token
    try:
        raw = _fernet().decrypt(token[len(ENC_PREFIX):].encode("ascii"))
        return raw.decode("utf-8")
    except (InvalidToken, Exception):
        return ""


def seal_dict(data: dict, fields) -> dict:
    """把 data 中指定字段的明文加密（已加密的跳过）。返回新 dict。"""
    out = dict(data)
    for f in fields:
        v = out.get(f)
        if isinstance(v, str) and v and not is_encrypted(v):
            out[f] = encrypt_str(v)
    return out


def open_dict(data: dict, fields) -> dict:
    """把 data 中指定字段的密文解密（明文跳过）。返回新 dict。"""
    out = dict(data)
    for f in fields:
        v = out.get(f)
        if is_encrypted(v):
            out[f] = decrypt_str(v)
    return out
