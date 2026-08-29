"""Auth —— 用户注册 / 登录 / token 鉴权（前后端拆分后的越权防护）

用户数据存储在 data/users.json：
{
  "users": {
    "<username>": {
      "salt": "<随机盐>",
      "password_hash": "<sha256(salt+password)>",
      "created_at": "<ISO 时间>",
      "tokens": { "<token>": "<过期时间戳>" }
    }
  }
}
"""

import hashlib
import json
import secrets
import threading
import time
from pathlib import Path

# 前后端拆分后：数据统一放在 server/ 上一级的 data/ 目录
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
USERS_PATH = DATA_DIR / "users.json"

# token 有效期：30 天
TOKEN_TTL = 30 * 24 * 3600

_lock = threading.Lock()


def _load() -> dict:
    if not USERS_PATH.exists():
        return {"users": {}}
    try:
        return json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"users": {}}


def _save(data: dict) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = USERS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(USERS_PATH)


def hash_password(password: str, salt: str | None = None) -> tuple:
    """计算 (salt, sha256(salt+password))。"""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return salt, digest


def register(username: str, password: str) -> dict:
    """注册新用户。用户名已存在 / 参数非法时抛 ValueError。"""
    username = username.strip()
    if not (2 <= len(username) <= 32):
        raise ValueError("用户名长度需在 2~32 字符之间")
    if not password or len(password) < 6:
        raise ValueError("密码长度至少 6 位")
    with _lock:
        data = _load()
        users = data["users"]
        if username in users:
            raise ValueError("用户名已存在")
        salt, pwd_hash = hash_password(password)
        users[username] = {
            "salt": salt,
            "password_hash": pwd_hash,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "tokens": {},
        }
        _save(data)
    return {"username": username}


def login(username: str, password: str) -> str:
    """校验密码并签发 token；失败抛 ValueError。"""
    username = username.strip()
    with _lock:
        data = _load()
        user = data["users"].get(username)
        if not user:
            raise ValueError("用户名或密码错误")
        salt, pwd_hash = hash_password(password, user["salt"])
        if pwd_hash != user["password_hash"]:
            raise ValueError("用户名或密码错误")
        token = secrets.token_hex(24)
        user["tokens"][token] = str(int(time.time()) + TOKEN_TTL)
        _save(data)
    return token


def logout(token: str) -> None:
    """使指定 token 失效。"""
    if not token:
        return
    with _lock:
        data = _load()
        for user in data["users"].values():
            if token in user["tokens"]:
                del user["tokens"][token]
                break
        _save(data)


def get_user_by_token(token: str) -> str | None:
    """校验 token：有效则返回用户名，过期/不存在返回 None（顺带清理过期 token）。"""
    if not token:
        return None
    with _lock:
        data = _load()
        now = int(time.time())
        for username, user in data["users"].items():
            exp = user["tokens"].get(token)
            if exp is None:
                continue
            if int(exp) < now:
                del user["tokens"][token]
                _save(data)
                return None
            return username
    return None


def user_count() -> int:
    """已注册用户数（用于前端判断是否需要引导注册）。"""
    return len(_load()["users"])


def first_user() -> str | None:
    """最早注册的用户名（无用户时返回 None）。

    用于「课程按账号完全隔离」的旧数据迁移：无 owner 的遗留课程划给第一个注册账号。
    """
    users = _load()["users"]
    if not users:
        return None
    return min(users, key=lambda u: users[u].get("created_at", ""))
