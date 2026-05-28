"""
aiohttp web server for the Webex Vacation Bot status UI.

Routes:
  GET /          → renders index.html via Jinja2
  GET /api/status → JSON status payload
  GET /health    → 200 OK liveness probe

  GET  /setup                  → setup wizard step 1
  POST /setup/step1            → save Webex OAuth credentials, redirect to auth
  GET  /setup/webex/auth       → redirect browser to Webex OAuth
  GET  /setup/webex/callback   → receive OAuth code, exchange for tokens
  GET  /setup/step2            → vacation settings form
  POST /setup/step2            → save vacation settings
  GET  /setup/step3            → optional settings form
  POST /setup/step3            → save optional settings
  GET  /setup/summary          → review all config
"""
import base64
import hmac
import os
import json
import secrets
import time
import logging
from pathlib import Path

import aiohttp_jinja2
import jinja2
import httpx
from aiohttp import web

import aiosqlite
import db

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Same path logic as auth.py uses
_DB_PATH = os.getenv("SQLITE_PATH", "/data/vacation.db")
TOKENS_PATH = Path(_DB_PATH).parent / "tokens.json"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_base_url(request: web.Request) -> str:
    """Derive the base URL from the incoming request Host header."""
    host = request.headers.get("Host", "localhost:8080")
    scheme = request.url.scheme  # 'http' or 'https'
    return f"{scheme}://{host}"


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------

