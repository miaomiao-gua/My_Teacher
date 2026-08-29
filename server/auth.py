"""Auth —— 用户注册 / 登录 / token 鉴权（前后端拆分后的越权防护）

存储：v5.O 渐进式迁移，用户账号与会话从 users.json 迁入 SQLite（data/app.db）。

安全说明：
- 用户名白名单（只允许字母数字 _ - 中文），防止路径穿越（用户名会拼进文件路径）。
- 密码哈希：新注册用 PBKDF2-HMAC-SHA256（60 万次迭代）；旧 sha256 / 低迭代哈希校验通过后自动升级。
- token 由 secrets 生成（CSPRNG），30 天过期；会话存 sessions 表，读取时惰性清理过期项。
- 登录暴力破解限流（login_fails 表）：失败计数跨 worker 共享（SQLite 持久化），
  连续 5 次失败锁定 60 秒。
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

# 前后端拆分后：数据统一放在 server/ 上一级的 data/ 目录
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
USERS_PATH = DATA_DIR / "users.json"   # 旧版 JSON 用户文件（迁移用，成功后改名 .bak）
DB_PATH = DATA_DIR / "app.db"

# token 有效期：30 天
TOKEN_TTL = 30 * 24 * 3600

# 用户名白名单：字母/数字/下划线/连字符/中文，2~32 字符。
# 禁止 `..` `/` `\` 空格等——用户名会拼进 data/users/<name>/ 路径，必须防穿越。
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fa5]{2,32}$")

# PBKDF2 迭代次数（OWASP 推荐 >= 60 万；登录耗时约 0.2~0.8s，可接受）
_PBKDF2_ITER = 600_000
_PBKDF2_PREFIX = "pbkdf2$"

# 登录限流：连续失败 N 次后锁定 LOCK 秒
_LOGIN_FAIL_AFTER = 5
_LOGIN_FAIL_SECONDS = 60

# 进程内锁：串行化写操作，配合 WAL + busy_timeout 保证多进程/多线程下数据一致
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    """打开一个 SQLite 连接（每次操作独立连接，天然线程安全）。

    WAL 模式：并发读 + 单写者；busy_timeout 让写冲突时等待而非立刻报错。
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                username      TEXT PRIMARY KEY,
                salt          TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                username   TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_fails (
                key   TEXT PRIMARY KEY,
                fails INTEGER NOT NULL DEFAULT 0,
                until REAL    NOT NULL DEFAULT 0
            );
            """
        )


def _migrate_from_json() -> None:
    """把旧 users.json 中的账号一次性导入 SQLite；users 表已有数据则跳过。

    迁移成功后把 users.json 重命名为 users.json.bak 留作备份，避免重复导入。
    """
    if not USERS_PATH.exists():
        return
    try:
        data = json.loads(USERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    users = (data or {}).get("users") or {}
    if not users:
        return
    with _connect() as conn:
        cnt = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if cnt:
            return  # 已迁移过
        now = int(time.time())
        for name, u in users.items():
            conn.execute(
                "INSERT OR IGNORE INTO users(username, salt, password_hash, created_at) VALUES(?,?,?,?)",
                (name, u.get("salt", ""), u.get("password_hash", ""), u.get("created_at", "")),
            )
            for tk, exp in (u.get("tokens") or {}).items():
                try:
                    e = int(exp)
                except (TypeError, ValueError):
                    e = now
                conn.execute(
                    "INSERT OR IGNORE INTO sessions(token, username, expires_at) VALUES(?,?,?)",
                    (tk, name, e),
                )
    try:
        USERS_PATH.replace(USERS_PATH.with_suffix(".json.bak"))
    except Exception:
        pass


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
    salt, pwd_hash = hash_password(password)
    created = time.strftime("%Y-%m-%dT%H:%M:%S")
    with _lock, _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO users(username, salt, password_hash, created_at) VALUES(?,?,?,?)",
                (username, salt, pwd_hash, created),
            )
        except sqlite3.IntegrityError:
            raise ValueError("用户名已存在")
    return {"username": username}


def login(username: str, password: str) -> str:
    """校验密码并签发 token；失败抛 ValueError。

    旧 sha256 格式 / 迭代次数低于当前标准的哈希，校验通过后自动重哈希升级。
    """
    username = username.strip()
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT salt, password_hash FROM users WHERE username=?", (username,)
        ).fetchone()
        if not row:
            raise ValueError("用户名或密码错误")
        salt, pwd_hash = row
        if not verify_password(password, salt, pwd_hash):
            raise ValueError("用户名或密码错误")
        need_upgrade = not pwd_hash.startswith(_PBKDF2_PREFIX)
        if not need_upgrade:
            try:
                _, iter_s, _ = pwd_hash.split("$")
                need_upgrade = int(iter_s) < _PBKDF2_ITER
            except Exception:
                need_upgrade = True
        if need_upgrade:
            salt, pwd_hash = hash_password(password, salt)
            conn.execute(
                "UPDATE users SET salt=?, password_hash=? WHERE username=?",
                (salt, pwd_hash, username),
            )
        token = secrets.token_hex(24)
        conn.execute(
            "INSERT OR REPLACE INTO sessions(token, username, expires_at) VALUES(?,?,?)",
            (token, username, int(time.time()) + TOKEN_TTL),
        )
    return token


def logout(token: str) -> None:
    """使指定 token 失效。"""
    if not token:
        return
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def get_user_by_token(token: str) -> str | None:
    """校验 token：有效则返回用户名，过期/不存在返回 None（顺带清理过期 token）。"""
    if not token:
        return None
    with _lock, _connect() as conn:
        row = conn.execute(
            "SELECT username, expires_at FROM sessions WHERE token=?", (token,)
        ).fetchone()
        if not row:
            return None
        username, exp = row
        if int(exp) < int(time.time()):
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            return None
        return username


def user_count() -> int:
    """已注册用户数（用于前端判断是否需要引导注册）。"""
    with _connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def first_user() -> str | None:
    """最早注册的用户名（无用户时返回 None）。

    用于「课程按账号完全隔离」的旧数据迁移：无 owner 的遗留课程划给第一个注册账号。
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT username FROM users ORDER BY created_at ASC, rowid ASC LIMIT 1"
        ).fetchone()
        return row[0] if row else None


