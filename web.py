"""
aiohttp web server for the Webex Vacation Bot status UI.

Routes:
  GET /          → renders index.html via Jinja2
  GET /api/status → JSON status payload
  GET /health    → 200 OK liveness probe
"""
import os
import json
import logging
from pathlib import Path

import aiohttp_jinja2
import jinja2
from aiohttp import web

import db

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


async def _build_status() -> dict:
    """Assemble the status dict from the database."""
    enabled_val = await db.get_config("enabled", "false")
    enabled = enabled_val.lower() in ("1", "true", "yes")

    end_date      = await db.get_config("end_date")
    last_check    = await db.get_config("last_check")
    period_id_str = await db.get_config("current_period_id")
    current_period_id = int(period_id_str) if period_id_str else None

    # Fetch recent replies for the current period (newest first, cap at 50)
    recent_replies: list[dict] = []
    if current_period_id:
        import aiosqlite
        db_path = os.getenv("SQLITE_PATH", "/data/vacation.db")
        async with aiosqlite.connect(db_path) as conn:
            conn.row_factory = aiosqlite.Row
            async with conn.execute(
                """
                SELECT person_name, person_email, message_preview, replied_at
                FROM vacation_log
                WHERE period_id = ?
                ORDER BY replied_at DESC
                LIMIT 50
                """,
                (current_period_id,),
            ) as cur:
                rows = await cur.fetchall()
                recent_replies = [dict(r) for r in rows]

    # Optional summary stored as JSON in config
    summary_raw = await db.get_config("summary")
    summary = None
    if summary_raw:
        try:
            summary = json.loads(summary_raw)
        except json.JSONDecodeError:
            logger.warning("Failed to parse summary JSON from config")

    return {
        "enabled": enabled,
        "end_date": end_date,
        "last_check": last_check,
        "current_period_id": current_period_id,
        "recent_replies": recent_replies,
        "summary": summary,
    }


@aiohttp_jinja2.template("index.html")
async def handle_index(request: web.Request) -> dict:
    status = await _build_status()
    return {"status": status}


async def handle_api_status(request: web.Request) -> web.Response:
    status = await _build_status()
    return web.json_response(status)


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def create_app() -> web.Application:
    app = web.Application()

    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    )

    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_api_status)
    app.router.add_get("/health", handle_health)

    return app


async def init_and_run() -> None:
    await db.init_db()
    app = create_app()
    port = int(os.getenv("WEB_PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    logger.info("Web UI listening on http://0.0.0.0:%d", port)
    await site.start()
    # Keep running until cancelled
    import asyncio
    await asyncio.Event().wait()


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(init_and_run())
