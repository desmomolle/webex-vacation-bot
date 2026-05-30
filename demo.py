"""
Demo mode — click through the whole app without real Webex credentials.

Enable with DEMO_MODE=true in .env. In demo mode:
  - a sample vacation period + protocol entries are seeded on first start,
  - the OAuth step in the setup wizard is stubbed (fake tokens, no Webex round-trip),
  - the poll loop invents a new incoming contact every DEMO_POLL_SECONDS so the
    protocol fills up live.

Nothing leaves the machine in demo mode — no Webex, mail or LLM calls are made.
"""
import logging
import os
import time
from datetime import datetime, timezone

import aiosqlite

import auth
import db

log = logging.getLogger("vacation-bot.demo")

DEMO_POLL_SECONDS = int(os.getenv("DEMO_POLL_SECONDS", "30"))

_FAKE_CONTACTS = [
    ("Anna Schmidt", "anna.schmidt@cisco.com", "Hey, hast du die Quartalszahlen schon gesehen?"),
    ("Tom Becker", "tbecker@partner-gmbh.de", "Passt der Termin am Donnerstag?"),
    ("Julia Wagner", "julia.wagner@cisco.com", "Kannst du mir bei der Demo-Umgebung helfen?"),
    ("Mark Hoffmann", "m.hoffmann@kunde.com", "Danke, schöne Ferien!"),
    ("Priya Patel", "priya.patel@cisco.com", "Kurze Frage zum Renewal — ruf mich an wenn du zurück bist."),
    ("Lukas Maier", "lukas@startup.io", "Wollte nur Hallo sagen, viel Spaß im Urlaub!"),
    ("Sandra Klein", "sandra.klein@cisco.com", "Das Deck für Montag — kannst du nochmal drüberschauen?"),
]


def is_demo() -> bool:
    return os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes")


async def _latest_period_id(conn) -> int | None:
    async with conn.execute("SELECT id FROM vacation_periods ORDER BY id DESC LIMIT 1") as cur:
        row = await cur.fetchone()
    return row["id"] if row else None


async def seed_demo_data() -> None:
    """Create a sample period + initial protocol rows if none exist yet."""
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        period_id = await _latest_period_id(conn)
        if period_id is None:
            async with conn.execute(
                "INSERT INTO vacation_periods (start_date, end_date) VALUES (?, ?) RETURNING id",
                ("2026-05-24", "2026-06-02"),
            ) as cur:
                period_id = (await cur.fetchone())["id"]
            await conn.commit()

        async with conn.execute(
            "SELECT COUNT(*) AS n FROM vacation_log WHERE period_id = ?", (period_id,)
        ) as cur:
            existing = (await cur.fetchone())["n"]

        if existing == 0:
            for name, email, text in _FAKE_CONTACTS[:4]:
                await conn.execute(
                    "INSERT INTO vacation_log "
                    "(period_id, person_email, person_name, room_id, message_id, message_preview) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (period_id, email, name, "demo-room", "demo-msg", text),
                )
            await conn.commit()

    await db.set_config("vacation_enabled", "true")
    await db.set_config("vacation_end", "2026-06-02")
    await db.set_config("end_date", "2026-06-02")
    await db.set_config("current_period_id", str(period_id))
    log.info("Demo data ready (period #%s)", period_id)


def stub_tokens() -> None:
    """Write fake (encrypted) tokens so the wizard's OAuth step can complete."""
    auth._save_tokens({
        "access_token": "demo-access-token",
        "refresh_token": "demo-refresh-token",
        "expires_at": int(time.time()) + 999_999,
    })


async def simulate_incoming() -> dict:
    """Invent one new incoming contact and log a reply, cycling the fake pool."""
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        period_id = await _latest_period_id(conn)
        if period_id is None:
            return {"demo": True, "reason": "no period"}

        async with conn.execute(
            "SELECT person_email FROM vacation_log WHERE period_id = ?", (period_id,)
        ) as cur:
            seen = {r["person_email"] for r in await cur.fetchall()}

        nxt = next((c for c in _FAKE_CONTACTS if c[1] not in seen), None)
        if nxt is None:
            return {"demo": True, "replies_sent": 0, "reason": "all demo contacts replied"}

        name, email, text = nxt
        await conn.execute(
            "INSERT INTO vacation_log "
            "(period_id, person_email, person_name, room_id, message_id, message_preview) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (period_id, email, name, "demo-room", "demo-msg", text),
        )
        await conn.commit()

    now = datetime.now(timezone.utc).isoformat()
    await db.set_config("vacation_last_check", now)
    await db.set_config("last_check", now)
    log.info("Demo: simulated incoming from %s", name)
    return {"demo": True, "replies_sent": 1, "contact": name}
