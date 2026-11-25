
import sqlite3
import json
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

from app.config import DATABASE_URL


def get_database_path():
    if not DATABASE_URL.startswith("sqlite:///"):
        raise ValueError("Only SQLite DATABASE_URL is supported")

    return DATABASE_URL.replace("sqlite:///", "", 1)


DB_PATH = get_database_path()


@contextmanager
def get_connection():
    """Open a connection, commit on success, roll back on error, always close."""
    if DB_PATH != ":memory:":
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(DB_PATH, timeout=10)
    connection.row_factory = sqlite3.Row

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                system_prompt TEXT,
                created_at TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id)
                    REFERENCES sessions(session_id)
            )
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id, id)
        """)



def get_session(session_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,)
        ).fetchone()


def get_messages(session_id):
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT role, content, metadata
            FROM messages
            WHERE session_id = ?
            ORDER BY id ASC
        """, (session_id,)).fetchall()

        return [
            {
                "role": row["role"],
                "content": row["content"],
                **(
                    {"metadata": json.loads(row["metadata"])}
                    if row["metadata"] else {}
                )
            }
            for row in rows
        ]


def save_conversation(
    session_id,
    system_prompt,
    user_message,
    assistant_reply,
    metadata
):
    now = datetime.now(timezone.utc).isoformat()

    with get_connection() as conn:
        conn.execute("""
            INSERT INTO sessions (
                session_id, system_prompt, created_at
            )
            VALUES (?, ?, ?)
            ON CONFLICT(session_id)
            DO UPDATE SET system_prompt = excluded.system_prompt
        """, (session_id, system_prompt, now))

        conn.execute("""
            INSERT INTO messages (
                session_id, role, content, metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            session_id,
            "user",
            user_message,
            None,
            now
        ))

        conn.execute("""
            INSERT INTO messages (
                session_id, role, content, metadata, created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            session_id,
            "assistant",
            assistant_reply,
            json.dumps(metadata),
            now
        ))

