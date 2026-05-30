"""
One-time setup script: fetch Webex OAuth2 token.

This script opens your browser, lets you log in to Webex,
and saves the access + refresh token to /data/tokens.json.

Prerequisites:
  - WEBEX_CLIENT_ID and WEBEX_CLIENT_SECRET set in .env
  - Redirect URI registered at developer.webex.com: http://localhost:8888/callback

Usage:
  python get_webex_token.py
  (run once before the first docker compose up)
"""
import http.server
import json
import os
import time
import urllib.parse
import webbrowser
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID     = os.getenv("WEBEX_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("WEBEX_CLIENT_SECRET", "")
REDIRECT_URI  = "http://localhost:8888/callback"
SCOPES        = "spark:messages_write spark:rooms_read spark:memberships_read"
TOKENS_FILE   = Path(os.getenv("SQLITE_PATH", "./data/tokens.json"))

AUTH_URL = (
    "https://webexapis.com/v1/authorize?"
    + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": "setup",
    })
)

_auth_code: str | None = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        global _auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        if code:
            _auth_code = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h2>\xe2\x9c\x85 Done! You can close this window.</h2>"
                b"<p>Token saved. The bot is ready to start.</p></body></html>"
            )
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Error: no code received.")

    def log_message(self, *args) -> None:
        pass  # suppress request logs


def main() -> None:
    if not CLIENT_ID or not CLIENT_SECRET:
        print("ERROR: WEBEX_CLIENT_ID and WEBEX_CLIENT_SECRET must be set in .env.")
        raise SystemExit(1)

    print(">>> Opening browser for Webex login …")
    webbrowser.open(AUTH_URL)

    server = http.server.HTTPServer(("localhost", 8888), _CallbackHandler)
    print(">>> Waiting for callback (http://localhost:8888/callback) …")
    while _auth_code is None:
        server.handle_request()

    print(">>> Code received, exchanging for token …")
    resp = httpx.post("https://webexapis.com/v1/access_token", data={
        "grant_type":    "authorization_code",
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          _auth_code,
        "redirect_uri":  REDIRECT_URI,
    })
    resp.raise_for_status()
    data = resp.json()

    tokens = {
        "access_token":  data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at":    time.time() + data.get("expires_in", 43200),
    }

    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_FILE.write_text(json.dumps(tokens, indent=2))
    print(f"✅ Token saved: {TOKENS_FILE}")
    print("   Now start the bot with: docker compose up -d")


if __name__ == "__main__":
    main()
