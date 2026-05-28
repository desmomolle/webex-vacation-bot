"""
Webex OAuth2 token management.

Tokens are stored in /data/tokens.json (same volume as the SQLite DB).
Call `get_access_token()` before each Webex API request — it auto-refreshes
when the access token is less than 10 minutes from expiry.
"""
import json
import logging
import os
import time
from pathlib import Path

import httpx

import crypto
from crypto import InvalidToken

log = logging.getLogger("vacation-bot.auth")

TOKENS_FILE = Path(os.getenv("SQLITE_PATH", "/data/vacation.db")).parent / "tokens.json"
WEBEX_TOKEN_URL = "https://webexapis.com/v1/access_token"


def _load_tokens() -> dict:
    if TOKENS_FILE.exists():
        try:
            raw = TOKENS_FILE.read_text()
            try:
                raw = crypto.decrypt_str(raw)
            except InvalidToken:
                # File is plain JSON from before encryption was added — migrate on next save
                log.info("tokens.json is unencrypted (pre-migration) — will encrypt on next write")
            return json.loads(raw)
        except (json.JSONDecodeError, OSError):
            log.warning("Could not read tokens.json — starting fresh")
    return {}


def _save_tokens(tokens: dict) -> None:
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_FILE.write_text(crypto.encrypt_str(json.dumps(tokens, indent=2)))


def _is_expired(tokens: dict, margin_seconds: int = 600) -> bool:
    expires_at = tokens.get("expires_at", 0)
    return time.time() >= (expires_at - margin_seconds)


def _refresh_token_sync(tokens: dict) -> dict:
    client_id = os.getenv("WEBEX_CLIENT_ID", "")
    client_secret = os.getenv("WEBEX_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError("WEBEX_CLIENT_ID / WEBEX_CLIENT_SECRET not set — cannot refresh OAuth token")

    resp = httpx.post(WEBEX_TOKEN_URL, data={
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"],
    })
    resp.raise_for_status()
    data = resp.json()
    tokens["access_token"] = data["access_token"]
    tokens["expires_at"] = time.time() + data.get("expires_in", 43200)
    if "refresh_token" in data:
        tokens["refresh_token"] = data["refresh_token"]
    log.info("Webex access token refreshed, expires in %ds", data.get("expires_in", 0))
    return tokens


def get_access_token() -> str:
    """Return a valid Webex access token, refreshing OAuth tokens if needed.

    Priority:
    1. OAuth tokens in tokens.json (if present and valid / refreshable)
    2. WEBEX_PAT env var as static fallback (12h dev tokens)
    """
    tokens = _load_tokens()
    if tokens.get("access_token"):
        if _is_expired(tokens):
            if not tokens.get("refresh_token"):
                raise RuntimeError("OAuth access token expired and no refresh_token available — re-run get_webex_token.py")
            try:
                tokens = _refresh_token_sync(tokens)
                _save_tokens(tokens)
            except Exception as exc:
                log.error("Token refresh failed: %s", exc)
                raise
        return tokens["access_token"]

    # Static PAT fallback (dev/test usage)
    pat = os.getenv("WEBEX_PAT", "")
    if pat:
        return pat

    raise RuntimeError("No Webex credentials found — set WEBEX_PAT or run get_webex_token.py first")
