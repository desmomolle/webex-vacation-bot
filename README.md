# Webex Vacation Auto-Reply Bot

Automatische Abwesenheitsantworten für Webex — ähnlich einer E-Mail-Abwesenheitsnotiz, aber für Webex-Direktnachrichten.

**Was er tut:**
- Pollt deine Webex-DMs alle 15 Minuten
- Antwortet jeder Person genau einmal pro Urlaubsphase
- Unterscheidet automatisch zwischen internen und externen Kontakten (konfigurierbare Domain)
- Vollständiges Protokoll: wer wann geschrieben hat
- Deaktiviert sich automatisch am Rückkehrdatum
- Bot per Klick aktivieren/deaktivieren — kein Terminal nötig

**Was er NICHT tut:**
- Gruppenräume oder Spaces beantworten (nur 1:1 DMs)
- Nachrichten weiterleiten oder speichern

**Voraussetzungen:**
- Docker Desktop (Windows/Mac) oder Docker auf Linux/NAS
- Webex-Account + einmalige App-Registrierung (~5 Minuten)
- Keine Programmierkenntnisse nötig

|  |  |
|---|---|
| ![Login](docs/screenshot-login.png) | ![Status-Seite](docs/screenshot-status.png) |
| *Passwortgeschützter Login* | *Status-Seite mit Protokoll* |

---

## Schnellstart

> **Tipp für Synology/QNAP:** Schritt 3 entfällt — alles im Browser. Kein Terminal nötig.

### 1. Webex App registrieren

