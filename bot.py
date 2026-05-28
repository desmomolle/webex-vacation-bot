"""
Webex Vacation Auto-Reply Bot — main entry point.

Runs two concurrent tasks:
  1. Polling loop: checks Webex for new DMs every POLL_INTERVAL_MINUTES
  2. Web server: status UI at http://localhost:8080
"""
import asyncio
import logging
import os
import signal

import db
import auth
import vacation
import web as web_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("vacation-bot")

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "15")) * 60  # seconds


async def _poll_loop() -> None:
    """Run vacation.check_vacation_replies() on a fixed interval."""
    log.info("Poll loop started — interval: %d min", POLL_INTERVAL // 60)
    while True:
        try:
            # Inject fresh access token into env before each poll
            try:
                token = auth.get_access_token()
                os.environ["WEBEX_PAT"] = token
            except RuntimeError as exc:
                log.error("Auth error: %s — skipping poll", exc)
                await asyncio.sleep(POLL_INTERVAL)
                continue

            result = await vacation.check_vacation_replies()
            log.info("Poll result: %s", result)

            # Track last check time for the status UI
            from datetime import datetime, timezone
            await db.set_config("last_check", datetime.now(timezone.utc).isoformat())

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("Unexpected error in poll loop: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)


async def _run_web() -> None:
    """Start the aiohttp status web server."""
    from aiohttp import web
    app = web_server.create_app()
    port = int(os.getenv("WEB_PORT", "8080"))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("Status UI: http://0.0.0.0:%d", port)
    await asyncio.Event().wait()  # run until cancelled


async def main() -> None:
    log.info("Webex Vacation Bot starting")
    await db.init_db()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        log.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    poll_task = asyncio.create_task(_poll_loop(), name="poll-loop")
    web_task  = asyncio.create_task(_run_web(),   name="web-server")

    await stop_event.wait()

    log.info("Shutting down gracefully…")
    poll_task.cancel()
    web_task.cancel()
    await asyncio.gather(poll_task, web_task, return_exceptions=True)
    log.info("Bye.")


if __name__ == "__main__":
    asyncio.run(main())
