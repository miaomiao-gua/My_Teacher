"""Auth —— 用户注册 / 登录 / token 鉴权（前后端拆分后的越权防护）

用户数据存储在 data/users.json：
{
  "users": {
    "<username>": {
      "salt": "<随机盐>",
      "password_hash": "<pbkdf2 或兼容的旧 sha256 摘要>",
      "created_at": "<ISO 时间>",
      "tokens": { "<token>": "<过期时间戳>" }
    }
  }
}

安全说明：
- 用户名白名单（只允许字母数字 _ - 中文），防止路径穿越（用户名会拼进文件路径）。
- 密码哈希：新注册用 PBKDF2-HMAC-SHA256（12 万次迭代）；旧 sha256 格式校验通过后自动升级。
- token 由 secrets 生成（CSPRNG），30 天过期。
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path

# 前后端拆分后：数据统一放在 server/ 上一级的 data/ 目录
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
USERS_PATH = DATA_DIR / "users.json"

# token 有效期：30 天
TOKEN_TTL = 30 * 24 * 3600

# 用户名白名单：字母/数字/下划线/连字符/中文，2~32 字符。
# 禁止 `..` `/` `\` 空格等——用户名会拼进 data/users/<name>/ 路径，必须防穿越。
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fa5]{2,32}$")

# PBKDF2 迭代次数（OWASP 建议 >= 60 万；兼顾低配教学机取 12 万）
_PBKDF2_ITER = 120_000
_PBKDF2_PREFIX = "pbkdf2$"

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
    """计算 (salt, password_hash)。使用 PBKDF2-HMAC-SHA256，盐为 CSPRNG 随机。"""
    salt = salt or secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ITER)
    digest = base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")
    return salt, f"{_PBKDF2_PREFIX}{_PBKDF2_ITER}${digest}"


def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    """校验密码。新格式 pbkdf2 直接比对；旧格式 sha256(salt+password) 兼容比对（时序安全）。"""
    if not stored_hash:
        return False
    if stored_hash.startswith(_PBKDF2_PREFIX):
        try:
            _, iter_s, digest = stored_hash.split("$")
            key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iter_s))
            computed = base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")
        except Exception:
            return False
        return hmac.compare_digest(computed, digest)
    # 旧格式：sha256(salt + password)
    old = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return hmac.compare_digest(old, stored_hash)


def register(username: str, password: str) -> dict:
    """注册新用户。用户名已存在 / 参数非法时抛 ValueError。"""
    username = username.strip()
    if not _USERNAME_RE.match(username):
        raise ValueError("用户名仅支持中英文、数字、下划线和连字符（2~32 字符）")
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
        if not verify_password(password, user["salt"], user["password_hash"]):
            raise ValueError("用户名或密码错误")
        # 旧 sha256 哈希校验通过后，自动升级为 PBKDF2
        if not user["password_hash"].startswith(_PBKDF2_PREFIX):
            salt, pwd_hash = hash_password(password, user["salt"])
            user["salt"], user["password_hash"] = salt, pwd_hash
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
