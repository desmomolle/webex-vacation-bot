# Webex Vacation Auto-Reply Bot

Ein schlanker, selbst gehosteter Bot der automatisch auf eingehende Webex-Direktnachrichten antwortet, während du im Urlaub bist — ähnlich einer klassischen E-Mail-Abwesenheitsnotiz, aber für Webex Messaging.

**Was er tut:**
- Pollt deine Webex-DMs alle 15 Minuten
- Antwortet jeder Person genau einmal pro Urlaubsphase
- Unterscheidet automatisch zwischen internen Cisco-Kollegen und externen Kontakten (unterschiedliche Templates)
- Führt ein vollständiges Protokoll: wer wann geschrieben hat
- Deaktiviert sich automatisch wenn das Rückkehrdatum erreicht ist
- Status-Seite im Browser: `http://localhost:8080`

**Was er NICHT tut:**
- Nachrichten als gelesen markieren (bleiben unberührt)
- Gruppenräume oder Spaces beantworten (nur 1:1 DMs)
- Nachrichten weiterleiten oder speichern

**Voraussetzungen:**
- Docker Desktop (Windows/Mac) oder Docker auf Linux/NAS
- Webex-Account + einmalige App-Registrierung (~5 Minuten)
- Python 3.11+ (nur für das einmalige Token-Setup)
- Keine Programmierkenntnisse nötig

---

## Schritt 1 — Webex App registrieren

> Dieser Schritt ist einmalig. Du brauchst einen OAuth-Token damit der Bot auch nach 12 Stunden noch funktioniert.

