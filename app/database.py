import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.config import settings
from app.core.policy import policy_engine


class MemoryPolicyError(ValueError):
    """Raised when a memory write contains PHI or credentials."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.sqlite_db_path
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.init_db()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    session_id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL DEFAULT 'web',
                    user_id TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL DEFAULT 'New Conversation',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    model TEXT DEFAULT 'opencode',
                    tool_calls TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES conversations(session_id)
                );

                CREATE TABLE IF NOT EXISTS memories (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    due_date TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS local_calendar_events (
                    id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    location TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS local_sheet_rows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sheet_range TEXT NOT NULL,
                    values_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS telegram_processed_updates (
                    update_id INTEGER PRIMARY KEY,
                    processed_at TEXT NOT NULL
                );
                """
            )

    def ensure_conversation(
        self, session_id: str, channel: str = "web", user_id: str = "default", title: Optional[str] = None
    ) -> Dict[str, Any]:
        now = _utc_now()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE session_id = ?", (session_id,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE conversations SET updated_at = ? WHERE session_id = ?",
                    (now, session_id),
                )
                return dict(row)
            conv_title = title or f"Session {session_id[:12]}"
            conn.execute(
                """
                INSERT INTO conversations (session_id, channel, user_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, channel, user_id, conv_title, now, now),
            )
            return {
                "session_id": session_id,
                "channel": channel,
                "user_id": user_id,
                "title": conv_title,
                "created_at": now,
                "updated_at": now,
            }

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        model: str = "opencode",
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        channel: str = "web",
        user_id: str = "default",
    ) -> Dict[str, Any]:
        title_hint = content[:50].strip() if role == "user" and content else None
        self.ensure_conversation(session_id, channel=channel, user_id=user_id, title=title_hint)
        now = _utc_now()
        tool_calls_json = json.dumps(tool_calls) if tool_calls else None
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO messages (session_id, role, content, model, tool_calls, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, role, content, model, tool_calls_json, now),
            )
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE session_id = ?",
                (now, session_id),
            )
            msg_id = cur.lastrowid
        return {
            "id": msg_id,
            "session_id": session_id,
            "role": role,
            "content": content,
            "model": model,
            "tool_calls": tool_calls or [],
            "created_at": now,
        }

    def get_messages(self, session_id: str, limit: int = 30) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM messages
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (session_id, limit),
            ).fetchall()
        result = []
        for r in rows:
            item = dict(r)
            item["tool_calls"] = json.loads(item["tool_calls"]) if item.get("tool_calls") else []
            result.append(item)
        return result

    def clear_conversation(self, session_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))

    def list_conversations(self, limit: int = 25) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT c.*, COUNT(m.id) AS message_count
                FROM conversations c
                LEFT JOIN messages m ON c.session_id = m.session_id
                GROUP BY c.session_id
                ORDER BY c.updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # Telegram webhook deduplication
    def claim_telegram_update(self, update_id: int) -> bool:
        """Atomically claim an update so webhook retries cannot repeat side effects."""
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=30)).isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO telegram_processed_updates (update_id, processed_at) VALUES (?, ?)",
                (int(update_id), now.isoformat()),
            )
            conn.execute(
                "DELETE FROM telegram_processed_updates WHERE processed_at < ?", (cutoff,)
            )
            return cursor.rowcount == 1

    # Memories
    def save_memory(self, key: str, value: str) -> Dict[str, Any]:
        key = (key or "").strip()
        value = (value or "").strip()
        if not key or not value:
            raise ValueError("Memory key and value are required.")
        if len(key) > 100 or len(value) > 5000:
            raise ValueError("Memory key must be at most 100 characters and value at most 5000.")

        decision = policy_engine.check_memory_write(key, value)
        if not decision.get("allowed"):
            raise MemoryPolicyError(decision.get("warning") or "Memory write rejected by policy.")

        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memories (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key.strip(), value.strip(), now),
            )
        return {"key": key.strip(), "value": value.strip(), "updated_at": now}

    def list_memories(self) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT key, value, updated_at FROM memories ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_memory(self, key: str) -> bool:
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM memories WHERE key = ?", (key,))
            return cur.rowcount > 0

    # Tasks
    def add_task(self, title: str, due_date: Optional[str] = None) -> Dict[str, Any]:
        now = _utc_now()
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO tasks (title, status, due_date, created_at) VALUES (?, 'pending', ?, ?)",
                (title.strip(), due_date, now),
            )
            task_id = cur.lastrowid
        return {
            "id": task_id,
            "title": title.strip(),
            "status": "pending",
            "due_date": due_date,
            "created_at": now,
        }

    def list_tasks(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY id DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

    def update_task_status(self, task_id: int, status: str) -> Optional[Dict[str, Any]]:
        with self.connect() as conn:
            conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None


db = Database()