Öffne [developer.webex.com](https://developer.webex.com) → **My Webex Apps** → **Create a New App** → **Integration**

Wichtige Felder:
- **Redirect URI:** `http://localhost:8080/setup/webex/callback`
  *(Auf NAS: `http://nas-ip:8080/setup/webex/callback`)*
- **Scopes:** `spark:messages_write`, `spark:rooms_read`, `spark:memberships_read`

Kopiere **Client ID** und **Client Secret**.

### 2. Projekt herunterladen & konfigurieren

```bash
git clone https://github.com/desmomolle/webex-vacation-bot.git
cd webex-vacation-bot
cp .env.example .env
```

Öffne `.env` und trage ein:
```
MY_WEBEX_EMAIL=dein.name@cisco.com
WEBEX_CLIENT_ID=...
WEBEX_CLIENT_SECRET=...
```

### 3. Bot starten

```bash
docker compose up -d
```

### 4. Setup im Browser abschließen

Öffne **[http://localhost:8080/setup](http://localhost:8080/setup)**
*(Auf NAS: `http://nas-ip:8080/setup`)*

Das Zugangs-Passwort steht in den Logs:
```bash
docker logs webex-vacation-bot 2>&1 | grep "ACCESS PASSWORD"
```

Mit diesem Passwort meldest du dich an — es schützt sowohl die Status-Seite
als auch den Einrichtungs-Wizard. Setze `SETUP_PASSWORD=...` in der `.env`,
wenn du ein festes Passwort möchtest.

Der Wizard führt dich durch:
1. Webex autorisieren (Browser-Login, einmalig)
2. Urlaubskonfiguration (Rückkehrdatum, Templates, interne Domain)
3. Optionales (E-Mail-Report, KI-Summary)
4. Zusammenfassung & Bot starten

**Fertig.** Status und Steuerung unter [http://localhost:8080](http://localhost:8080).

---

## Bot steuern

**Per Browser:**
`http://localhost:8080` → Button **"Deaktivieren"** oder **"Aktivieren"** — kein Terminal nötig.

Der Bot deaktiviert sich außerdem automatisch am konfigurierten Rückkehrdatum.

**Container stoppen** (vollständig beenden):
```bash
docker compose down
```

---

## Deployment-Varianten

### Windows / Mac (Docker Desktop)

Genau wie im Schnellstart. Docker Desktop muss beim Start laufen (`restart: unless-stopped` sorgt für Autostart).

### Linux

```bash
curl -fsSL https://get.docker.com | sh   # Docker installieren
docker compose up -d
```
Browser → `http://localhost:8080/setup`

### Synology NAS (DSM 7.2+)

1. **Container Manager** → **Projekt** → **Erstellen**
2. Projektdateien in einen NAS-Ordner hochladen (z.B. `/docker/webex-vacation-bot`)
3. `docker-compose.yml` auswählen
4. Umgebungsvariablen setzen: `MY_WEBEX_EMAIL`, `WEBEX_CLIENT_ID`, `WEBEX_CLIENT_SECRET`
5. Container starten → Browser: `http://nas-ip:8080/setup`

Alles weitere im Wizard — kein Terminal, kein Python.

### QNAP NAS

1. **Container Station** → **Anwendungen** → **Erstellen**
2. `docker-compose.yml` hochladen
3. Umgebungsvariablen setzen: `MY_WEBEX_EMAIL`, `WEBEX_CLIENT_ID`, `WEBEX_CLIENT_SECRET`
4. Container starten → Browser: `http://nas-ip:8080/setup`

> NAS-Vorteil: Bot läuft 24/7 ohne PC. SQLite-Datei liegt im NAS-Volume und wird automatisch mitgesichert.

### Raspberry Pi

```bash
curl -fsSL https://get.docker.com | sh
docker compose up -d
```
Browser → `http://raspberry-pi-ip:8080/setup`

---

## Optional — E-Mail-Report bei Urlaubsende

Konfigurierbar im Setup-Wizard (Schritt 3) oder manuell in `.env`:

### Option A — Gmail OAuth (empfohlen, kein App-Passwort)

1. [Google Cloud Console](https://console.cloud.google.com) → Gmail API aktivieren → OAuth 2.0 Client ID erstellen (Typ: Desktop)
2. Im Wizard: Gmail-Button klicken → Browser-Login → fertig

Oder manuell:
```
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...
MAIL_TO=dein.name@cisco.com
```
Dann einmalig im Projektordner: `python get_gmail_token.py`

### Option B — SMTP

Im Wizard konfigurierbar oder in `.env`:
```
MAIL_TO=dein.name@cisco.com
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USER=dein.name@firma.com
SMTP_PASSWORD=...
```
Funktioniert mit Outlook, Cisco Mail, Gmail (App-Passwort).

---

## Optional — KI-Summary

Klassifiziert Nachrichten bei Urlaubsende automatisch in "dringend / kann warten".

Im Wizard (Schritt 3) oder in `.env` — einen Key reicht, Gemini hat Vorrang:
```
GEMINI_API_KEY=...    # Google Gemini Flash (empfohlen)
OPENAI_API_KEY=...    # OpenAI GPT-4o-mini
```
Kosten: einmaliger Call bei Urlaubsende, < 1000 Tokens → Cent-Bereich.

---

## Logs & Diagnose

**Live-Logs:**
```bash
docker logs webex-vacation-bot -f
```

**Zugangs-Passwort aus Logs lesen:**
```bash
docker logs webex-vacation-bot 2>&1 | grep "ACCESS PASSWORD"
```

**Auf Synology:** Container Manager → Container auswählen → Tab **"Log"**

**Auf QNAP:** Container Station → Container → **"Protokoll"**

**Typische Log-Meldungen:**
- `Poll result:` — Ergebnis des 15-Minuten-Checks
- `Replied to:` — wer eine Antwort erhalten hat
- `vacation ended — auto-disabled` — Bot hat sich automatisch deaktiviert
- `Token refresh` — Webex-Token wurde erneuert (normal, kein Handlungsbedarf)
- `ACCESS PASSWORD` — einmalig beim ersten Start (Login-Passwort)

---

## Sicherheit

- **Login auf allem** — Status-Seite, Protokoll, API und Wizard sind hinter einem Passwort-Login (signierte Session-Cookies). Ohne Login sieht niemand, wer dir geschrieben hat. Nur `/health` ist öffentlich. Passwort wird beim ersten Start automatisch generiert und in den Logs angezeigt; festes Passwort: `SETUP_PASSWORD=...` in `.env`.
- **Tokens verschlüsselt** — `data/tokens.json` ist von Anfang an Fernet-verschlüsselt (auch direkt nach der Einrichtung). Key in `data/.key`, wird automatisch beim ersten Start erzeugt.
- **`data/`-Ordner sichern** — enthält Verschlüsselungs-Key, Session-Key + Datenbank. Ohne den Key sind gespeicherte Tokens nicht wiederherstellbar.
- **OAuth abgesichert** — der `state`-Parameter wird gegen einen einmaligen Cookie validiert (Schutz gegen OAuth-CSRF / Code-Injection).
- **Secrets maskiert** — Client Secret, API-Keys und SMTP-Passwort werden in der Summary nur als `abcd****` angezeigt.
- **CSRF-Schutz** — alle Formulare und die Toggle-API per Double-Submit-Token gesichert; Session-Cookies mit `SameSite=Lax`, `secure` automatisch bei HTTPS.

> Port 8080 sollte **nicht** direkt aus dem Internet erreichbar sein. Für externen Zugriff: VPN oder Cloudflare Tunnel mit Access-Policy nutzen.

---

## Häufige Fragen

**Der Wizard öffnet sich, aber Webex-Autorisierung schlägt fehl**
→ Redirect URI in developer.webex.com prüfen: muss exakt `http://localhost:8080/setup/webex/callback` lauten (oder NAS-IP statt localhost).

**"Token refresh failed"**
→ Client ID oder Client Secret falsch. Werte aus developer.webex.com erneut kopieren und im Wizard neu eingeben.

**Status-Seite zeigt immer "Inaktiv"**
→ Im Wizard Schritt 2 prüfen ob Urlaubskonfiguration gespeichert wurde, oder direkt auf `http://localhost:8080` den Aktivieren-Button klicken.

**Der Bot antwortet nicht auf Testnachrichten**
→ Nachrichten müssen 1:1 DMs sein (keine Gruppenräume). Logs prüfen: `docker logs webex-vacation-bot`.

**Nachrichten aus der Vergangenheit werden beantwortet**
→ Normales Verhalten beim ersten Start. Der Bot verarbeitet alle DMs ab dem Urlaubsphasen-Start. Antworten werden nie doppelt gesendet.

**Wie sehe ich wer mir geschrieben hat?**
→ Status-Seite `http://localhost:8080` zeigt das vollständige Protokoll. Optional: E-Mail-Report nach Urlaubsende konfigurieren.
