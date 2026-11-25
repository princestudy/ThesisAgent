import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

# 部署时可以用 THESISAGENT_DB 指向挂载卷，默认放在项目目录。
DB_PATH = Path(
    os.getenv("THESISAGENT_DB", "")
    or (Path(__file__).resolve().parent / "memory.db")
)
DEFAULT_USER_ID = "local-user"
DEFAULT_SESSION_ID = "terminal"
MAX_MEMORY_MESSAGES = 100
# 每个用户最多保留几个对话（网页版新建对话时的上限）。
MAX_CONVERSATIONS = max(1, int(os.getenv("MAX_CONVERSATIONS", "2") or 2))
NEW_CONVERSATION_TITLE = "新对话"
LEGACY_CONVERSATION_ID = "web"
LEGACY_CONVERSATION_TITLE = "历史对话"


class ConversationLimitError(RuntimeError):
    """对话数量达到上限。"""


def new_conversation_id() -> str:
    return "c-" + uuid.uuid4().hex[:12]


def _connect() -> sqlite3.Connection:
    """多用户并发写时，WAL 和 busy_timeout 能显著减少 database is locked。"""
    connection = sqlite3.connect(DB_PATH, timeout=10.0)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def initialize_database() -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL DEFAULT 'local-user',
                session_id TEXT NOT NULL DEFAULT 'terminal',
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "user_id" not in columns:
            connection.execute(
                "ALTER TABLE messages ADD COLUMN user_id TEXT NOT NULL DEFAULT 'local-user'"
            )
        if "session_id" not in columns:
            connection.execute(
                "ALTER TABLE messages ADD COLUMN session_id TEXT NOT NULL DEFAULT 'terminal'"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_scope "
            "ON messages (user_id, session_id, id)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '新对话',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversations_user "
            "ON conversations (user_id, updated_at DESC)"
        )


def save_message(
    role: str,
    content: str,
    user_id: str = DEFAULT_USER_ID,
    session_id: str = DEFAULT_SESSION_ID,
) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            "INSERT INTO messages (user_id, session_id, role, content) "
            "VALUES (?, ?, ?, ?)",
            (user_id, session_id, role, content),
        )


def load_recent_messages(
    limit: int = 20,
    user_id: str = DEFAULT_USER_ID,
    session_id: str = DEFAULT_SESSION_ID,
) -> list[dict[str, str]]:
    limit = max(1, min(limit, MAX_MEMORY_MESSAGES))
    with closing(_connect()) as connection:
        rows = connection.execute(
            """
            SELECT role, content
            FROM messages
            WHERE user_id = ? AND session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, session_id, limit),
        ).fetchall()

    return [{"role": role, "content": content} for role, content in reversed(rows)]


def clear_messages(
    user_id: str = DEFAULT_USER_ID,
    session_id: str = DEFAULT_SESSION_ID,
) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            "DELETE FROM messages WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        )


# ---------------------------------------------------------------- 会话（多对话）


def _row_to_conversation(row: sqlite3.Row) -> dict[str, str]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def count_conversations(user_id: str = DEFAULT_USER_ID) -> int:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()
    return int(row[0]) if row else 0


def list_conversations(user_id: str = DEFAULT_USER_ID) -> list[dict[str, str]]:
    """按最近使用时间排序，最新的在前。"""
    with closing(_connect()) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, user_id, title, created_at, updated_at
            FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC, created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [_row_to_conversation(row) for row in rows]


def get_conversation(
    conversation_id: str, user_id: str = DEFAULT_USER_ID
) -> dict[str, str] | None:
    with closing(_connect()) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, user_id, title, created_at, updated_at "
            "FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
    return _row_to_conversation(row) if row else None


def create_conversation(
    user_id: str = DEFAULT_USER_ID,
    title: str = NEW_CONVERSATION_TITLE,
    conversation_id: str | None = None,
) -> str:
    """新建会话；超过 MAX_CONVERSATIONS 时抛 ConversationLimitError。"""
    conversation_id = conversation_id or new_conversation_id()
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        total = connection.execute(
            "SELECT COUNT(*) FROM conversations WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        if int(total) >= MAX_CONVERSATIONS:
            connection.rollback()
            raise ConversationLimitError(
                f"每个账号最多保留 {MAX_CONVERSATIONS} 个对话，请先删除一个再新建。"
            )
        connection.execute(
            "INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
            (conversation_id, user_id, title or NEW_CONVERSATION_TITLE),
        )
        connection.commit()
    except ConversationLimitError:
        raise
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return conversation_id


def touch_conversation(
    conversation_id: str, user_id: str = DEFAULT_USER_ID
) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            "UPDATE conversations SET updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )


def rename_conversation(
    conversation_id: str, title: str, user_id: str = DEFAULT_USER_ID
) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            "UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ? AND user_id = ?",
            ((title or NEW_CONVERSATION_TITLE)[:60], conversation_id, user_id),
        )


def delete_conversation(
    conversation_id: str, user_id: str = DEFAULT_USER_ID
) -> None:
    with closing(_connect()) as connection, connection:
        connection.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        connection.execute(
            "DELETE FROM messages WHERE user_id = ? AND session_id = ?",
            (user_id, conversation_id),
        )


def has_messages(conversation_id: str, user_id: str = DEFAULT_USER_ID) -> bool:
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT 1 FROM messages WHERE user_id = ? AND session_id = ? LIMIT 1",
            (user_id, conversation_id),
        ).fetchone()
    return row is not None


def ensure_default_conversation(user_id: str = DEFAULT_USER_ID) -> str:
    """保证账号至少有一个会话；旧版单会话数据（session_id='web'）会被接管。"""
    existing = list_conversations(user_id)
    if existing:
        return existing[0]["id"]
    if has_messages(LEGACY_CONVERSATION_ID, user_id):
        create_conversation(
            user_id,
            title=LEGACY_CONVERSATION_TITLE,
            conversation_id=LEGACY_CONVERSATION_ID,
        )
        return LEGACY_CONVERSATION_ID
    return create_conversation(user_id)

