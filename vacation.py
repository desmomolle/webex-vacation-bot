"""
Webex Vacation Auto-Reply Bot — core polling logic.

Config is read from environment variables (.env) with DB config table as override.
No CSA / Things3 dependencies.
"""
import asyncio
import httpx
import json as _json
import logging
import os
from datetime import datetime, timezone, date

import aiosqlite
import db

log = logging.getLogger("vacation-bot")

MY_EMAIL = os.getenv("MY_WEBEX_EMAIL", "")

# Default templates — can be overridden via env or db config table
MSG_INTERNAL_DEFAULT = (
    "⚡ Auto-reply: Hi, I'm on vacation until {end_date} and not reading messages. "
    "I'll get back to you when I'm back!"
)
MSG_EXTERNAL_DEFAULT = (
    "⚡ Automatic out-of-office reply: Thank you for your message. "
    "I am currently out of office and will return on {end_date}. "
    "Your message will not be forwarded. I will get back to you upon my return."
)


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, "")
    if raw.lower() in ("1", "true", "yes"):
        return True
    if raw.lower() in ("0", "false", "no"):
        return False
    return default


async def _cfg(key: str, env_key: str | None = None, default=None) -> str:
    """Read config: env var takes precedence over DB, then default."""
    if env_key:
        env_val = os.getenv(env_key, "")
        if env_val:
            return env_val
    db_val = await db.get_config(key, None)
    if db_val is not None:
        return str(db_val).strip().strip('"')
    return default if default is not None else ""


async def get_or_create_period() -> int | None:
    """Return existing open vacation period id or create a new one."""
    vacation_end = os.getenv("VACATION_END_DATE") or await db.get_config("vacation_end", "")
    vacation_end = str(vacation_end).strip().strip('"')

    try:
        end_date_obj = date.fromisoformat(vacation_end) if vacation_end else date.today()
    except ValueError:
        end_date_obj = date.today()

    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        # Reuse existing open period for this end date
        async with conn.execute(
            "SELECT id FROM vacation_periods WHERE end_date = ? AND closed_at IS NULL",
            (end_date_obj.isoformat(),)
        ) as cur:
            row = await cur.fetchone()
        if row:
            return row["id"]

        # Create new period
        today = date.today().isoformat()
        async with conn.execute(
            "INSERT INTO vacation_periods (start_date, end_date) VALUES (?, ?) RETURNING id",
            (today, end_date_obj.isoformat())
        ) as cur:
            row = await cur.fetchone()
        await conn.commit()
        period_id = row["id"]
        log.info(f"New vacation period created: #{period_id} until {end_date_obj}")
        return period_id


async def close_open_periods() -> None:
    """Mark all open vacation periods as closed (used on manual deactivation)."""
    async with aiosqlite.connect(db.DB_PATH) as conn:
        await conn.execute(
            "UPDATE vacation_periods SET closed_at = ? WHERE closed_at IS NULL",
            (datetime.now(timezone.utc).isoformat(),),
        )
        await conn.commit()


