"""
SQLite async wrapper for the Webex vacation bot.
Tables: vacation_periods, vacation_log, config
"""
import os
import aiosqlite
from datetime import datetime, timezone

DB_PATH = os.getenv("SQLITE_PATH", "/data/vacation.db")


async def init_db() -> None:
    """Create tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS vacation_periods (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                start_date  TEXT NOT NULL,
                end_date    TEXT NOT NULL,
                created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                closed_at   TEXT
            );

            CREATE TABLE IF NOT EXISTS vacation_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                period_id       INTEGER NOT NULL REFERENCES vacation_periods(id),
                person_email    TEXT NOT NULL,
                person_name     TEXT NOT NULL,
                room_id         TEXT NOT NULL,
                message_id      TEXT NOT NULL,
                message_preview TEXT,
                replied_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );

            CREATE TABLE IF NOT EXISTS config (
                key     TEXT PRIMARY KEY,
                value   TEXT
            );
        """)
        await db.commit()


async def get_config(key: str, default=None) -> str | None:
    """Return config value for key, or default if not set."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT value FROM config WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            if row is None:
                return default
            return row["value"]


async def set_config(key: str, value) -> None:
    """Upsert a config value. Non-string values are coerced to str."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value))
        )
        await db.commit()
