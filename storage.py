"""SQLite persistence for user composition state, reply routing, and update cursor."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS awaiting_message (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reply_map (
                owner_chat_id INTEGER NOT NULL,
                owner_message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                user_chat_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (owner_chat_id, owner_message_id)
            );
            CREATE INDEX IF NOT EXISTS reply_map_user_idx ON reply_map(user_id);
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def set_awaiting(self, user_id: int, chat_id: int) -> None:
        self.db.execute(
            "INSERT INTO awaiting_message(user_id, chat_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id, created_at=excluded.created_at",
            (user_id, chat_id, int(time.time())),
        )
        self.db.commit()

    def get_awaiting_chat(self, user_id: int) -> int | None:
        row = self.db.execute(
            "SELECT chat_id FROM awaiting_message WHERE user_id=?", (user_id,)
        ).fetchone()
        return int(row["chat_id"]) if row else None

    def clear_awaiting(self, user_id: int) -> None:
        self.db.execute("DELETE FROM awaiting_message WHERE user_id=?", (user_id,))
        self.db.commit()

    def add_reply_target(
        self, owner_chat_id: int, owner_message_id: int,
        user_id: int, user_chat_id: int,
    ) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO reply_map "
            "(owner_chat_id, owner_message_id, user_id, user_chat_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (owner_chat_id, owner_message_id, user_id, user_chat_id, int(time.time())),
        )
        self.db.commit()

    def get_reply_target(self, owner_chat_id: int, owner_message_id: int) -> tuple[int, int] | None:
        row = self.db.execute(
            "SELECT user_id, user_chat_id FROM reply_map "
            "WHERE owner_chat_id=? AND owner_message_id=?",
            (owner_chat_id, owner_message_id),
        ).fetchone()
        if not row:
            return None
        return int(row["user_id"]), int(row["user_chat_id"])

    def get_cursor(self) -> int:
        row = self.db.execute("SELECT value FROM metadata WHERE key='update_cursor'").fetchone()
        return int(row["value"]) if row else 0

    def save_cursor(self, update_id: int) -> None:
        self.db.execute(
            "INSERT INTO metadata(key, value) VALUES ('update_cursor', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(update_id),),
        )
        self.db.commit()