async def check_vacation_replies() -> dict:
    """Poll Webex for new 1:1 messages and send auto-replies."""

    # Enabled check: env first, then DB
    enabled_env = os.getenv("VACATION_ENABLED", "")
    if enabled_env:
        enabled = enabled_env.lower() in ("1", "true", "yes")
    else:
        enabled_raw = await db.get_config("vacation_enabled", "false")
        enabled = str(enabled_raw).lower() in ("1", "true")
    if not enabled:
        return {"skipped": True, "reason": "vacation not enabled"}

    vacation_end = os.getenv("VACATION_END_DATE") or await db.get_config("vacation_end", "")
    vacation_end = str(vacation_end).strip().strip('"')

    # Auto-disable when vacation has ended
    if vacation_end:
        try:
            end_date = date.fromisoformat(vacation_end)
            if end_date < date.today():
                async with aiosqlite.connect(db.DB_PATH) as conn:
                    await conn.execute(
                        "UPDATE vacation_periods SET closed_at = ? WHERE end_date = ? AND closed_at IS NULL",
                        (datetime.now(timezone.utc).isoformat(), end_date.isoformat())
                    )
                    await conn.commit()
                await db.set_config("vacation_enabled", "false")
                log.info("Vacation ended — period closed, auto-disabled")
                return {"skipped": True, "reason": "vacation ended, auto-disabled"}
        except ValueError:
            pass

    pat = os.getenv("WEBEX_PAT") or await db.get_config("webex_pat", "")
    pat = str(pat).strip().strip('"')
    if not pat:
        return {"skipped": True, "reason": "no PAT configured"}

    period_id = await get_or_create_period()

    # Load templates (env overrides DB)
    msg_internal = os.getenv("MSG_INTERNAL") or await db.get_config("vacation_message_internal", MSG_INTERNAL_DEFAULT)
    msg_external = os.getenv("MSG_EXTERNAL") or await db.get_config("vacation_message_external", MSG_EXTERNAL_DEFAULT)
    msg_internal = str(msg_internal).strip().strip('"')
    msg_external = str(msg_external).strip().strip('"')

    # Format the end_date placeholder
    if vacation_end:
        try:
            d = datetime.strptime(vacation_end, "%Y-%m-%d")
            formatted_date = d.strftime("%d.%m.%Y")
        except ValueError:
            formatted_date = vacation_end
    else:
        formatted_date = "soon"
    msg_internal = msg_internal.replace("{end_date}", formatted_date)
    msg_external = msg_external.replace("{end_date}", formatted_date)

    # Get phase start time and already-replied emails
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT created_at FROM vacation_periods WHERE id = ?", (period_id,)
        ) as cur:
            period_row = await cur.fetchone()
        phase_start_iso = period_row["created_at"] if period_row else ""

        async with conn.execute(
            "SELECT person_email FROM vacation_log WHERE period_id = ?", (period_id,)
        ) as cur:
            replied_rows = await cur.fetchall()
    replied_emails = {r["person_email"] for r in replied_rows}

    results = {
        "period_id": period_id,
        "checked_rooms": 0,
        "new_messages": 0,
        "replies_sent": 0,
        "already_replied": 0,
        "errors": [],
    }

    async with httpx.AsyncClient(timeout=30) as client:
        headers = {"Authorization": f"Bearer {pat}"}

        # Paginate through all direct rooms, sorted by last activity
        rooms = []
        url = "https://webexapis.com/v1/rooms?type=direct&max=100&sortBy=lastactivity"
        try:
            while url and len(rooms) < 500:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    if not rooms:
                        return {"skipped": True, "reason": f"Webex API error {r.status_code}"}
                    break
                page = r.json().get("items", [])
                if not page:
                    break
                rooms.extend(page)

                # Stop paginating once all rooms on this page predate the phase
                oldest = page[-1].get("lastActivity", "")
                if phase_start_iso and oldest and oldest < phase_start_iso:
                    break

                url = None
                link_header = r.headers.get("Link", "")
                if 'rel="next"' in link_header:
                    for part in link_header.split(","):
                        if 'rel="next"' in part:
                            url = part.split("<")[1].split(">")[0]
        except Exception as e:
            if not rooms:
                return {"skipped": True, "reason": str(e)}

        log.info(f"Rooms loaded: {len(rooms)} (paginated)")
        results["checked_rooms"] = len(rooms)

        for room in rooms:
            room_id = room.get("id")
            last_activity = room.get("lastActivity", "")

            if phase_start_iso and last_activity and last_activity < phase_start_iso:
                continue

            # Fetch up to 50 recent messages in this room
            try:
                r = await client.get(
                    f"https://webexapis.com/v1/messages?roomId={room_id}&max=50",
                    headers=headers
                )
                if r.status_code != 200:
                    continue
                all_msgs = r.json().get("items", [])
                if not all_msgs:
                    continue

                # Collect messages from the other person since phase start (chronological)
                sender_msgs = []
                sender_email = ""
                for msg in reversed(all_msgs):
                    msg_email = msg.get("personEmail", "").lower()
                    msg_created = msg.get("created", "")
                    if phase_start_iso and msg_created and msg_created < phase_start_iso:
                        continue
                    if msg_email == MY_EMAIL.lower():
                        continue
                    if not sender_email:
                        sender_email = msg.get("personEmail", "")
                    sender_msgs.append(msg)

                if not sender_msgs:
                    continue
            except Exception:
                continue

            sender_name = sender_email.split("@")[0]

            # Resolve display name via room membership
            try:
                r = await client.get(
                    f"https://webexapis.com/v1/memberships?roomId={room_id}&max=5",
                    headers=headers
                )
                if r.status_code == 200:
                    for m in r.json().get("items", []):
                        if m.get("personEmail", "").lower() == sender_email.lower():
                            sender_name = m.get("personDisplayName", sender_name)
                            break
            except Exception:
                pass

            email_lower = sender_email.lower()

            # Skip bots
            if email_lower.endswith("@webex.bot") or email_lower.endswith("@sparkbot.io"):
                continue

            results["new_messages"] += len(sender_msgs)

            if sender_email in replied_emails:
                results["already_replied"] += 1
                continue

            # Build message preview for log
            message_log = []
            for msg in sender_msgs:
                ts = msg.get("created", "")[:19].replace("T", " ")
                text = (msg.get("text") or msg.get("html") or "").strip()
                if text:
                    message_log.append(f"[{ts}] {text}")
            message_preview = "\n".join(message_log) or "(no text messages)"
            log.info(f"{sender_name}: {len(sender_msgs)} message(s) since phase start")

            # Internal vs external reply (domain configurable)
            internal_domain = os.getenv("INTERNAL_DOMAIN") or await db.get_config("internal_domain", "cisco.com")
            internal_domain = str(internal_domain).strip().strip('"').lower().lstrip("@")
            reply_text = msg_internal if email_lower.endswith(f"@{internal_domain}") else msg_external

            try:
                reply_r = await client.post(
                    "https://webexapis.com/v1/messages",
                    headers=headers,
                    json={"roomId": room_id, "text": reply_text}
                )
                if reply_r.status_code in (200, 201):
                    async with aiosqlite.connect(db.DB_PATH) as conn:
                        await conn.execute(
                            """INSERT INTO vacation_log
                               (period_id, person_email, person_name, room_id, message_id, message_preview)
                               VALUES (?, ?, ?, ?, ?, ?)""",
                            (period_id, sender_email, sender_name, room_id,
                             sender_msgs[-1].get("id", ""), message_preview)
                        )
                        await conn.commit()
                    replied_emails.add(sender_email)
                    results["replies_sent"] += 1
                    log.info(f"Reply sent to {sender_name}")
                else:
                    results["errors"].append(f"{sender_name}: HTTP {reply_r.status_code}")
            except Exception as e:
                results["errors"].append(f"{sender_name}: {str(e)[:60]}")

            await asyncio.sleep(0.2)

    await db.set_config("vacation_last_check", datetime.now(timezone.utc).isoformat())
    log.info(f"Vacation check done: {results['replies_sent']} replies sent")
    return results