# ---- 登录限流（SQLite 持久化，gunicorn 多 worker 共享） ----

def throttle_until(key: str) -> float:
    """返回该 key 当前锁定剩余秒数（0 = 未锁定）。"""
    with _connect() as conn:
        row = conn.execute("SELECT until FROM login_fails WHERE key=?", (key,)).fetchone()
        if not row:
            return 0.0
        return max(0.0, float(row[0]) - time.time())


def throttle_fail(key: str) -> int:
    """记录一次失败，返回累计失败次数。达到阈值后写入锁定截止时间。"""
    with _lock, _connect() as conn:
        conn.execute(
            "INSERT INTO login_fails(key, fails, until) VALUES(?,1,0) "
            "ON CONFLICT(key) DO UPDATE SET fails = fails + 1",
            (key,),
        )
        fails = conn.execute("SELECT fails FROM login_fails WHERE key=?", (key,)).fetchone()[0]
        if fails >= _LOGIN_FAIL_AFTER:
            conn.execute(
                "UPDATE login_fails SET until=? WHERE key=?",
                (time.time() + _LOGIN_FAIL_SECONDS, key),
            )
        return fails


def throttle_reset(key: str) -> None:
    """登录成功后清除失败计数。"""
    with _connect() as conn:
        conn.execute("DELETE FROM login_fails WHERE key=?", (key,))


# 模块导入即初始化数据库 + 迁移旧 users.json（gunicorn 多 worker 各自执行，幂等）
_init_db()
_migrate_from_json()
