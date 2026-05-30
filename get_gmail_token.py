"""
One-time setup script: fetch Gmail OAuth2 token.

Prerequisites:
  1. Google Cloud Console → Project → enable Gmail API
  2. Create an OAuth 2.0 Client ID (type: Desktop application)
  3. Download client_secret.json and place it in this directory
  4. Set GMAIL_CLIENT_ID + GMAIL_CLIENT_SECRET in .env

Usage:
  python get_gmail_token.py
  (run once; token is saved as gmail_token.json)
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
        print("ERROR: client_secret.json not found and GMAIL_CLIENT_ID/SECRET not set in .env.")
        raise SystemExit(1)

    print(">>> Opening browser for Google login …")
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
    print(f"✅ Gmail token saved: {TOKEN_FILE}")
    print("   Email sending at the end of your vacation is now enabled.")


if __name__ == "__main__":
    main()
