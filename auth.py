"""账号与密钥校验。

设计要点：
- 密码只保存 scrypt 派生值，不保存明文，也不用额外依赖；
- 登录失败计数 + 临时锁定，避免无限重试；
- API Key 不落库、不写日志，只活在浏览器会话内存里；
- 本模块只提供原语，权限判断（谁能建号、谁能禁用谁）放在界面层。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
)

import config
import memory
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
MAX_USERS = max(1, config.get_int("MAX_USERS", 200))
# 部署级配置：固定管理员账号（ADMIN_USERNAME + ADMIN_PASSWORD），以及注册邀请码。
# 不配置时保持"第一个注册者是管理员、开放注册"的旧行为。
ADMIN_USERNAME = str(config.get("ADMIN_USERNAME", "")).strip()
ADMIN_PASSWORD = str(config.get("ADMIN_PASSWORD", "")).strip()
INVITE_CODE = str(config.get("INVITE_CODE", "")).strip()
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
COMMON_PASSWORDS = {
    "12345678",
    "123456789",
    "1234567890",
    "11111111",
    "88888888",
    "password",
    "password1",
    "password123",
    "qwertyui",
    "qwerty123",
    "abc12345",
    "iloveyou",
    "admin123",
    "admin1234",
    "woaini520",
    "a1234567",
    "p@ssw0rd",
    "letmein1",
}
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

load_dotenv()


class AuthError(RuntimeError):
    """账号相关操作失败。"""


def api_base_url() -> str:
    return str(config.get("OPENAI_BASE_URL", "")).strip() or DEFAULT_BASE_URL


def api_model() -> str:
    return str(config.get("OPENAI_MODEL", "")).strip() or DEFAULT_MODEL


def _raw_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(memory.DB_PATH, timeout=10.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def initialize_auth_database() -> None:
    with closing(_raw_connect()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                is_disabled INTEGER NOT NULL DEFAULT 0,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # 历史上可能残留多个管理员，先把多余的降级，再加唯一约束。
        admin_ids = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM users WHERE is_admin = 1 ORDER BY id ASC"
            ).fetchall()
        ]
        for extra_id in admin_ids[1:]:
            connection.execute(
                "UPDATE users SET is_admin = 0 WHERE id = ?", (extra_id,)
            )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_single_admin "
            "ON users(is_admin) WHERE is_admin = 1"
        )
        connection.commit()


def _connect() -> sqlite3.Connection:
    initialize_auth_database()
    return _raw_connect()


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    ).hex()


def validate_username(username: str) -> str:
    candidate = (username or "").strip()
    if not USERNAME_PATTERN.match(candidate):
        raise AuthError("用户名需 3-32 位，只能包含字母、数字、下划线、点和短横线。")
    return candidate


def validate_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"密码至少需要 {MIN_PASSWORD_LENGTH} 个字符。")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthError("密码过长。")
    if password.strip() != password:
        raise AuthError("密码首尾不能是空格。")
    if password.lower() in COMMON_PASSWORDS:
        raise AuthError("这个密码太常见了，请换一个更不容易猜到的密码。")
    return password


def _insert_user(
    username: str, password: str, *, is_admin: bool | None = None
) -> dict[str, object]:
    """写入一个账号；is_admin 为 None 表示沿用"第一个注册者是管理员"的规则。"""
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(password, salt)

    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        total = int(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        if total >= MAX_USERS:
            connection.rollback()
            raise AuthError(f"注册人数已达上限（{MAX_USERS} 人），请联系管理员。")
        admin_flag = (total == 0) if is_admin is None else is_admin
        try:
            cursor = connection.execute(
                "INSERT INTO users (username, password_salt, password_hash, is_admin) "
                "VALUES (?, ?, ?, ?)",
                (username, salt.hex(), password_hash, int(admin_flag)),
            )
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            message = str(error)
            if "idx_single_admin" in message:
                raise AuthError("系统里的管理员已经有且仅有一个。") from error
            raise AuthError("该用户名已被占用。") from error

    return {
        "id": int(cursor.lastrowid or 0),
        "username": username,
        "is_admin": bool(admin_flag),
    }


def register_user(
    username: str, password: str, invite_code: str | None = None
) -> dict[str, object]:
    """开放注册。

    - 配置了 `INVITE_CODE` 时必须带对邀请码；
    - 配置了 `ADMIN_USERNAME` 时只有这个用户名会成为管理员，其他都是普通用户；
    - 两者都没配置时，沿用"第一个注册者自动成为唯一管理员"。
    """
    if INVITE_CODE and (invite_code or "").strip() != INVITE_CODE:
        raise AuthError("邀请码不正确，请向管理员索取。")
    candidate = validate_username(username)
    validate_password(password)
    if password.lower() == candidate.lower():
        raise AuthError("密码不能和用户名相同。")

    if ADMIN_USERNAME:
        is_admin: bool | None = candidate.lower() == ADMIN_USERNAME.lower()
        if is_admin and has_admin():
            raise AuthError("管理员账号已经存在。")
    else:
        is_admin = None
    return _insert_user(candidate, password, is_admin=is_admin)


def ensure_admin_account() -> str | None:
    """按配置保证有一个固定的管理员账号；返回提示文案（None 表示正常）。"""
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        return None
    try:
        username = validate_username(ADMIN_USERNAME)
    except AuthError:
        return (
            "配置的 ADMIN_USERNAME 不合法：需要 3-32 位字母、数字、下划线、点或短横线。"
        )
    if len(ADMIN_PASSWORD) < MIN_PASSWORD_LENGTH:
        return f"配置的 ADMIN_PASSWORD 太短，至少要 {MIN_PASSWORD_LENGTH} 位。"

    existing = get_user(username)
    if existing:
        if existing["is_admin"]:
            return None
        if has_admin():
            return f"用户名 {username} 已经被普通用户占用，请换一个 ADMIN_USERNAME。"
        with closing(_connect()) as connection:
            connection.execute(
                "UPDATE users SET is_admin = 1, is_disabled = 0, "
                "failed_attempts = 0, locked_until = NULL WHERE id = ?",
                (existing["id"],),
            )
            connection.commit()
        return None

    if has_admin():
        return None
    try:
        _insert_user(username, ADMIN_PASSWORD, is_admin=True)
    except AuthError as error:
        return f"创建固定管理员失败：{error}"
    return None


def _row_to_user(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
        "is_disabled": bool(row["is_disabled"]),
        "created_at": row["created_at"],
    }


def get_user(username: str) -> dict[str, object] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE username = ?",
            ((username or "").strip(),),
        ).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_id(user_id: int | str) -> dict[str, object] | None:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE id = ?",
            (int(user_id),),
        ).fetchone()
    return _row_to_user(row) if row else None


def list_users() -> list[dict[str, object]]:
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM users ORDER BY id ASC"
        ).fetchall()
    return [_row_to_user(row) for row in rows]


def count_admins() -> int:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS total FROM users WHERE is_admin = 1 AND is_disabled = 0"
        ).fetchone()
    return int(row["total"]) if row else 0


def _is_locked(row: sqlite3.Row) -> bool:
    locked_until = row["locked_until"]
    if not locked_until:
        return False
    try:
        deadline = datetime.fromisoformat(locked_until)
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline > datetime.now(UTC)


def _record_failure(connection: sqlite3.Connection, user_id: int) -> None:
    row = connection.execute(
        "SELECT failed_attempts FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    attempts = int(row["failed_attempts"]) + 1 if row else 1
    locked_until = None
    if attempts >= MAX_FAILED_ATTEMPTS:
        locked_until = (
            datetime.now(UTC) + timedelta(minutes=LOCK_MINUTES)
        ).isoformat()
        attempts = 0
    connection.execute(
        "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
        (attempts, locked_until, user_id),
    )
    connection.commit()


def authenticate(username: str, password: str) -> tuple[dict[str, object] | None, str]:
    """校验账号密码，返回 (用户信息, 提示语)。

    用户名不存在和密码错误返回同一句提示，避免用户名枚举。
    """
    account = (username or "").strip()
    if not account or not password:
        return None, "请输入用户名和密码。"

    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE username = ?",
            (account,),
        ).fetchone()
        if row is None:
            # 对不存在的用户也做一次同样代价的哈希，避免用响应时间枚举用户名。
            _hash_password(password, b"\x00" * 16)
            return None, "用户名或密码错误。"
        if bool(row["is_disabled"]):
            return None, "该账号已被禁用，请联系管理员。"
        if _is_locked(row):
            return None, f"登录失败次数过多，账号已锁定 {LOCK_MINUTES} 分钟。"

        salt = bytes.fromhex(row["password_salt"])
        candidate_hash = _hash_password(password, salt)
        if not hmac.compare_digest(candidate_hash, row["password_hash"]):
            _record_failure(connection, int(row["id"]))
            return None, "用户名或密码错误。"

        connection.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = NULL WHERE id = ?",
            (int(row["id"]),),
        )
        connection.commit()

    return {
        "id": int(row["id"]),
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
    }, "登录成功。"


def set_password(user_id: int | str, new_password: str) -> None:
    validate_password(new_password)
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(new_password, salt)
    with closing(_connect()) as connection:
        connection.execute(
            "UPDATE users SET password_salt = ?, password_hash = ?, "
            "failed_attempts = 0, locked_until = NULL WHERE id = ?",
            (salt.hex(), password_hash, int(user_id)),
        )
        connection.commit()


def change_password(
    user_id: int | str,
    current_password: str,
    new_password: str,
) -> tuple[bool, str]:
    user = get_user_by_id(user_id)
    if user is None:
        return False, "账号不存在。"
    _, message = authenticate(str(user["username"]), current_password)
    if message != "登录成功。":
        return False, "当前密码不正确。"
    if new_password and new_password.lower() == str(user["username"]).lower():
        return False, "密码不能和用户名相同。"
    set_password(user_id, new_password)
    return True, "密码已更新。"


def set_disabled(user_id: int | str, disabled: bool) -> None:
    target = get_user_by_id(user_id)
    if target is None:
        raise AuthError("账号不存在。")
    if target["is_admin"] and disabled:
        raise AuthError("管理员账号不能被禁用。")
    with closing(_connect()) as connection:
        connection.execute(
            "UPDATE users SET is_disabled = ? WHERE id = ?",
            (int(disabled), int(user_id)),
        )
        connection.commit()


def count_users() -> int:
    with closing(_connect()) as connection:
        row = connection.execute("SELECT COUNT(*) AS total FROM users").fetchone()
    return int(row["total"]) if row else 0


def has_admin() -> bool:
    return count_admins() > 0


def verify_api_key(api_key: str, base_url: str | None = None) -> tuple[bool, str]:
    """通过调用服务端的模型列表接口校验 API Key，不消耗生成费用。"""
    key = (api_key or "").strip()
    if not key.startswith("sk-") or len(key) < 20:
        return False, "API Key 格式不正确，应以 sk- 开头。"

    target = (base_url or api_base_url()).strip()
    if not target.lower().startswith(("http://", "https://")):
        return False, "接口地址必须以 http:// 或 https:// 开头。"

    client = OpenAI(
        api_key=key,
        base_url=target,
        timeout=15.0,
        max_retries=0,
    )
    try:
        client.models.list()
    except AuthenticationError:
        return False, "API Key 无效或已被禁用。"
    except APIConnectionError:
        return False, "无法连接模型服务，请检查网络或代理设置。"
    except APIStatusError as error:
        if error.status_code in (401, 403):
            return False, "API Key 无效或没有访问权限。"
        if error.status_code == 429:
            return False, "请求过于频繁，请稍后再试。"
        return False, f"密钥校验失败（服务返回 {error.status_code}）。"
    except Exception:  # noqa: BLE001 - 校验失败一律按不可用处理
        return False, "密钥校验失败，请稍后重试。"
    return True, "API Key 校验通过。"
