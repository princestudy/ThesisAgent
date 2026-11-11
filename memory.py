import sqlite3
from contextlib import closing
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "memory.db"
DEFAULT_USER_ID = "local-user"
DEFAULT_SESSION_ID = "terminal"
MAX_MEMORY_MESSAGES = 100


def initialize_database() -> None:
    with closing(sqlite3.connect(DB_PATH)) as connection, connection:
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


def save_message(
    role: str,
    content: str,
    user_id: str = DEFAULT_USER_ID,
    session_id: str = DEFAULT_SESSION_ID,
) -> None:
    with closing(sqlite3.connect(DB_PATH)) as connection, connection:
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
    with closing(sqlite3.connect(DB_PATH)) as connection:
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
    with closing(sqlite3.connect(DB_PATH)) as connection, connection:
        connection.execute(
            "DELETE FROM messages WHERE user_id = ? AND session_id = ?",
            (user_id, session_id),
        )
