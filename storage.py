"""SQLite persistence for user composition state, reply routing, blocklist, and update cursor."""
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
            -- Every user the bot has seen, used to turn "@username" into a numeric ID.
            CREATE TABLE IF NOT EXISTS known_users (
                user_id INTEGER PRIMARY KEY,
                chat_id INTEGER,
                username TEXT,
                last_seen INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS known_users_username_idx
                ON known_users(username COLLATE NOCASE);
            -- Blocklist rows may carry a numeric ID, a username, or both.
            CREATE TABLE IF NOT EXISTS blocked_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                username TEXT,
                blocked_at INTEGER NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS blocked_users_user_idx
                ON blocked_users(user_id) WHERE user_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS blocked_users_username_idx
                ON blocked_users(username COLLATE NOCASE) WHERE username IS NOT NULL;
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

    # ------------------------------------------------------------------
    # Blocklist + known users
    # ------------------------------------------------------------------
    def upsert_known_user(self, user_id: int, chat_id: int | None, username: str | None) -> None:
        """Remember a user the bot has seen so "@username" can be resolved to an ID."""
        self.db.execute(
            "INSERT INTO known_users(user_id, chat_id, username, last_seen) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET "
            "chat_id=COALESCE(excluded.chat_id, known_users.chat_id), "
            "username=COALESCE(excluded.username, known_users.username), "
            "last_seen=excluded.last_seen",
            (user_id, chat_id, username, int(time.time())),
        )
        self.db.commit()

    def get_known_user(self, user_id: int) -> dict[str, object] | None:
        row = self.db.execute(
            "SELECT user_id, chat_id, username FROM known_users WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row:
            return None
        return {
            "user_id": int(row["user_id"]),
            "chat_id": int(row["chat_id"]) if row["chat_id"] is not None else None,
            "username": row["username"],
        }

    def find_user_id_by_username(self, username: str) -> int | None:
        row = self.db.execute(
            "SELECT user_id FROM known_users WHERE username=? COLLATE NOCASE "
            "ORDER BY last_seen DESC LIMIT 1",
            (username,),
        ).fetchone()
        return int(row["user_id"]) if row else None

    def block_user(self, user_id: int, chat_id: int | None, username: str | None) -> None:
        """Add (or refresh) a blocked user. Blocking twice never duplicates a row."""
        row = self.db.execute(
            "SELECT id FROM blocked_users WHERE user_id=?", (user_id,)
        ).fetchone()
        if row is None and username:
            # Promote a username-only rule into a full rule with the numeric ID.
            row = self.db.execute(
                "SELECT id FROM blocked_users WHERE username=? COLLATE NOCASE",
                (username,),
            ).fetchone()
        if row is not None:
            self.db.execute(
                "UPDATE blocked_users SET user_id=?, chat_id=?, username=COALESCE(?, username) "
                "WHERE id=?",
                (user_id, chat_id, username, row["id"]),
            )
        else:
            self.db.execute(
                "INSERT INTO blocked_users(user_id, chat_id, username, blocked_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, chat_id, username, int(time.time())),
            )
        self.db.commit()

    def block_username_only(self, username: str) -> None:
        """Block a username that has not messaged the bot yet (no numeric ID known)."""
        row = self.db.execute(
            "SELECT id FROM blocked_users WHERE username=? COLLATE NOCASE", (username,)
        ).fetchone()
        if row is not None:
            return
        self.db.execute(
            "INSERT INTO blocked_users(user_id, chat_id, username, blocked_at) "
            "VALUES (NULL, NULL, ?, ?)",
            (username, int(time.time())),
        )
        self.db.commit()

    def unblock_user(self, user_id: int) -> bool:
        cursor = self.db.execute("DELETE FROM blocked_users WHERE user_id=?", (user_id,))
        self.db.commit()
        return cursor.rowcount > 0

    def unblock_username(self, username: str) -> bool:
        cursor = self.db.execute(
            "DELETE FROM blocked_users WHERE username=? COLLATE NOCASE", (username,)
        )
        self.db.commit()
        return cursor.rowcount > 0

    def is_blocked(self, user_id: int | None, username: str | None = None) -> bool:
        """Return True when the numeric ID *or* the username is on the blocklist."""
        if user_id is not None:
            row = self.db.execute(
                "SELECT 1 FROM blocked_users WHERE user_id=?", (user_id,)
            ).fetchone()
            if row:
                return True
        if username:
            row = self.db.execute(
                "SELECT 1 FROM blocked_users WHERE username=? COLLATE NOCASE",
                (username,),
            ).fetchone()
            if row:
                return True
        return False

    def find_blocked_by_username(self, username: str) -> tuple[int | None, str] | None:
        """Look up a blocklist row by username (case-insensitive)."""
        row = self.db.execute(
            "SELECT user_id, username FROM blocked_users WHERE username=? COLLATE NOCASE",
            (username,),
        ).fetchone()
        if not row:
            return None
        user_id = int(row["user_id"]) if row["user_id"] is not None else None
        return user_id, str(row["username"] or username)

    def remember_username(self, user_id: int, username: str,
                          chat_id: int | None = None) -> None:
        """Tie a blocked user to their numeric ID and keep the username current.

        This runs whenever a blocked user writes to the bot, so:
          * a username-only rule becomes permanent (numeric ID is learned);
          * renaming later cannot slip past the numeric-ID rule.
        """
        if not username:
            return
        row = self.db.execute(
            "SELECT id FROM blocked_users WHERE user_id=?", (user_id,)
        ).fetchone()
        if row is None:
            # Username-only rule written before this user ever messaged the bot.
            row = self.db.execute(
                "SELECT id FROM blocked_users WHERE username=? COLLATE NOCASE",
                (username,),
            ).fetchone()
            if row is None:
                return
            self.db.execute(
                "UPDATE blocked_users SET user_id=?, chat_id=? WHERE id=?",
                (user_id, chat_id, row["id"]),
            )
            self.db.commit()
            return
        clash = self.db.execute(
            "SELECT id FROM blocked_users WHERE username=? COLLATE NOCASE AND id<>?",
            (username, row["id"]),
        ).fetchone()
        if clash is not None:
            # The new username is already covered by another rule; drop the stale row.
            self.db.execute("DELETE FROM blocked_users WHERE id=?", (row["id"],))
        else:
            self.db.execute(
                "UPDATE blocked_users SET username=?, chat_id=COALESCE(?, chat_id) WHERE id=?",
                (username, chat_id, row["id"]),
            )
        self.db.commit()

    def list_blocked(self) -> list[dict[str, object]]:
        rows = self.db.execute(
            "SELECT user_id, chat_id, username, blocked_at FROM blocked_users "
            "ORDER BY blocked_at DESC, id DESC"
        ).fetchall()
        return [
            {
                "user_id": int(row["user_id"]) if row["user_id"] is not None else None,
                "chat_id": int(row["chat_id"]) if row["chat_id"] is not None else None,
                "username": str(row["username"]) if row["username"] else None,
                "blocked_at": int(row["blocked_at"]),
            }
            for row in rows
        ]

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
