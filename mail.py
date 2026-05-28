"""
Email sending module for the vacation bot.

Backend priority:
  1. Gmail OAuth2  — if GMAIL_CLIENT_ID + GMAIL_CLIENT_SECRET are set
  2. SMTP           — if SMTP_HOST + SMTP_USER are set
  3. Skip           — log a warning and return False
"""
import base64
import json
import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

log = logging.getLogger("vacation-bot.mail")

MAIL_TO = os.getenv("MAIL_TO", "")

# Gmail token is stored next to the SQLite DB
_SQLITE_PATH = os.getenv("SQLITE_PATH", "/data/vacation.db")
_TOKEN_PATH = Path(_SQLITE_PATH).parent / "gmail_token.json"


# ---------------------------------------------------------------------------
# Gmail OAuth2 backend
# ---------------------------------------------------------------------------

def _build_raw_message(subject: str, html_body: str, to: str) -> str:
    """Encode a MIME message as base64url string for the Gmail API."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["To"] = to
    msg["From"] = to  # send-as self; Gmail API uses the authenticated user
    msg.attach(MIMEText(html_body, "html"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return raw


async def _send_gmail_oauth(subject: str, html_body: str) -> bool:
    """Send via Gmail API using stored OAuth2 token. Returns True on success."""
    client_id = os.getenv("GMAIL_CLIENT_ID", "")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return False
    if not MAIL_TO:
        log.warning("MAIL_TO not set, skipping Gmail send")
        return False

    try:
        # Lazy import — only needed when Gmail backend is active
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        import asyncio
    except ImportError:
        log.error("google-auth / google-api-python-client not installed")
        return False

    # Load stored token
    if not _TOKEN_PATH.exists():
        log.error(f"Gmail token not found at {_TOKEN_PATH} — run the OAuth flow first")
        return False

    token_data = json.loads(_TOKEN_PATH.read_text())
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: creds.refresh(Request()))
            # Persist refreshed token
            _TOKEN_PATH.write_text(json.dumps({
                "token": creds.token,
                "refresh_token": creds.refresh_token,
            }))
        except Exception as e:
            log.error(f"Gmail token refresh failed: {e}")
            return False

    try:
        loop = asyncio.get_event_loop()
        raw = _build_raw_message(subject, html_body, MAIL_TO)

        def _send():
            service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()

        await loop.run_in_executor(None, _send)
        log.info(f"Gmail sent: '{subject}' → {MAIL_TO}")
        return True
    except Exception as e:
        log.error(f"Gmail send failed: {e}")
        return False


# ---------------------------------------------------------------------------
# SMTP backend
# ---------------------------------------------------------------------------

async def _send_smtp(subject: str, html_body: str) -> bool:
    """Send via SMTP using aiosmtplib. Returns True on success."""
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_password = os.getenv("SMTP_PASSWORD", "")
    if not smtp_host or not smtp_user:
        return False
    if not MAIL_TO:
        log.warning("MAIL_TO not set, skipping SMTP send")
        return False

    try:
        import aiosmtplib
    except ImportError:
        log.error("aiosmtplib not installed")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = MAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=smtp_port,
            username=smtp_user,
            password=smtp_password,
            start_tls=True,
        )
        log.info(f"SMTP sent: '{subject}' → {MAIL_TO}")
        return True
    except Exception as e:
        log.error(f"SMTP send failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def send_summary_email(subject: str, html_body: str) -> bool:
    """
    Send an email using the best available backend.
    Returns True if sent successfully, False if all backends are unavailable or failed.
    """
    # 1. Gmail OAuth2
    if os.getenv("GMAIL_CLIENT_ID") and os.getenv("GMAIL_CLIENT_SECRET"):
        if await _send_gmail_oauth(subject, html_body):
            return True
        log.warning("Gmail OAuth failed, trying SMTP fallback")

    # 2. SMTP
    if os.getenv("SMTP_HOST") and os.getenv("SMTP_USER"):
        if await _send_smtp(subject, html_body):
            return True
        log.warning("SMTP send failed")

    log.warning("No mail backend configured or all failed — email not sent")
    return False
