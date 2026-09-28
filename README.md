# 🚨 LastMile Alert — Emergency Warning Accessibility Platform

Takes a sample **official emergency alert** and makes it clearer, multilingual,
visual, and deliverable over low-bandwidth channels — with full delivery and
acknowledgement tracking.

> **All sample alerts, translations of samples, recipients, volunteers and
> timestamps in this project are fictional demo data.**

**Pipeline:** Official Alert → Clarity Processor → Language Bank → Visual Studio →
Delivery Simulator → Receipt Tracker

## Required Installers

1. **Python 3.11+** — https://www.python.org/downloads/
   On Windows, tick **"Add Python to PATH"** during installation.
2. **Git** — https://git-scm.com/
3. **VS Code** — https://code.visualstudio.com/

## Installation

```text
pip install -r requirements.txt
python app.py
```

Then open the URL printed in the terminal, e.g. `http://127.0.0.1:5000`.

Or just double-click **`start.bat`** (Windows) / run **`./start.sh`** (macOS/Linux) —
both install dependencies if needed and launch the app.

## Windows Firewall (let other devices on your Wi-Fi open the app)

1. Open **Windows Security → Firewall & network protection → Allow an app
   through firewall**.
2. Click **Change settings → Allow another app → Browse**, select your
   `python.exe` (e.g. `C:\Users\<you>\AppData\Local\Programs\Python\Python311\python.exe`),
   click **Add**.
3. Tick **Private** (your home/college Wi-Fi) for the Python entry → **OK**.
4. Keep the app running; other devices use the second printed URL.

## LAN Access (open the app from another phone/laptop on the same Wi-Fi)

1. On the laptop running the app, open **Command Prompt** and run `ipconfig`.
2. Find your Wi-Fi adapter's **IPv4 Address**, e.g. `192.168.1.42`.
   (The app also prints this automatically as the second URL on startup.)
3. On the other device (same Wi-Fi), open a browser and go to
   `http://192.168.1.42:5000` (use your IPv4 and the printed port).

## Project structure

```text
lastmile-alert/
├── app.py                 # Flask backend: API, clarity engine, simulators
├── seed.py                # creates schema + seeds 2 alerts (recipients: registered citizens only)
├── check.py               # endpoint self-test, prints PASS/FAIL per test
├── config.py              # ports, limits, state→language map (tunable)
├── requirements.txt       # pinned deps (Flask, psutil)
├── start.bat / start.sh   # one-command startup scripts
├── .gitignore
├── README.md
├── debug.log              # auto-generated backend log
├── alerts.db              # auto-generated SQLite database
├── data/
│   └── translations.json  # hardcoded translations (add languages here)
├── templates/             # plain HTML (Jinja), no frameworks
│   ├── base.html
│   ├── index.html         # home / dashboard
│   ├── processor.html     # intake + 6-stage pipeline
│   ├── delivery.html      # SMS / USSD / relay simulator
│   ├── tracker.html       # acknowledgement dashboard (read-only)
│   └── one_message.html   # 📱 recipient phone view: one bandwidth-aware
│                          #    message + "Got it" / "I'm Safe" buttons
│   ├── debug.html         # debug console (/debug)
│   └── error.html         # friendly error page
└── static/
    ├── css/style.css      # plain CSS, print styles included
    └── js/app.js          # vanilla JS helpers (relative API paths only)
```

## API endpoints

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/alerts` | Create alert from official text + state |
| GET | `/api/alerts` | List processed alerts |
| GET | `/api/alerts/<id>` | Full alert: fields, translations, visual |
| POST | `/api/alerts/<id>/simulate-delivery` | SMS chunking + simulated delivery, USSD |
| POST | `/api/alerts/<id>/simulate-relay` | 5-volunteer offline relay + integrity checks |
| GET | `/api/alerts/<id>/tracker` | Recipient acknowledgement data + stats |
| GET | `/api/alerts/<id>/messages` | Organiser preview: one message per language |
| POST | `/api/alerts/<id>/read` | Recipient tapped "Got it" (their phone) |
| POST | `/api/alerts/<id>/acknowledge` | Recipient tapped "I'm Safe" (their phone) |
| GET | `/api/languages` | language choices for registration |
| POST | `/api/register` | register a citizen (stored in DB) |
| GET | `/api/users/<id>` | one citizen's stored details |
| GET | `/api/users/<id>/alerts` | citizen's alerts, own state first, in their language |
| GET | `/api/health` | status, db, counts, memory, port, routes |
| GET | `/api/diagnostics` | recent backend events |
| GET | `/api/sample-alerts` | the 2 pre-loaded samples |
| POST | `/api/reset` | one-click demo reset |

## Three-minute judge demo (click-by-click)

**👤 Citizen side first (the villager's phone):**

1. Run `python app.py`, open the printed URL. The navbar shows **Backend Online** (green).
2. Click **Register** → enter a name, village, state (**Assam** or **Odisha** for the
   samples), language, channel → **Register**. Details are stored in the database.
3. You land on **🚨 My Alerts** — the citizen's home. Each alert is one clean
   message in the citizen's own language. No settings, no stats, no clutter.
4. Tap **✓ Got it**, then **🙏 I'm Safe** — the buttons belong to the citizen's
   side, so the acknowledgement is real, not the organiser clicking for them.

**🛠 Organiser side (the control room):**

5. Click **New Alert** → pick the pre-loaded **Assam Flood Warning** sample →
   **Process Alert**. The pipeline (simplify → translate → compress for SMS)
   runs automatically in the backend.
6. The alert page shows the **outgoing message** in every language (tab to
   switch — try the Assamese tab) plus the **✅ Acknowledgements** panel:
   live counts and per-person status, read-only like a real control room.
   Your citizen from step 4 now shows as **✓ Safe**.
7. Open the **Delivery Simulator** → **Run SMS Simulation**: watch the chunks
   walk `QUEUED → SENT → DELIVERED`; chunk 2 fails once with a readable
   reason, **retries exactly once**, then delivers.
8. Scroll to the **USSD simulation** — a feature-phone `*123#` session (simulation only).
9. Click **Run 5-Volunteer Relay**: Official Source → V1 → V2 → V3 → V4 → V5 →
   Community, with timestamps and a **✓ MATCH — meaning preserved** integrity
   check at every hop.