def _check_setup_auth(request: web.Request) -> bool:
    """Check HTTP Basic Auth for setup routes. Only the password is verified."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8", errors="replace")
        _username, _, password = decoded.partition(":")
        expected = os.getenv("SETUP_PASSWORD", "")
        if not expected:
            # No password configured → deny access
            return False
        return hmac.compare_digest(password, expected)
    except Exception:
        return False


def _setup_auth_required(request: web.Request):
    """Return a 401 response if auth fails, else None."""
    if _check_setup_auth(request):
        return None
    return web.Response(
        status=401,
        headers={"WWW-Authenticate": 'Basic realm="Webex Vacation Bot Setup"'},
        text="Unauthorized",
    )


def _generate_csrf_token() -> str:
    return secrets.token_hex(32)


def _get_csrf_token(request: web.Request) -> str:
    """Return the existing CSRF cookie token or generate a fresh one."""
    return request.cookies.get("csrf_token") or _generate_csrf_token()


def _validate_csrf(request: web.Request, form_data: dict) -> bool:
    """Constant-time comparison of the submitted token against the cookie."""
    submitted = form_data.get("csrf_token", "")
    cookie_val = request.cookies.get("csrf_token", "")
    if not submitted or not cookie_val:
        return False
    return hmac.compare_digest(submitted, cookie_val)


def _mask(val) -> str:
    """Mask a secret value, keeping only the first 4 chars."""
    if not val:
        return ""
    if len(val) <= 4:
        return "****"
    return val[:4] + "****"


async def _all_config() -> dict:
    """Return all config rows as a plain dict."""
    async with aiosqlite.connect(_DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute("SELECT key, value FROM config") as cur:
            rows = await cur.fetchall()
            return {r["key"]: r["value"] for r in rows}


# ---------------------------------------------------------------------------
# Existing routes
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Setup wizard routes
# ---------------------------------------------------------------------------

async def handle_setup_get(request: web.Request) -> web.Response:
    """GET /setup — wizard entry point, step 1."""
    auth = _setup_auth_required(request)
    if auth:
        return auth
    config = await _all_config()
    csrf_token = _get_csrf_token(request)
    response = aiohttp_jinja2.render_template(
        "setup.html", request, {"step": 1, "config": config, "csrf_token": csrf_token}
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="Strict")
    return response


async def handle_setup_step1_post(request: web.Request) -> web.Response:
    """POST /setup/step1 — save Webex OAuth credentials."""
    auth = _setup_auth_required(request)
    if auth:
        return auth
    data = await request.post()
    if not _validate_csrf(request, data):
        return web.Response(status=403, text="CSRF validation failed")

    client_id = data.get("webex_client_id", "").strip()
    client_secret = data.get("webex_client_secret", "").strip()

    if client_id:
        await db.set_config("webex_client_id", client_id)
    if client_secret:
        await db.set_config("webex_client_secret", client_secret)

    raise web.HTTPFound("/setup/webex/auth")


async def handle_setup_webex_auth(request: web.Request) -> web.Response:
    """GET /setup/webex/auth — redirect browser to Webex OAuth consent page."""
    auth = _setup_auth_required(request)
    if auth:
        return auth
    client_id = await db.get_config("webex_client_id", "")
    base_url = _get_base_url(request)
    redirect_uri = f"{base_url}/setup/webex/callback"

    scope = "spark:messages_write spark:rooms_read spark:memberships_read"
    import urllib.parse
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": "setup",
    })
    webex_auth_url = f"https://webexapis.com/v1/authorize?{params}"

    raise web.HTTPFound(webex_auth_url)


async def handle_setup_webex_callback(request: web.Request) -> web.Response:
    """GET /setup/webex/callback — exchange OAuth code for tokens."""
    auth = _setup_auth_required(request)
    if auth:
        return auth
    code = request.rel_url.query.get("code", "")
    if not code:
        raise web.HTTPBadRequest(reason="Missing OAuth code parameter")

    client_id = await db.get_config("webex_client_id", "")
    client_secret = await db.get_config("webex_client_secret", "")
    base_url = _get_base_url(request)
    redirect_uri = f"{base_url}/setup/webex/callback"

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://webexapis.com/v1/access_token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )

    if resp.status_code != 200:
        logger.error("Token exchange failed: %s %s", resp.status_code, resp.text)
        raise web.HTTPBadGateway(reason=f"Webex token exchange failed: {resp.status_code}")

    token_data = resp.json()
    expires_at = int(time.time()) + int(token_data.get("expires_in", 3600))

    tokens = {
        "access_token": token_data.get("access_token", ""),
        "refresh_token": token_data.get("refresh_token", ""),
        "expires_at": expires_at,
    }

    TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_PATH.write_text(json.dumps(tokens, indent=2))
    logger.info("Webex tokens saved to %s", TOKENS_PATH)

    raise web.HTTPFound("/setup/step2")


async def handle_setup_step2_get(request: web.Request) -> web.Response:
    """GET /setup/step2 — vacation settings form."""
    auth = _setup_auth_required(request)
    if auth:
        return auth
    config = await _all_config()
    csrf_token = _get_csrf_token(request)
    response = aiohttp_jinja2.render_template(
        "setup.html", request, {"step": 2, "config": config, "csrf_token": csrf_token}
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="Strict")
    return response


async def handle_setup_step2_post(request: web.Request) -> web.Response:
    """POST /setup/step2 — save vacation settings."""
    auth = _setup_auth_required(request)
    if auth:
        return auth
    data = await request.post()
    if not _validate_csrf(request, data):
        return web.Response(status=403, text="CSRF validation failed")

    fields = {
        "vacation_end": data.get("vacation_end", "").strip(),
        "internal_domain": data.get("internal_domain", "cisco.com").strip() or "cisco.com",
        "vacation_message_internal": data.get("vacation_message_internal", "").strip(),
        "vacation_message_external": data.get("vacation_message_external", "").strip(),
        "vacation_enabled": "true",
    }

    for key, value in fields.items():
        await db.set_config(key, value)

    raise web.HTTPFound("/setup/step3")


async def handle_setup_step3_get(request: web.Request) -> web.Response:
    """GET /setup/step3 — optional settings form."""
    auth = _setup_auth_required(request)
    if auth:
        return auth
    config = await _all_config()
    csrf_token = _get_csrf_token(request)
    response = aiohttp_jinja2.render_template(
        "setup.html", request, {"step": 3, "config": config, "csrf_token": csrf_token}
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="Strict")
    return response


async def handle_setup_step3_post(request: web.Request) -> web.Response:
    """POST /setup/step3 — save optional settings."""
    auth = _setup_auth_required(request)
    if auth:
        return auth
    data = await request.post()
    if not _validate_csrf(request, data):
        return web.Response(status=403, text="CSRF validation failed")

    optional_fields = {
        "poll_interval": data.get("poll_interval", "").strip(),
        "mail_to": data.get("mail_to", "").strip(),
        "smtp_host": data.get("smtp_host", "").strip(),
        "smtp_port": data.get("smtp_port", "").strip(),
        "smtp_user": data.get("smtp_user", "").strip(),
        "smtp_password": data.get("smtp_password", "").strip(),
        "gemini_api_key": data.get("gemini_api_key", "").strip(),
        "openai_api_key": data.get("openai_api_key", "").strip(),
    }

    for key, value in optional_fields.items():
        if value:  # only persist non-empty values
            await db.set_config(key, value)

    raise web.HTTPFound("/setup/summary")


async def handle_setup_summary(request: web.Request) -> web.Response:
    """GET /setup/summary — review all config + auth status."""
    auth = _setup_auth_required(request)
    if auth:
        return auth
    config = await _all_config()
    webex_auth_done = TOKENS_PATH.exists()

    # Mask secrets before passing to template
    for key in ("webex_client_secret", "smtp_password", "gemini_api_key", "openai_api_key"):
        if key in config:
            config[key] = _mask(config[key])

    csrf_token = _get_csrf_token(request)
    response = aiohttp_jinja2.render_template(
        "setup.html",
        request,
        {
            "step": "summary",
            "config": config,
            "webex_auth_done": webex_auth_done,
            "csrf_token": csrf_token,
        },
    )
    response.set_cookie("csrf_token", csrf_token, httponly=True, samesite="Strict")
    return response


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> web.Application:
    app = web.Application()

    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    )

    # Existing routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_api_status)
    app.router.add_get("/health", handle_health)

    # Setup wizard routes
    app.router.add_get("/setup", handle_setup_get)
    app.router.add_post("/setup/step1", handle_setup_step1_post)
    app.router.add_get("/setup/webex/auth", handle_setup_webex_auth)
    app.router.add_get("/setup/webex/callback", handle_setup_webex_callback)
    app.router.add_get("/setup/step2", handle_setup_step2_get)
    app.router.add_post("/setup/step2", handle_setup_step2_post)
    app.router.add_get("/setup/step3", handle_setup_step3_get)
    app.router.add_post("/setup/step3", handle_setup_step3_post)
    app.router.add_get("/setup/summary", handle_setup_summary)

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