1. Öffne [developer.webex.com](https://developer.webex.com) und logge dich mit deinem Cisco-Account ein
2. Klicke oben rechts auf deinen Avatar → **"My Webex Apps"**
3. Klicke auf **"Create a New App"** → wähle **"Integration"**
4. Fülle das Formular aus:
   - **App Name:** z.B. `Mein Abwesenheitsbot`
   - **App Icon:** beliebig
   - **Description:** optional
   - **Redirect URI(s):** `http://localhost:8888/callback`
   - **Scopes:** Aktiviere diese drei:
     - `spark:messages_write`
     - `spark:rooms_read`
     - `spark:memberships_read`
5. Klicke **"Add Integration"**
6. Kopiere **Client ID** und **Client Secret** — du brauchst sie gleich

---

## Schritt 2 — Projekt einrichten

```bash
# Projektordner herunterladen / klonen
git clone https://github.com/desmomolle/webex-vacation-bot.git
cd webex-vacation-bot

# Konfigurationsdatei erstellen
cp .env.example .env
```

Öffne `.env` und trage mindestens ein:

```env
MY_WEBEX_EMAIL=dein.name@cisco.com
WEBEX_CLIENT_ID=abc123...          # aus Schritt 1
WEBEX_CLIENT_SECRET=xyz789...      # aus Schritt 1
```

Alle weiteren Einstellungen (Urlaubsdaten, Templates, Domain) kannst du im Browser-Wizard konfigurieren.

---

## Schritt 3 — Bot starten

```bash
docker compose up -d
```

---

## Schritt 4 — Setup-Wizard öffnen

Öffne im Browser: **[http://localhost:8080/setup](http://localhost:8080/setup)**

> Auf Synology/QNAP: `http://nas-ip:8080/setup`

Der Wizard führt dich durch:
1. **Webex autorisieren** — Browser-Login, Token wird automatisch gespeichert
2. **Urlaubskonfiguration** — Rückkehrdatum, interne Domain, Nachrichten-Templates
3. **Optionales** — E-Mail-Report, KI-Summary, Poll-Intervall
4. **Zusammenfassung** — Überblick aller Einstellungen, Bot-Status

Kein Terminal oder Python nötig für den Setup-Prozess.

---

## Schritt 5 — Status prüfen

---

## Schritt 5 — Status prüfen

Öffne `http://localhost:8080` in deinem Browser. Du siehst:

- **Aktiv / Inaktiv** — ob der Bot gerade antwortet
- **Urlaubsende** — konfiguriertes Rückkehrdatum
- **Letzter Poll** — wann der Bot zuletzt Nachrichten geprüft hat
- **Protokoll** — wer dir wann geschrieben hat (Name, E-Mail, Vorschau, Zeitpunkt)
- **Return Summary** — KI-Einschätzung "dringend / kann warten" nach Urlaubsende (falls KI-Key konfiguriert)

---

## Logs & Diagnose

**Live-Logs anzeigen:**
```bash
docker logs webex-vacation-bot -f
```

**Setup-Passwort aus den Logs lesen** (nur beim ersten Start sichtbar):
```bash
docker logs webex-vacation-bot 2>&1 | grep "SETUP WIZARD PASSWORD"
```

**Letzten 50 Zeilen anzeigen:**
```bash
docker logs webex-vacation-bot --tail 50
```

**Auf Synology (Container Manager):**
Container auswählen → Tab "Log" → Logs werden live angezeigt.

**Auf QNAP (Container Station):**
Container auswählen → "Protokoll" → live oder als Datei exportieren.

**Was du in den Logs siehst:**
- `Poll result:` — Ergebnis jedes 15-Minuten-Checks
- `Replied to:` — wer eine Antwort bekommen hat
- `vacation ended — auto-disabled` — Bot hat sich automatisch deaktiviert
- `Token refresh` — Webex-Token wurde erneuert (normal)
- `SETUP WIZARD PASSWORD` — einmalig beim ersten Start wenn kein Passwort gesetzt

---

## Bot stoppen / Urlaub beenden

```bash
# Bot stoppen
docker compose down

# Oder: Urlaub vorzeitig beenden (Bot deaktiviert sich automatisch am VACATION_END_DATE)
# Dafür VACATION_ENABLED=false in .env setzen, dann:
docker compose restart
```

---

## Optional — E-Mail-Report bei Urlaubsende

Wenn du nach dem Urlaub einen Zusammenfassungs-Report per E-Mail erhalten möchtest:

### Option A — Gmail (empfohlen)

1. Öffne die [Google Cloud Console](https://console.cloud.google.com)
2. Erstelle ein neues Projekt (oder nutze ein bestehendes)
3. Aktiviere die **Gmail API** unter "APIs & Dienste"
4. Erstelle unter "Anmeldedaten" eine **OAuth 2.0 Client-ID** (Typ: Desktop-Anwendung)
5. Lade `client_secret.json` herunter und lege sie in den Projektordner
6. Trage in `.env` ein:
   ```env
   MAIL_TO=dein.name@cisco.com
   GMAIL_CLIENT_ID=...
   GMAIL_CLIENT_SECRET=...
   ```
7. Führe einmalig aus:
   ```bash
   pip install google-auth google-auth-oauthlib google-api-python-client
   python get_gmail_token.py
   ```

### Option B — SMTP (Outlook, Cisco Mail, Gmail mit App-Passwort)

Trage in `.env` ein:
```env
MAIL_TO=dein.name@cisco.com
SMTP_HOST=smtp.office365.com    # oder smtp.gmail.com / Cisco SMTP
SMTP_PORT=587
SMTP_USER=dein.name@firma.com
SMTP_PASSWORD=dein-passwort
```

---

## Optional — KI-Summary aktivieren

Der Bot kann die eingegangenen Nachrichten bei Urlaubsende automatisch klassifizieren: **"dringend"** vs. **"kann warten"**.

Trage einen der folgenden Keys in `.env` ein (Gemini hat Vorrang):

```env
GEMINI_API_KEY=...      # Google Gemini Flash (günstiger)
# oder
OPENAI_API_KEY=...      # OpenAI GPT-4o-mini
```

Kosten: Einmaliger Call bei Urlaubsende, < 1000 Tokens → Cent-Bereich.

---

## Deployment-Varianten

### Windows / Mac (Docker Desktop)

Genau wie oben beschrieben. Docker Desktop muss laufen. Der Bot startet automatisch neu wenn Docker Desktop neugestartet wird (`restart: unless-stopped`).

### Linux

```bash
# Docker installieren (falls noch nicht vorhanden)
curl -fsSL https://get.docker.com | sh

# Bot starten
docker compose up -d

# Autostart bei System-Neustart ist durch restart: unless-stopped bereits konfiguriert
```

### Synology NAS (DSM 7.2+)

1. Öffne den **Container Manager** auf deiner Synology
2. Klicke auf **"Projekt"** → **"Erstellen"**
3. Gib dem Projekt einen Namen: `webex-vacation-bot`
4. Wähle den Ordner aus, in den du die Projektdateien hochgeladen hast (z.B. `/docker/webex-vacation-bot`)
5. Kopiere den Inhalt von `docker-compose.yml` in das Textfeld
6. Klicke auf **"Weiter"** → Umgebungsvariablen über die Oberfläche eingeben oder `.env` hochladen
7. **Port 8080** ist bereits konfiguriert — Status-Seite ist im lokalen Netz unter `http://nas-ip:8080` erreichbar

**Setup auf Synology:** Kein Python nötig. Nach `docker compose up` einfach `http://nas-ip:8080/setup` im Browser öffnen — der Wizard übernimmt alles inkl. Webex-Autorisierung direkt im Browser.

### QNAP NAS

1. Öffne die **Container Station**
2. Klicke auf **"Anwendungen"** → **"Erstellen"**
3. Wähle **"docker-compose.yml hochladen"**
4. Umgebungsvariablen direkt in der QNAP-Oberfläche setzen (mind. `MY_WEBEX_EMAIL`, `WEBEX_CLIENT_ID`, `WEBEX_CLIENT_SECRET`)
5. Container starten → Browser: `http://nas-ip:8080/setup`

Kein Terminal nötig — Setup vollständig im Browser.

**Hinweis für NAS-Deployment:** Der Bot läuft 24/7 ohne dass dein PC eingeschaltet sein muss. Die SQLite-Datei liegt im NAS-Volume und wird automatisch mit deinem NAS-Backup gesichert.

### Raspberry Pi

```bash
# Docker installieren
curl -fsSL https://get.docker.com | sh

# Token-Setup
pip3 install httpx python-dotenv
python3 get_webex_token.py

# Bot starten
docker compose up -d
```

---

## Protokoll einsehen (SQLite)

Die Datenbank liegt in `./data/vacation.db`. Du kannst sie mit dem [DB Browser for SQLite](https://sqlitebrowser.org) (kostenlos, Windows/Mac/Linux) öffnen.

Oder per Kommandozeile im Container:

```bash
docker exec -it webex-vacation-bot sqlite3 /data/vacation.db "SELECT * FROM vacation_log ORDER BY replied_at DESC;"
```

---

## Sicherheit

Der Bot speichert sensible Daten (Webex-Token, API-Keys) sicher:

**Webex-Tokens sind verschlüsselt** — die Datei `data/tokens.json` wird mit Fernet-Verschlüsselung gespeichert. Der Key liegt in `data/.key` und wird beim ersten Start automatisch erzeugt.

**Den `data/`-Ordner regelmäßig sichern** — er enthält Key + Datenbank. Ohne den Key können gespeicherte Tokens nicht entschlüsselt werden.

**Setup-Wizard ist passwortgeschützt** — beim ersten Start wird ein zufälliges Passwort erzeugt:
```bash
docker logs webex-vacation-bot | grep "SETUP WIZARD PASSWORD"
```
Für ein festes Passwort: `SETUP_PASSWORD=deinpasswort` in `.env` eintragen.

**Secrets werden maskiert** — Client Secret, API-Keys und SMTP-Passwort werden in der Summary-Seite nur als `abcd****` angezeigt.

**CSRF-Schutz** — alle Wizard-Formulare sind gegen Cross-Site-Request-Forgery abgesichert.

> Hinweis: Der Bot ist für den Betrieb im lokalen Heimnetz / Firmen-VPN ausgelegt. Port 8080 sollte **nicht** direkt aus dem Internet erreichbar sein — nutze einen VPN oder Cloudflare Tunnel mit Access-Policy wenn externer Zugriff nötig ist.

---

## Häufige Fragen

**Der Bot startet nicht / "no Webex credentials found"**
→ `get_webex_token.py` noch nicht ausgeführt. Schritt 3 wiederholen.

**"Token refresh failed"**
→ Client ID oder Secret falsch in `.env`. Werte aus developer.webex.com erneut kopieren.

**Status-Seite zeigt "Inaktiv"**
→ `VACATION_ENABLED=true` in `.env` prüfen, dann `docker compose restart`.

**Der Bot antwortet nicht auf Testnachrichten**
→ Nachrichten müssen 1:1 DMs sein (keine Gruppenräume). Prüfe die Logs: `docker logs webex-vacation-bot`.

**Nachrichten aus der Vergangenheit werden beantwortet**
→ Normales Verhalten beim ersten Start. Der Bot verarbeitet alle DMs ab dem Start der Urlaubsphase. Bereits gesendete Antworten werden nicht doppelt gesendet.