10. Open **Debug Console**: timestamped backend events, API latencies, retry
    events — also saved to `debug.log`.
11. (Bonus) Press **A+ Big Text** / **◐ Contrast** in the navbar for accessibility
    modes, or **Print** for the one-page alert card.

## Adding a new language (no code changes)

Edit `data/translations.json` and add a language code under the alert key:

```json
"assam_flood": {
  "kok": "…Konkani translation of the simplified alert…"
}
```

Then add the state→language entry in `config.py` (`STATE_LANGUAGES`).
The Language Bank picks it up automatically; missing languages fall back to
English with a **⚠ Translation pending** warning. Or call
`add_translation("assam_flood", "kok", text)` from a Python shell.

> **Custom alerts** (anything you paste yourself) are translated **two ways**:
> 1. **Offline phrase bank** (`data/offline_bank.json`) — the simplified alert
>    is assembled from fixed blocks (header, severity line, safety lines,
>    ~20 common instructions), each pre-translated into 11 languages.
>    Zero internet needed, works on a LAN.
> 2. **Live translation** when online — overwrites with a fuller translation
>    that also covers unusual sentences. No API key, no new dependencies.
> If the machine is offline, step 1 stands on its own; anything still missing
> keeps the honest **⚠ Translation pending** fallback instead of failing.

## Troubleshooting

- **Port already in use** — the app automatically tries 5001, 5002, … and prints
  the working URL. Never crashes on a busy port.
- **`pip` / `python` not recognized** — reinstall Python 3.11+ and tick
  **Add Python to PATH**; reopen the terminal. Try `py` instead of `python`.
- **PATH problems** — `python -m pip` works even when bare `pip` doesn't.
- **Flask-CORS missing** — not needed; the frontend uses same-origin relative
  paths (`/api/...`), so no CORS setup is required.
- **Odia/Assamese show as boxes** — install a local font once:
  download **Noto Sans** + **Noto Sans Oriya/Bengali** from Google Fonts
  (fonts.google.com/noto), install the TTFs, restart the browser. The CSS
  already falls back through system Indian fonts (`Nirmala UI`, `Mangal`).
- **Firewall blocking access** — allow `python.exe` through Windows Firewall
  (Private network), see above.
- **Backend Offline (red)** — the Flask server isn't running; run `python app.py`
  in the project folder and refresh.
- **Database reset** — click **Reset Demo** on the home page, or run
  `python seed.py`. This wipes `alerts.db` and reseeds everything.
- **Run the self-test** — with the app running: `python check.py`
  (optionally `python check.py http://127.0.0.1:5001`).

## Offline guarantee

After `pip install -r requirements.txt`, everything runs locally: no CDN, no
external fonts, no chart libraries, no frontend build step. Charts are pure
HTML/CSS, icons are inline SVG/emoji, speech uses the browser's built-in
`speechSynthesis`.

## Registration and alert sound repair — 26 September 2026

Registration now clears old errors correctly and uses the shared API helper's
supported JSON request handling. The helper accepts both existing calling styles,
which also fixes the citizen **Got it** and **I'm Safe** requests. Submitting a
registration disables its button until completion; validation failures restore it
and display a useful message. State, field types, text lengths and delivery channel
are validated by the server. Registration identity falls back to session storage if
local storage is unavailable. A network outage no longer deletes that identity.
After **Got it**, **I'm Safe** remains available.

### Buzzer

- Open a processed organiser alert or the citizen **My Alerts** page.
- Click **Enable alert sound** on that page. Browser autoplay rules require a click;
  enabling sound on a previous page does not unlock a newly loaded page.
- An **Extreme** alert plays six short alternating tones. A **Severe** alert plays
  three short tones. **Advisory** alerts never trigger a buzzer.
- A pending urgent alert sounds when audio is enabled. Subsequent qualifying alerts
  sound once per alert/severity in that browser tab. Routine refreshes do not repeat
  an already sounded alert.
- Click **Stop sound** to silence the current pattern or **Mute alert sound** to
  disable future sound until enabled again.
- Citizen pages check for updates every 15 seconds while visible. Only unread,
  unacknowledged alerts for the citizen's selected state can trigger their buzzer.
  State matching is not exact village geolocation. Organisers hear the selected
  processed alert when sound is enabled.
- Keep the page open, allow the site's audio and ensure the device volume is audible.
  A closed website cannot provide background alarms; this is not a push-notification
  or real telecom service.

The sound is generated locally with Web Audio. It requires no audio download, CDN,
internet connection, package or API key. It is an attention cue only. Alert source
text, severity classification, translations, required actions, SMS/relay processing
and the existing database schema were not changed by this repair. The uploaded
SQLite database and existing logs are preserved in the corrected archive.

Validation: all 19 existing `check.py` checks passed against an isolated test database.
Additional checks covered registration input errors and persistence, the actual
registration JavaScript handler, both API call formats, Advisory silence, Severe
and Extreme patterns, duplicate suppression, mute and stop. Audio scheduling was
tested with a mocked Web Audio context; actual speaker playback depends on the
user's browser/device and was not audibly verified here.