async def get_vacation_status() -> dict:
    """Return current status and period history for the UI."""
    vacation_end = os.getenv("VACATION_END_DATE") or await db.get_config("vacation_end", "")
    vacation_end = str(vacation_end).strip().strip('"')

    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        async with conn.execute("""
            SELECT p.id, p.start_date, p.end_date, p.created_at, p.closed_at,
                   COUNT(l.id) as reply_count
            FROM vacation_periods p
            LEFT JOIN vacation_log l ON l.period_id = p.id
            GROUP BY p.id
            ORDER BY p.id DESC
            LIMIT 10
        """) as cur:
            periods = [dict(r) for r in await cur.fetchall()]

        current_period_id = None
        recent_replies = []
        if vacation_end:
            try:
                end_date_obj = date.fromisoformat(vacation_end)
                async with conn.execute(
                    "SELECT id FROM vacation_periods WHERE end_date = ? AND closed_at IS NULL",
                    (end_date_obj.isoformat(),)
                ) as cur:
                    row = await cur.fetchone()
                if row:
                    current_period_id = row["id"]
                    async with conn.execute("""
                        SELECT person_email, person_name, message_preview, replied_at
                        FROM vacation_log WHERE period_id = ?
                        ORDER BY replied_at DESC LIMIT 20
                    """, (current_period_id,)) as cur:
                        recent_replies = [dict(r) for r in await cur.fetchall()]
            except ValueError:
                pass

    enabled_env = os.getenv("VACATION_ENABLED", "")
    if enabled_env:
        enabled = enabled_env.lower() in ("1", "true", "yes")
    else:
        enabled_raw = await db.get_config("vacation_enabled", "false")
        enabled = str(enabled_raw).lower() in ("1", "true")

    return {
        "enabled": enabled,
        "end_date": vacation_end,
        "last_check": await db.get_config("vacation_last_check"),
        "current_period_id": current_period_id,
        "periods": periods,
        "recent_replies": recent_replies,
    }


