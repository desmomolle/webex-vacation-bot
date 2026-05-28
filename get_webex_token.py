"""
Einmaliges Setup-Script: Webex OAuth2 Token holen.

Dieses Script öffnet deinen Browser, lässt dich bei Webex einloggen
und speichert Access- + Refresh-Token in /data/tokens.json.

Voraussetzungen:
  - WEBEX_CLIENT_ID und WEBEX_CLIENT_SECRET in .env eingetragen
  - Redirect URI in developer.webex.com eingetragen: http://localhost:8888/callback

Ausführen:
  python get_webex_token.py
  (einmalig vor dem ersten docker compose up)
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
                b"<html><body><h2>✅ Fertig! Du kannst dieses Fenster schlie\xdfen.</h2>"
                b"<p>Token gespeichert. Bot kann gestartet werden.</p></body></html>"
            )
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Fehler: kein Code erhalten.")

    def log_message(self, *args) -> None:
        pass  # suppress request logs


def main() -> None:
    if not CLIENT_ID or not CLIENT_SECRET:
        print("FEHLER: WEBEX_CLIENT_ID und WEBEX_CLIENT_SECRET müssen in .env stehen.")
        raise SystemExit(1)

    print(">>> Browser öffnet sich für Webex-Login …")
    webbrowser.open(AUTH_URL)

    server = http.server.HTTPServer(("localhost", 8888), _CallbackHandler)
    print(">>> Warte auf Callback (http://localhost:8888/callback) …")
    while _auth_code is None:
        server.handle_request()

    print(">>> Code erhalten, tausche gegen Token …")
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
    print(f"✅ Token gespeichert: {TOKENS_FILE}")
    print("   Starte jetzt den Bot mit: docker compose up -d")


if __name__ == "__main__":
    main()
