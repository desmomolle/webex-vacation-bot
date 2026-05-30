"""
aiohttp web server for the Webex Vacation Bot status UI.

All routes require a logged-in session (signed cookie) except /health, /login
and /static. The login password is SETUP_PASSWORD (auto-generated on first
start and printed to the logs, or set explicitly in .env).

Routes:
  GET  /          → renders index.html via Jinja2 (status dashboard)
  GET  /api/status → JSON status payload
  POST /api/toggle → flip vacation_enabled (CSRF-protected)
  GET  /health    → 200 OK liveness probe (public)

  GET  /login     → login form (public)
  POST /login     → verify password, set session cookie
  GET  /logout    → clear session

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
import hashlib
import hmac
import os
import json
import secrets
import stat
import time
import logging
import urllib.parse
from pathlib import Path

import aiohttp_jinja2
import jinja2
import httpx
from aiohttp import web

import aiosqlite
import auth
import db
import demo

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Same path logic as auth.py uses
_DB_PATH = os.getenv("SQLITE_PATH", "/data/vacation.db")
_DATA_DIR = Path(_DB_PATH).parent
TOKENS_PATH = _DATA_DIR / "tokens.json"

# Session cookie lifetime
SESSION_MAX_AGE = 7 * 24 * 3600  # 7 days


# ---------------------------------------------------------------------------
# Cookie helper
# ---------------------------------------------------------------------------

def _is_https(request: web.Request) -> bool:
    # Honour reverse-proxy header, fall back to the request scheme.
    xf_proto = request.headers.get("X-Forwarded-Proto", "")
    if xf_proto:
        return xf_proto.split(",")[0].strip() == "https"
    return request.url.scheme == "https"


def _set_cookie(response: web.Response, request: web.Request, name: str,
                value: str, *, max_age=None, samesite="Lax", http_only=True):
    response.set_cookie(
        name, value,
        max_age=max_age,
        httponly=http_only,
        samesite=samesite,
        secure=_is_https(request),
        path="/",
    )


# ---------------------------------------------------------------------------
# Session (signed cookie)
# ---------------------------------------------------------------------------

def _session_secret() -> bytes:
    """Return the persistent HMAC secret used to sign session cookies."""
    path = _DATA_DIR / ".session_key"
    if path.exists():
        return path.read_bytes().strip()
    secret = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(secret)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
    logger.info("Session signing key generated at %s", path)
    return secret


def _make_session_token() -> str:
    issued = str(int(time.time()))
    sig = hmac.new(_session_secret(), issued.encode(), hashlib.sha256).hexdigest()
    return f"{issued}.{sig}"


def _valid_session(request: web.Request) -> bool:
    raw = request.cookies.get("session", "")
    issued_str, _, sig = raw.partition(".")
    if not issued_str or not sig:
        return False
    expected = hmac.new(_session_secret(), issued_str.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        issued = int(issued_str)
    except ValueError:
        return False
    return (time.time() - issued) < SESSION_MAX_AGE


# ---------------------------------------------------------------------------
# CSRF (double-submit cookie)
# ---------------------------------------------------------------------------

def _generate_csrf_token() -> str:
    return secrets.token_hex(32)


def _get_csrf_token(request: web.Request) -> str:
    """Return the existing CSRF cookie token or generate a fresh one."""
    return request.cookies.get("csrf_token") or _generate_csrf_token()


def _validate_csrf(request: web.Request, submitted: str) -> bool:
    """Constant-time comparison of the submitted token against the cookie."""
    cookie_val = request.cookies.get("csrf_token", "")
    if not submitted or not cookie_val:
        return False
    return hmac.compare_digest(submitted, cookie_val)


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def _get_base_url(request: web.Request) -> str:
    """Derive the base URL from the incoming request Host header."""
    host = request.headers.get("Host", "localhost:8080")
    scheme = "https" if _is_https(request) else "http"
    return f"{scheme}://{host}"


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
# Auth middleware
# ---------------------------------------------------------------------------

PUBLIC_PATHS = {"/health", "/login", "/logout"}


@web.middleware
async def auth_middleware(request: web.Request, handler):
    path = request.path
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await handler(request)

    if _valid_session(request):
        return await handler(request)

    # Unauthenticated
    if path.startswith("/api/"):
        return web.json_response({"error": "unauthorized"}, status=401)

    nxt = urllib.parse.quote(request.path_qs, safe="")
    raise web.HTTPFound(f"/login?next={nxt}")


# ---------------------------------------------------------------------------
# Login routes
# ---------------------------------------------------------------------------

async def handle_login_get(request: web.Request) -> web.Response:
    if _valid_session(request):
        raise web.HTTPFound("/")
    csrf_token = _get_csrf_token(request)
    nxt = request.rel_url.query.get("next", "/")
    response = aiohttp_jinja2.render_template(
        "login.html", request,
        {"csrf_token": csrf_token, "next": nxt, "error": None, "demo": demo.is_demo()},
    )
    _set_cookie(response, request, "csrf_token", csrf_token, samesite="Strict")
    return response


async def handle_login_post(request: web.Request) -> web.Response:
    data = await request.post()
    if not _validate_csrf(request, data.get("csrf_token", "")):
        return web.Response(status=403, text="CSRF validation failed")

    password = data.get("password", "")
    expected = os.getenv("SETUP_PASSWORD", "")
    nxt = data.get("next", "/") or "/"
    # Only allow same-site relative redirects
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"

    if not expected or not hmac.compare_digest(password, expected):
        csrf_token = _get_csrf_token(request)
        response = aiohttp_jinja2.render_template(
            "login.html", request,
            {"csrf_token": csrf_token, "next": nxt, "error": "Wrong password.",
             "demo": demo.is_demo()},
        )
        response.set_status(401)
        _set_cookie(response, request, "csrf_token", csrf_token, samesite="Strict")
        return response

    response = web.HTTPFound(nxt)
    _set_cookie(response, request, "session", _make_session_token(),
                max_age=SESSION_MAX_AGE, samesite="Lax")
    return response


async def handle_logout(request: web.Request) -> web.Response:
    response = web.HTTPFound("/login")
    response.del_cookie("session", path="/")
    return response


# ---------------------------------------------------------------------------
# Status routes
# ---------------------------------------------------------------------------

async def _build_status() -> dict:
    """Assemble the status dict from the database."""
    enabled_val = await db.get_config("vacation_enabled", "false")
    enabled = enabled_val.lower() in ("1", "true", "yes")

    end_date      = await db.get_config("end_date")
    last_check    = await db.get_config("last_check")
    period_id_str = await db.get_config("current_period_id")
    current_period_id = int(period_id_str) if period_id_str else None

    # Fetch recent replies for the current period (newest first, cap at 50)
    recent_replies: list[dict] = []
    if current_period_id:
        async with aiosqlite.connect(_DB_PATH) as conn:
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


async def handle_index(request: web.Request) -> web.Response:
    status = await _build_status()
    csrf_token = _get_csrf_token(request)
    response = aiohttp_jinja2.render_template(
        "index.html", request, {"status": status, "csrf_token": csrf_token, "demo": demo.is_demo()},
    )
    _set_cookie(response, request, "csrf_token", csrf_token, samesite="Strict")
    return response


async def handle_api_status(request: web.Request) -> web.Response:
    status = await _build_status()
    return web.json_response(status)


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def handle_api_vacation(request: web.Request) -> web.Response:
    """POST /api/vacation — set the return date and activate/deactivate the
    auto-reply (CSRF-protected). This is the recurring control on the dashboard;
    the one-time setup wizard no longer handles it.

    JSON body: {"enabled": bool, "return_date": "YYYY-MM-DD"}
    """
    if not _validate_csrf(request, request.headers.get("X-CSRF-Token", "")):
        return web.json_response({"error": "csrf"}, status=403)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid body"}, status=400)

    enabled = bool(data.get("enabled"))
    return_date = (data.get("return_date") or "").strip()

    if enabled and not return_date:
        return web.json_response({"error": "return_date required to activate"}, status=400)

    if return_date:
        await db.set_config("vacation_end", return_date)
        await db.set_config("end_date", return_date)  # keep status display in sync

    await db.set_config("vacation_enabled", "true" if enabled else "false")
    return web.json_response({"enabled": enabled, "return_date": return_date})


# ---------------------------------------------------------------------------
# Setup wizard routes
# ---------------------------------------------------------------------------

def _render_setup(request: web.Request, ctx: dict) -> web.Response:
    csrf_token = _get_csrf_token(request)
    ctx = {**ctx, "csrf_token": csrf_token, "demo": demo.is_demo()}
    response = aiohttp_jinja2.render_template("setup.html", request, ctx)
    _set_cookie(response, request, "csrf_token", csrf_token, samesite="Strict")
    return response


async def handle_setup_get(request: web.Request) -> web.Response:
    """GET /setup — wizard entry point, step 1."""
    config = await _all_config()
    return _render_setup(request, {"step": 1, "config": config})


async def handle_setup_step1_post(request: web.Request) -> web.Response:
    """POST /setup/step1 — save Webex OAuth credentials."""
    data = await request.post()
    if not _validate_csrf(request, data.get("csrf_token", "")):
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
    if demo.is_demo():
        # Skip the real Webex round-trip: write fake tokens and continue.
        demo.stub_tokens()
        logger.info("DEMO: Webex OAuth stubbed, fake tokens written")
        raise web.HTTPFound("/setup/step2")

    client_id = await db.get_config("webex_client_id", "")
    base_url = _get_base_url(request)
    redirect_uri = f"{base_url}/setup/webex/callback"

    state = secrets.token_urlsafe(24)
    scope = "spark:messages_write spark:rooms_read spark:memberships_read"
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    })
    webex_auth_url = f"https://webexapis.com/v1/authorize?{params}"

    response = web.HTTPFound(webex_auth_url)
    # Lax so the cookie survives the top-level redirect back from Webex.
    _set_cookie(response, request, "oauth_state", state, max_age=600, samesite="Lax")
    return response


async def handle_setup_webex_callback(request: web.Request) -> web.Response:
    """GET /setup/webex/callback — exchange OAuth code for tokens."""
    # Validate state to prevent OAuth login-CSRF / code injection.
    state = request.rel_url.query.get("state", "")
    cookie_state = request.cookies.get("oauth_state", "")
    if not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
        raise web.HTTPBadRequest(reason="Invalid OAuth state")

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

    # Store encrypted from the start (auth._save_tokens applies Fernet).
    auth._save_tokens(tokens)
    logger.info("Webex tokens saved (encrypted) to %s", TOKENS_PATH)

    response = web.HTTPFound("/setup/step2")
    response.del_cookie("oauth_state", path="/")
    return response


async def handle_setup_step2_get(request: web.Request) -> web.Response:
    """GET /setup/step2 — vacation settings form."""
    config = await _all_config()
    return _render_setup(request, {"step": 2, "config": config})


async def handle_setup_step2_post(request: web.Request) -> web.Response:
    """POST /setup/step2 — save vacation settings."""
    data = await request.post()
    if not _validate_csrf(request, data.get("csrf_token", "")):
        return web.Response(status=403, text="CSRF validation failed")

    # One-time reply configuration only. The return date and activation are
    # set later on the dashboard (POST /api/vacation), so the wizard never has
    # to be re-run when going on vacation.
    fields = {
        "internal_domain": data.get("internal_domain", "cisco.com").strip() or "cisco.com",
        "vacation_message_internal": data.get("vacation_message_internal", "").strip(),
        "vacation_message_external": data.get("vacation_message_external", "").strip(),
    }

    for key, value in fields.items():
        await db.set_config(key, value)

    raise web.HTTPFound("/setup/step3")


async def handle_setup_step3_get(request: web.Request) -> web.Response:
    """GET /setup/step3 — optional settings form."""
    config = await _all_config()
    return _render_setup(request, {"step": 3, "config": config})


async def handle_setup_step3_post(request: web.Request) -> web.Response:
    """POST /setup/step3 — save optional settings."""
    data = await request.post()
    if not _validate_csrf(request, data.get("csrf_token", "")):
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
    config = await _all_config()
    webex_auth_done = TOKENS_PATH.exists()

    # Mask secrets before passing to template
    for key in ("webex_client_secret", "smtp_password", "gemini_api_key", "openai_api_key"):
        if key in config:
            config[key] = _mask(config[key])

    return _render_setup(request, {
        "step": "summary",
        "config": config,
        "webex_auth_done": webex_auth_done,
    })


# ---------------------------------------------------------------------------
# Settings (edit config after setup)
# ---------------------------------------------------------------------------

_SETTINGS_SECRET_KEYS = ("smtp_password", "gemini_api_key", "openai_api_key")


async def handle_settings_get(request: web.Request) -> web.Response:
    """GET /settings — edit all configuration after the initial wizard."""
    config = await _all_config()
    secrets_set = {k: bool(config.get(k)) for k in _SETTINGS_SECRET_KEYS}
    csrf_token = _get_csrf_token(request)
    response = aiohttp_jinja2.render_template("settings.html", request, {
        "config": config,
        "secrets_set": secrets_set,
        "saved": request.rel_url.query.get("saved") == "1",
        "csrf_token": csrf_token,
        "demo": demo.is_demo(),
    })
    _set_cookie(response, request, "csrf_token", csrf_token, samesite="Strict")
    return response


async def handle_settings_post(request: web.Request) -> web.Response:
    """POST /settings — persist edited configuration."""
    data = await request.post()
    if not _validate_csrf(request, data.get("csrf_token", "")):
        return web.Response(status=403, text="CSRF validation failed")

    # Note: the return date and activation live on the dashboard
    # (POST /api/vacation), not here — Settings is one-time configuration only.

    # Required fields — only overwrite when a non-empty value is provided
    for key in ("internal_domain", "vacation_message_internal", "vacation_message_external"):
        val = data.get(key, "").strip()
        if val:
            await db.set_config(key, val)

    # Optional, non-secret — allow clearing
    for key in ("mail_to", "smtp_host", "smtp_port", "smtp_user"):
        await db.set_config(key, data.get(key, "").strip())

    # Secrets — only overwrite when a new value is entered (blank keeps current)
    for key in _SETTINGS_SECRET_KEYS:
        val = data.get(key, "").strip()
        if val:
            await db.set_config(key, val)

    raise web.HTTPFound("/settings?saved=1")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> web.Application:
    app = web.Application(middlewares=[auth_middleware])

    aiohttp_jinja2.setup(
        app,
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
    )

    # Auth
    app.router.add_get("/login", handle_login_get)
    app.router.add_post("/login", handle_login_post)
    app.router.add_get("/logout", handle_logout)

    # Status / API
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/status", handle_api_status)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/api/vacation", handle_api_vacation)

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

    # Settings
    app.router.add_get("/settings", handle_settings_get)
    app.router.add_post("/settings", handle_settings_post)

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
