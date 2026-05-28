"""
Einmaliges Setup-Script: Gmail OAuth2 Token holen.

Voraussetzungen:
  1. Google Cloud Console → Projekt → Gmail API aktivieren
  2. OAuth 2.0 Client ID erstellen (Typ: Desktop-Anwendung)
  3. client_secret.json herunterladen und in diesen Ordner legen
  4. GMAIL_CLIENT_ID + GMAIL_CLIENT_SECRET in .env eintragen

Ausführen:
  python get_gmail_token.py
  (einmalig; token wird als gmail_token.json gespeichert)
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES        = ["https://www.googleapis.com/auth/gmail.send"]
CLIENT_ID     = os.getenv("GMAIL_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET", "")
TOKEN_FILE    = Path(os.getenv("SQLITE_PATH", "./data/tokens.json")).parent / "gmail_token.json"
SECRETS_FILE  = Path("client_secret.json")


def main() -> None:
    if SECRETS_FILE.exists():
        # Use downloaded client_secret.json (preferred)
        flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_FILE), SCOPES)
    elif CLIENT_ID and CLIENT_SECRET:
        # Build config from .env variables
        client_config = {
            "installed": {
                "client_id":     CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
                "token_uri":     "https://oauth2.googleapis.com/token",
                "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
            }
        }
        flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    else:
        print("FEHLER: client_secret.json nicht gefunden und GMAIL_CLIENT_ID/SECRET nicht in .env.")
        raise SystemExit(1)

    print(">>> Browser öffnet sich für Google-Login …")
    creds: Credentials = flow.run_local_server(port=0)

    token_data = {
        "token":         creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri":     creds.token_uri,
        "client_id":     creds.client_id,
        "client_secret": creds.client_secret,
        "scopes":        list(creds.scopes or SCOPES),
    }

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2))
    print(f"✅ Gmail-Token gespeichert: {TOKEN_FILE}")
    print("   E-Mail-Versand beim Urlaubsende ist jetzt aktiviert.")


if __name__ == "__main__":
    main()