async def get_period_log(period_id: int) -> list[dict]:
    """Full log for a given vacation period."""
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("""
            SELECT person_email, person_name, message_preview, replied_at
            FROM vacation_log WHERE period_id = ?
            ORDER BY replied_at ASC
        """, (period_id,)) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def generate_return_summary(period_id: int) -> dict:
    """
    AI-sorted return summary for a vacation period.
    Tries Gemini first, then OpenAI gpt-4o-mini, then plain list.
    """
    async with aiosqlite.connect(db.DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("""
            SELECT person_name, person_email, message_preview, replied_at
            FROM vacation_log WHERE period_id = ?
            ORDER BY replied_at ASC
        """, (period_id,)) as cur:
            replies = [dict(r) for r in await cur.fetchall()]

    if not replies:
        return {
            "total": 0,
            "summary": "No messages during your vacation.",
            "urgent": [],
            "can_wait": [],
        }

    messages_text = "\n".join(
        f"- {r['person_name']} ({r['person_email']}): {r['message_preview']}"
        for r in replies
    )

    prompt = f"""The user is returning from vacation. Here are all Webex messages that arrived during their absence:

{messages_text}

Sort them into "probably urgent" and "can wait". For urgent ones, briefly explain why.

Reply ONLY with valid JSON:
{{
  "urgent": [{{"name": "...", "preview": "...", "reason": "..."}}],
  "can_wait": [{{"name": "...", "preview": "..."}}],
  "summary": "X people messaged you. Y of them look urgent."
}}"""

    def _plain_fallback():
        return {
            "total": len(replies),
            "summary": f"{len(replies)} people messaged you: {', '.join(r['person_name'] for r in replies)}",
            "urgent": [],
            "can_wait": [{"name": r["person_name"], "preview": r["message_preview"]} for r in replies],
        }

    def _parse_llm_response(text: str) -> dict | None:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        try:
            data = _json.loads(text)
            data["total"] = len(replies)
            return data
        except (KeyError, _json.JSONDecodeError) as e:
            log.warning(f"Return summary JSON parse error: {e}")
            return None

    async with httpx.AsyncClient(timeout=60) as client:
        # Try Gemini first
        gemini_key = os.getenv("GEMINI_API_KEY", "")
        if gemini_key:
            try:
                r = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}",
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "systemInstruction": {
                            "parts": [{"text": "You are a helpful assistant. Sort messages by urgency. Only valid JSON."}]
                        },
                        "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024},
                    }
                )
                if r.status_code == 200:
                    raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = _parse_llm_response(raw)
                    if parsed:
                        return parsed
            except Exception as e:
                log.warning(f"Gemini call failed: {e}")

        # Fallback: OpenAI gpt-4o-mini
        openai_key = os.getenv("OPENAI_API_KEY", "")
        if openai_key:
            try:
                r = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {openai_key}"},
                    json={
                        "model": "gpt-4o-mini",
                        "temperature": 0.3,
                        "max_tokens": 1024,
                        "messages": [
                            {"role": "system", "content": "You are a helpful assistant. Sort messages by urgency. Only valid JSON."},
                            {"role": "user", "content": prompt},
                        ],
                    }
                )
                if r.status_code == 200:
                    raw = r.json()["choices"][0]["message"]["content"]
                    parsed = _parse_llm_response(raw)
                    if parsed:
                        return parsed
            except Exception as e:
                log.warning(f"OpenAI call failed: {e}")

    return _plain_fallback()


async def run_poll_loop() -> None:
    """Main loop: poll on the configured interval until interrupted."""
    interval_minutes = int(os.getenv("POLL_INTERVAL_MINUTES", "15"))
    log.info(f"Vacation bot started — polling every {interval_minutes} min")
    while True:
        try:
            result = await check_vacation_replies()
            log.info(f"Poll result: {result}")
        except Exception as e:
            log.exception(f"Poll loop error: {e}")
        await asyncio.sleep(interval_minutes * 60)


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run_poll_loop())
