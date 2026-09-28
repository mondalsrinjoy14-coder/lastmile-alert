"""LastMile Alert - Emergency Warning Accessibility Platform.

Backend: Python 3 + Flask + SQLite (file-based, zero external DB setup).
Fully offline after `pip install -r requirements.txt`. No CDN, no build step.

Pipeline: Official Alert -> Clarity Processor -> Language Bank ->
           Visual Studio -> Delivery Simulator -> Receipt Tracker
"""

import json
import logging
import os
import re
import socket
import sqlite3
import time
import traceback
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta

from flask import Flask, g, jsonify, render_template, request

import config

# ----------------------------------------------------------------------------
# Logging: every backend event goes to debug.log AND an in-memory ring buffer
# served to the Debug Console (/debug) and /api/diagnostics.
# ----------------------------------------------------------------------------
logging.basicConfig(
    filename=config.DEBUG_LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger("lastmile")

DIAG_BUFFER = deque(maxlen=300)  # recent events for the diagnostics panel
APP_START_TIME = time.time()
BOUND_PORT = {"value": config.DEFAULT_PORT}  # filled in by main()


def log_event(category, message):
    """Record one timestamped backend event to the log file and the live buffer."""
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "category": category,
        "message": message,
    }
    DIAG_BUFFER.append(entry)
    logger.info("[%s] %s", category, message)


def timed(category):
    """Decorator: time a request handler and log its latency in milliseconds."""
    def decorator(fn):
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                response = fn(*args, **kwargs)
                ms = (time.perf_counter() - t0) * 1000
                status = response[1] if isinstance(response, tuple) else 200
                log_event(category, f"{request.method} {request.path} -> HTTP {status} in {ms:.1f} ms")
                return response
            except Exception as exc:  # never leak tracebacks to the user
                log_event("ERROR", f"{request.method} {request.path} failed: {exc}")
                logger.exception("Unhandled error in %s", request.path)
                raise
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


# ----------------------------------------------------------------------------
# Flask app
# ----------------------------------------------------------------------------
app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False  # keep Indian scripts readable in JSON


# ----------------------------------------------------------------------------
# Database layer (file-based SQLite, created automatically on first run)
# ----------------------------------------------------------------------------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    official_text TEXT NOT NULL,
    state TEXT NOT NULL,
    disaster_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    severity_color TEXT NOT NULL,
    affected_area TEXT NOT NULL,
    required_action TEXT NOT NULL,
    simplified_text TEXT NOT NULL,
    sample_key TEXT,
    sms_result TEXT,
    relay_result TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    village TEXT NOT NULL,
    state TEXT NOT NULL,
    language TEXT NOT NULL,
    channel TEXT NOT NULL,
    delivery_status TEXT NOT NULL,
    read_status TEXT NOT NULL,
    ack_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_translations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id INTEGER NOT NULL,
    lang_code TEXT NOT NULL,
    text TEXT NOT NULL,
    UNIQUE(alert_id, lang_code)
);
"""


def db_path():
    """Return the absolute path of the SQLite database file."""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, config.DATABASE_PATH)


def get_db():
    """Open (or reuse) the per-request SQLite connection."""
    if "db" not in g:
        conn = sqlite3.connect(db_path())
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


def direct_db():
    """Open a standalone SQLite connection (for scripts outside requests)."""
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they do not exist yet."""
    conn = direct_db()
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    conn.close()
    log_event("DB", f"schema ready at {db_path()}")


@app.teardown_appcontext
def close_db(exception):
    """Close the per-request SQLite connection after each request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ----------------------------------------------------------------------------
# CLARITY PROCESSOR - rule-based offline plain-language engine.
# Extracts 4 semantic fields, then rewrites the alert at ~Grade-5 reading level.
# Meaning, urgency, area and action are preserved; nothing new is invented.
# ----------------------------------------------------------------------------

EVENT_KEYWORDS = {
    "Flood": ["flood", "flooding", "flooded", "inundat", "waterlog",
              "brahmaputra", "river", "embankment", "heavy rainfall"],
    "Cyclone": ["cyclone", "cyclonic", "storm", "landfall", "wind speed",
                "kmph", "gust", "sea "],
    "Earthquake": ["earthquake", "tremor", "magnitude", "epicent",
                   "richter", "seismic"],
    "Heatwave": ["heatwave", "heat wave", "heat-wave", "temperature",
                 "hot weather", "loo "],
}

EVENT_ICONS = {"Flood": "\U0001f30a", "Cyclone": "\U0001f300",
               "Earthquake": "\U0001f3da\ufe0f", "Heatwave": "\U0001f321\ufe0f"}

SEVERITY_RULES = [  # checked in order; first match wins
    ("Extreme", "RED", ["red alert", "extremely heavy", "extreme",
                        "evacuate immediately", "very severe",
                        "above the danger", "extremely likely"]),
    ("Severe", "ORANGE", ["orange alert", "severe", "very likely",
                          "heavy rainfall", "strong winds",
                          "must not venture", "sustained"]),
    ("Advisory", "YELLOW", ["yellow alert", "advisory", "likely",
                            "moderate", "watch", "expected"]),
]

SEVERITY_SENTENCE = {
    "Extreme": "This is an EXTREME emergency. Act NOW.",
    "Severe": "This is a SEVERE warning. Get ready now.",
    "Advisory": "This is an ADVISORY. Stay alert and be careful.",
}

URGENCY_LABEL = {
    "RED": "EVACUATE NOW / EXTREME EMERGENCY",
    "ORANGE": "PREPARE / SEVERE WARNING",
    "YELLOW": "STAY ALERT / ADVISORY",
}

# Districts known per state, used to extract the affected area from the text.
STATE_DISTRICTS = {
    "Assam": ["Dhemaji", "Lakhimpur", "Dibrugarh", "Barpeta", "Nalbari",
              "Majuli", "Sivasagar", "Jorhat", "Darrang", "Morigaon"],
    "Odisha": ["Puri", "Ganjam", "Jagatsinghpur", "Kendrapara", "Bhadrak",
               "Balasore", "Khordha", "Cuttack", "Nayagarh", "Jajpur"],
    "Bihar": ["Patna", "Bhagalpur", "Darbhanga", "Muzaffarpur", "Purnia"],
    "West Bengal": ["Kolkata", "Howrah", "Hooghly", "Nadia", "Murshidabad"],
    "Uttar Pradesh": ["Lucknow", "Varanasi", "Gorakhpur", "Prayagraj"],
    "Maharashtra": ["Mumbai", "Thane", "Raigad", "Ratnagiri", "Pune"],
    "Gujarat": ["Ahmedabad", "Surat", "Vadodara", "Kutch", "Rajkot"],
    "Tamil Nadu": ["Chennai", "Cuddalore", "Nagapattinam", "Thoothukudi"],
    "Kerala": ["Kochi", "Thiruvananthapuram", "Kozhikode", "Alappuzha"],
    "Karnataka": ["Bengaluru", "Mangaluru", "Mysuru", "Belagavi"],
}

ACTION_CUES = ["evacuate", "move to", "stay indoors", "stay inside",
               "do not", "avoid", "keep ", "switch off", "listen",
               "follow", "contact", "call ", "help ", "carry", "take ",
               "remain", "must not", "should stay"]

# Complex word -> simple word replacements for Grade-5 readability.
SIMPLE_WORDS = {
    "precipitation": "rain", "inundation": "flooding", "evacuate": "move to a safe place",
    "imminent": "coming very soon", "residents": "people",
    "authorities": "government officers", "advisory": "warning",
    "sustained": "continuous", "vulnerable": "unsafe",
    "approximately": "about", "utilize": "use", "ensure": "make sure",
    "immediately": "now", "assistance": "help", "anticipated": "expected",
    "commence": "start", "subsequently": "then", "disseminate": "share",
    "refrain": "do not", "endeavour": "try", "necessitate": "need",
}


def detect_event_type(text):
    """Pick the disaster type with the most keyword hits in the text."""
    low = text.lower()
    best, best_hits = "Flood", 0
    for event, keywords in EVENT_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in low)
        if hits > best_hits:
            best, best_hits = event, hits
    log_event("PIPELINE", f"event type detected: {best} ({best_hits} keyword hits)")
    return best


def detect_severity(text):
    """Pick the first matching severity level (Extreme > Severe > Advisory)."""
    low = text.lower()
    for level, color, keywords in SEVERITY_RULES:
        if any(kw in low for kw in keywords):
            log_event("PIPELINE", f"severity detected: {level} ({color})")
            return level, color
    log_event("PIPELINE", "severity defaulted to Advisory (YELLOW)")
    return "Advisory", "YELLOW"


def extract_affected_area(text, state):
    """Find named districts in the text; fall back to the state name."""
    found = []
    for district in STATE_DISTRICTS.get(state, []):
        if re.search(r"\b" + re.escape(district) + r"\b", text, re.IGNORECASE):
            found.append(district)
    area = ", ".join(found) + (f" ({state})" if found else state)
    log_event("PIPELINE", f"affected area extracted: {area}")
    return area


def _is_meaningful(sentence, event):
    """Reject fragments too short to carry an instruction (e.g. 'Cyclone.').

    One-word leftovers and bare event names must never leak into the
    simplified alert shown to the community.
    """
    words = re.findall(r"[A-Za-z]+", sentence)
    if len(words) < 2:
        return False
    if sentence.strip().lower().rstrip(".") == event.lower():
        return False
    return True


def extract_required_action(text, event=""):
    """Pick the sentences that contain action cues (what people must do)."""
    sentences = [s.strip() for s in re.split(r"[.!?]\s+", text.strip()) if s.strip()]
    actions = [s for s in sentences
               if any(cue in s.lower() for cue in ACTION_CUES)
               and _is_meaningful(s, event)]
    if not actions:  # fallback: last meaningful sentence, which usually holds the ask
        meaningful = [s for s in sentences if _is_meaningful(s, event)]
        actions = meaningful[-1:] if meaningful else []
    # re.split consumes the ". " delimiters, so restore a terminator per
    # sentence - otherwise "Move out. Stay calm." would merge into one
    # run-on line downstream.
    action = " ".join(s if s.endswith((".", "!", "?")) else s + "."
                      for s in actions[:3]).strip()
    log_event("PIPELINE", f"required action extracted: {action[:80]}...")
    return action or "Follow official announcements."


def simplify_sentence(sentence):
    """Rewrite one sentence with simple words and short structure."""
    out = sentence
    for complex_word, simple_word in SIMPLE_WORDS.items():
        out = re.sub(r"\b" + re.escape(complex_word) + r"\b",
                     simple_word, out, flags=re.IGNORECASE)
    out = out.strip().rstrip(".")
    return out[0].upper() + out[1:] if out else out


def build_simplified(event, severity, area, action):
    """Assemble the Grade-5 simplified alert from the extracted fields."""
    icon = EVENT_ICONS.get(event, "\u26a0\ufe0f")
    parts = [f"{icon} {event.upper()} ALERT for {area}."]
    parts.append(SEVERITY_SENTENCE[severity])
    seen = {p.lower() for p in parts}
    for raw in re.split(r"[.!?]\s+", action.strip()):
        if not raw.strip() or not _is_meaningful(raw, event):
            continue
        cleaned = simplify_sentence(raw)
        if cleaned.lower() in seen:  # never repeat the header/severity lines
            continue
        seen.add(cleaned.lower())
        parts.append(cleaned + ".")
    parts.append("Help children and old people first.")
    parts.append("Listen to the radio for new updates.")
    simplified = " ".join(parts)
    log_event("PIPELINE", f"simplified alert built ({len(simplified)} chars)")
    return simplified


def process_alert(official_text, state, disaster_type=None):
    """Run the full clarity pipeline and return every extracted field."""
    t0 = time.perf_counter()
    event = disaster_type if disaster_type in config.DISASTER_TYPES else detect_event_type(official_text)
    severity, color = detect_severity(official_text)
    area = extract_affected_area(official_text, state)
    action = extract_required_action(official_text, event)
    simplified = build_simplified(event, severity, area, action)
    ms = (time.perf_counter() - t0) * 1000
    log_event("PIPELINE", f"clarity processor finished in {ms:.1f} ms")
    return {
        "event_type": event,
        "severity": severity,
        "severity_color": color,
        "urgency_label": URGENCY_LABEL[color],
        "icon": EVENT_ICONS.get(event, "\u26a0\ufe0f"),
        "affected_area": area,
        "required_action": action,
        "simplified_text": simplified,
        "processing_ms": round(ms, 1),
    }


# ----------------------------------------------------------------------------
# LANGUAGE BANK - hardcoded translations for the two sample alerts.
# Any state/language without a translation falls back to English with a
# visible "Translation pending" flag. New languages are added by editing
# data/translations.json only - no code change required.
# ----------------------------------------------------------------------------
def translations_file():
    """Return the absolute path of the translations JSON file."""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "data", "translations.json")


def load_translations():
    """Load the hardcoded translation bank from disk."""
    with open(translations_file(), encoding="utf-8") as f:
        return json.load(f)


TRANSLATIONS = load_translations()


def offline_bank_file():
    """Return the absolute path of the offline phrase-bank JSON file."""
    base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "data", "offline_bank.json")


# Languages a citizen can register in: (code, display name).
LANG_CHOICES = [
    ("en", "English"), ("hi", "Hindi"), ("as", "Assamese"), ("bn", "Bengali"),
    ("gu", "Gujarati"), ("kn", "Kannada"), ("ml", "Malayalam"),
    ("mr", "Marathi"), ("or", "Odia"), ("pa", "Punjabi"),
    ("ta", "Tamil"), ("te", "Telugu"),
]


def load_offline_bank():
    """Load the pre-translated phrase bank (works with zero internet)."""
    try:
        with open(offline_bank_file(), encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log_event("LANGUAGE", f"offline bank unavailable: {exc}")
        return {}


OFFLINE_BANK = load_offline_bank()


def _normalize_line(s):
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def translate_action_line_offline(line, bank_actions):
    """Translate one simplified action sentence using the offline bank.

    Returns the translation on a normalized exact match, else None
    (caller keeps the English sentence or tries the live endpoint).
    """
    norm = _normalize_line(line)
    if not norm:
        return None
    for eng, translated in bank_actions.items():
        if _normalize_line(eng) == norm:
            return translated
    return None


def build_offline_translations(event, severity, area, action, state):
    """Assemble translations from the offline phrase bank - no internet.

    The simplified alert is built from fixed blocks (header, severity line,
    safety lines, common instructions), so each block is translated by
    dictionary lookup and reassembled. Returns (texts, blocks, unmatched_idx):
      texts         {lang_code: assembled translated text}
      blocks        {lang_code: [translated block strings]}
      unmatched_idx block indices still in English (no bank match)
    The caller can live-translate just those blocks when online, keeping the
    curated translations for everything else.
    """
    reg_name, reg_code = config.STATE_LANGUAGES.get(state, ("English", "en"))
    targets = [("hi", "Hindi")]
    if reg_code not in ("en", "hi") and reg_code in OFFLINE_BANK:
        targets.append((reg_code, reg_name))
    targets = [(c, n) for c, n in targets if c in OFFLINE_BANK]
    if not targets:
        return {}, {}, []

    icon = EVENT_ICONS.get(event, "\u26a0\ufe0f")
    action_lines = [simplify_sentence(r) for r in re.split(r"[.!?]\s+", action.strip())
                    if r.strip() and _is_meaningful(r, event)]
    seen = set()
    clean_lines = []
    for ln in action_lines:
        key = ln.lower()
        if key not in seen:
            seen.add(key)
            clean_lines.append(ln)

    blocks = {}
    for code, name in targets:
        bank = OFFLINE_BANK[code]
        t_event = bank["events"].get(event, event)
        blist = [bank["header"].format(icon=icon, event=t_event, area=area),
                 bank["severity"].get(severity, SEVERITY_SENTENCE[severity])]
        for ln in clean_lines:
            t = translate_action_line_offline(ln, bank["actions"])
            blist.append(t if t else (ln if ln.endswith(".") else ln + "."))
        blist.extend(bank["safety"])
        blocks[code] = blist
        log_event("LANGUAGE", f"offline translation built: {name} "
                              f"({len(blist)} blocks)")

    unmatched_idx = []
    for i, ln in enumerate(clean_lines):
        if not any(translate_action_line_offline(ln, OFFLINE_BANK[c]["actions"])
                   for c, _ in targets):
            unmatched_idx.append(2 + i)  # 2 header/severity blocks precede actions
    texts = {c: " ".join(b) for c, b in blocks.items()}
    return texts, blocks, unmatched_idx


def store_translations(alert_id, mapping):
    """Cache {lang_code: text} rows for an alert (offline or live)."""
    if not mapping:
        return
    db = get_db()
    for code, text in mapping.items():
        db.execute(
            "INSERT OR REPLACE INTO alert_translations (alert_id, lang_code, text)"
            " VALUES (?, ?, ?)", (alert_id, code, text))
    db.commit()


def add_translation(sample_key, lang_code, text):
    """Add one translation to the bank and persist it to translations.json.

    This is the reusable extension mechanism: pass a sample key like
    'assam_flood', a language code like 'kok', and the translated text.
    """
    bank = load_translations()
    bank.setdefault(sample_key, {})[lang_code] = text
    with open(translations_file(), "w", encoding="utf-8") as f:
        json.dump(bank, f, ensure_ascii=False, indent=2)
    global TRANSLATIONS
    TRANSLATIONS = bank
    log_event("LANGUAGE", f"added translation: {sample_key}/{lang_code}")


# Languages our bank uses that the free translation endpoint cannot handle.
# (Konkani is not supported there; such languages keep the pending fallback.)
UNTRANSLATABLE_CODES = {"kok"}

TRANSLATE_TIMEOUT_S = 10


def translate_text(text, target_code):
    """Translate English text via the free Google Translate endpoint.

    Returns the translated string, or None when offline / unsupported /
    rate-limited. No API key needed; pure stdlib so requirements.txt
    stays unchanged. Anything that goes wrong degrades to the honest
    'Translation pending' fallback instead of failing the request.
    """
    if not text or not text.strip() or target_code in UNTRANSLATABLE_CODES:
        return None
    try:
        params = urllib.parse.urlencode(
            {"client": "gtx", "sl": "en", "tl": target_code, "dt": "t",
             "q": text.strip()})
        url = "https://translate.googleapis.com/translate_a/single?" + params
        req = urllib.request.Request(
            url, headers={"User-Agent": "LastMileAlert/1.0"})
        with urllib.request.urlopen(req, timeout=TRANSLATE_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        translated = "".join(seg[0] for seg in payload[0] if seg and seg[0])
        return translated.strip() or None
    except Exception as exc:  # offline, timeout, rate limit: stay honest
        log_event("LANGUAGE", f"live translation to '{target_code}' failed: {exc}")
        return None


def get_translations(alert_row):
    """Build the language set for an alert: English + Hindi + regional.

    Returns {lang_code: {"language": name, "text": str, "pending": bool}}.
    Missing translations fall back to English with pending=True so the UI
    can show the 'Translation pending' warning.
    """
    state = alert_row["state"]
    reg_name, reg_code = config.STATE_LANGUAGES.get(state, ("English", "en"))
    wanted = [("en", "English"), ("hi", "Hindi")]
    if reg_code not in ("en", "hi"):
        wanted.append((reg_code, reg_name))
    bank = dict(TRANSLATIONS.get(alert_row["sample_key"] or "", {}))
    try:  # overlay live translations cached for custom alerts, if any
        db = get_db()
        for trow in db.execute(
                "SELECT lang_code, text FROM alert_translations WHERE alert_id = ?",
                (alert_row["id"],)):
            bank[trow["lang_code"]] = trow["text"]
    except Exception as exc:
        log_event("LANGUAGE", f"translation overlay lookup failed: {exc}")
    out = {}
    for code, name in wanted:
        if code in bank:
            out[code] = {"language": name, "text": bank[code], "pending": False}
        else:
            out[code] = {"language": name,
                         "text": alert_row["simplified_text"],
                         "pending": code != "en"}
    return out


# ----------------------------------------------------------------------------
# DELIVERY SIMULATOR - SMS chunking, fake network delivery, USSD, relay.
# Everything is simulated locally. No real SMS, phone numbers, or networks.
# ----------------------------------------------------------------------------

def chunk_text(text, size):
    """Split text into word-aware chunks of at most `size` characters."""
    words, chunks, current = text.split(), [], ""
    for word in words:
        candidate = (current + " " + word).strip()
        if len(candidate) <= size:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    # Safety net: hard-split any chunk that still exceeds the limit.
    final = []
    for chunk in chunks:
        while len(chunk) > size:
            final.append(chunk[:size])
            chunk = chunk[size:]
        final.append(chunk)
    return final


def _now_iso():
    """Current local time as an ISO timestamp string."""
    return datetime.now().isoformat(timespec="seconds")


def simulate_sms_delivery(text):
    """Simulate SMS delivery of `text` in <=160 char chunks.

    Each chunk walks QUEUED -> SENT -> DELIVERED. To demonstrate the retry
    path, the second chunk fails once with a human-readable reason, is
    retried exactly once, and then delivers. Fake delays only.
    """
    t0 = time.perf_counter()
    chunks = chunk_text(text, config.SMS_CHUNK_SIZE)
    delay = config.SIMULATED_NETWORK_DELAY
    results = []
    for i, chunk in enumerate(chunks):
        events = [{"status": "QUEUED", "ts": _now_iso()}]
        time.sleep(delay)
        events.append({"status": "SENT", "ts": _now_iso()})
        time.sleep(delay)
        final_status, failure_reason, retried = "DELIVERED", None, False
        if i == 1 and len(chunks) > 1:
            # Deterministic demo of the failure -> retry -> delivered path.
            failure_reason = "Simulated network timeout - weak signal in the area"
            events.append({"status": "FAILED", "ts": _now_iso(),
                           "reason": failure_reason})
            log_event("RETRY", f"SMS chunk {i + 1}/{len(chunks)} failed "
                               f"({failure_reason}); retrying once")
            retried = True
            time.sleep(delay)
            events.append({"status": "RETRY", "ts": _now_iso()})
            time.sleep(delay)
            events.append({"status": "DELIVERED", "ts": _now_iso()})
            log_event("DELIVERY", f"SMS chunk {i + 1}/{len(chunks)} delivered after retry")
        else:
            events.append({"status": "DELIVERED", "ts": _now_iso()})
            log_event("DELIVERY", f"SMS chunk {i + 1}/{len(chunks)} delivered")
        results.append({
            "index": i + 1,
            "text": chunk,
            "chars": len(chunk),
            "final_status": final_status,
            "retried": retried,
            "failure_reason": failure_reason,
            "events": events,
        })
    total_ms = round((time.perf_counter() - t0) * 1000, 1)
    log_event("DELIVERY", f"SMS simulation complete: {len(chunks)} chunks in {total_ms} ms")
    return {"chunk_count": len(chunks), "chunk_size": config.SMS_CHUNK_SIZE,
            "total_ms": total_ms, "chunks": results,
            "disclaimer": "SIMULATION ONLY - no real SMS was sent and no phone numbers were used."}


def build_ussd(translations):
    """Build a simulated USSD session for basic feature phones.

    Purely a local simulation - it never touches a real telecom network.
    """
    steps = [
        {"screen": "DIAL", "text": "User dials *123#",
         "note": "USSD code for the demo emergency alert service"},
        {"screen": "MENU", "text": "EMERGENCY ALERT\n1. Read alert - English\n2. Alert padhein - Hindi\n3. Alert in regional language\n0. Exit",
         "note": "Network shows a text menu (no internet needed on a real phone)"},
    ]
    ordered = ["en", "hi"] + [c for c in translations if c not in ("en", "hi")]
    for n, code in enumerate(ordered, start=1):
        t = translations[code]
        page1 = t["text"][:150]
        steps.append({"screen": f"SELECT {n}",
                      "text": f"You selected {n} - {t['language']}\n\n{page1}...",
                      "note": "Message split across USSD screens (182-char limit)"})
    steps.append({"screen": "END",
                  "text": "End of alert. Stay safe. Dial *123# anytime to read again.",
                  "note": "Session closed"})
    log_event("DELIVERY", f"USSD simulation built ({len(steps)} screens)")
    return {"screens": steps,
            "disclaimer": "SIMULATION ONLY - not connected to any telecom network."}


# ----------------------------------------------------------------------------
# OFFLINE COMMUNITY RELAY - the alert travels through N volunteers.
# Every hop runs a message-integrity check against the original meaning.
# ----------------------------------------------------------------------------
VOLUNTEERS = [
    ("V1", "Rina Das", "ASHA health worker"),
    ("V2", "Mohan Bora", "Village head"),
    ("V3", "Priya Kalita", "School teacher"),
    ("V4", "Rahul Saikia", "Shopkeeper"),
    ("V5", "Anita Deka", "Youth volunteer"),
]

RELAY_PARAPHRASE = {  # volunteer 3 rewords slightly; meaning must survive
    "Move to high ground or a relief camp NOW.":
        "Go to a safe high place or a relief camp now.",
    "Stay inside your house.":
        "Remain inside your house.",
}


def integrity_check(fields, forwarded_text):
    """Verify the forwarded message still carries the original meaning.

    Checks the four semantic fields (event, severity, area, action) against
    the forwarded text and reports each check separately.
    """
    low = forwarded_text.lower()
    area_bits = [b.strip().lower() for b in
                 re.split(r"[,()]", fields["affected_area"]) if b.strip()]
    checks = [
        ("Event type preserved",
         fields["event_type"].lower() in low or
         any(k in low for k in EVENT_KEYWORDS.get(fields["event_type"], []))),
        ("Severity preserved",
         fields["severity"].lower() in low or "extreme" in low or
         "severe" in low or "warning" in low or "advisory" in low),
        ("Affected area preserved",
         any(b and b in low for b in area_bits)),
        ("Required action preserved",
         any(cue.strip() in low for cue in ACTION_CUES if cue.strip())),
    ]
    passed = all(ok for _, ok in checks)
    return {"match": passed,
            "verdict": "MATCH - meaning preserved" if passed
                       else "MISMATCH - meaning changed!",
            "checks": [{"name": name, "ok": ok} for name, ok in checks]}


def simulate_relay(fields):
    """Run the N-volunteer offline relay with integrity checks at every hop."""
    t0 = time.perf_counter()
    original = fields["simplified_text"]
    count = config.RELAY_VOLUNTEER_COUNT
    chain = []
    current_text, clock = original, datetime.now()
    hops = [("Official Source", "District control room", "broadcast")]
    hops += [(vid, f"{name} ({role})", "offline forward")
             for vid, name, role in VOLUNTEERS[:count]]
    hops.append(("Community", "Villagers reached", "received"))
    for step, (vid, who, kind) in enumerate(hops):
        clock = clock + timedelta(minutes=2 + (step * 3) % 6)
        forwarded = current_text
        note = "Original message broadcast."
        if 1 <= step <= count:  # volunteer hops
            if step == 3:  # one volunteer paraphrases slightly
                for src, dst in RELAY_PARAPHRASE.items():
                    forwarded = forwarded.replace(src, dst)
                note = "Volunteer reworded slightly while forwarding."
            else:
                note = "Message forwarded unchanged."
            current_text = forwarded
        check = integrity_check(fields, forwarded)
        chain.append({
            "step": step,
            "id": vid,
            "who": who,
            "kind": kind,
            "timestamp": clock.isoformat(timespec="seconds"),
            "message_received": current_text if step else original,
            "message_forwarded": forwarded,
            "note": note,
            "integrity": check,
            "status": "FORWARDED" if step <= count else "RECEIVED",
        })
        log_event("RELAY", f"hop {step}: {vid} - {check['verdict']}")
    total_ms = round((time.perf_counter() - t0) * 1000, 1)
    all_ok = all(h["integrity"]["match"] for h in chain)
    log_event("RELAY", f"relay complete: {count} volunteers, "
                        f"integrity {'ALL PASS' if all_ok else 'FAILURES'} in {total_ms} ms")
    return {"volunteer_count": count, "hops": chain,
            "all_meanings_preserved": all_ok, "total_ms": total_ms,
            "disclaimer": "SIMULATION ONLY - volunteers and timestamps are fictional demo data."}


# ----------------------------------------------------------------------------
# SAMPLE ALERTS - pre-loaded fictional demo alerts for the intake page.
# ----------------------------------------------------------------------------
SAMPLE_ALERTS = [
    {
        "key": "assam_flood",
        "title": "Assam Flood Warning",
        "state": "Assam",
        "disaster_type": "Flood",
        "official": (
            "DEMO ALERT (fictional data) - RED ALERT: Extremely heavy rainfall "
            "is very likely over Assam during the next 48 hours. The Brahmaputra "
            "river is flowing above the danger level at Dhemaji, Lakhimpur and "
            "Dibrugarh. Flooding and waterlogging are expected in low-lying areas. "
            "Residents in vulnerable areas must evacuate immediately to higher "
            "ground or the nearest relief camp. Do not attempt to cross flooded "
            "roads or bridges. For emergency assistance, contact the district "
            "control room."
        ),
    },
    {
        "key": "odisha_cyclone",
        "title": "Odisha Cyclone Warning",
        "state": "Odisha",
        "disaster_type": "Cyclone",
        "official": (
            "DEMO ALERT (fictional data) - ORANGE ALERT: A severe cyclonic storm "
            "is very likely to cross the Odisha coast near Puri tonight, with "
            "sustained wind speeds of 90-100 kmph. Heavy rainfall and strong winds "
            "are expected in Puri, Ganjam and Jagatsinghpur districts. Fishermen "
            "must not venture into the sea. People in coastal areas should stay "
            "indoors and keep torch, drinking water and dry food ready. Switch off "
            "the main power supply if water enters the house. Follow official "
            "announcements on radio and television."
        ),
    },
]

EVENT_PICTOGRAMS = {
    "Flood": ["\U0001f30a", "\U0001f3e0", "\u26a1", "\U0001f4fb"],
    "Cyclone": ["\U0001f300", "\U0001f3e0", "\U0001f526", "\U0001f4fb"],
    "Earthquake": ["\U0001f3da\ufe0f", "\U0001f6b6", "\U0001f4fb", "\U0001f6a8"],
    "Heatwave": ["\U0001f321\ufe0f", "\U0001f4a7", "\U0001f3e0", "\U0001f4fb"],
}


def validate_alert_input(data):
    """Validate alert-intake input; return (ok, error_message)."""
    if not isinstance(data, dict):
        return False, "Malformed request: expected a JSON object."
    text = (data.get("official_text") or "").strip()
    state = (data.get("state") or "").strip()
    disaster = (data.get("disaster_type") or "").strip()
    if not text:
        return False, "Alert text is empty. Please paste an official alert or pick a sample."
    if len(text) > config.MAX_ALERT_CHARS:
        return False, (f"Alert is {len(text)} characters; the limit is "
                       f"{config.MAX_ALERT_CHARS}. Please shorten it.")
    if state not in config.STATE_LANGUAGES:
        return False, f"Invalid state: '{state}'. Please select a state from the list."
    if disaster and disaster not in config.DISASTER_TYPES:
        return False, f"Invalid disaster type: '{disaster}'."
    return True, ""


def alert_to_dict(row):
    """Expand one DB alert row into the full API payload."""
    fields = {
        "event_type": row["disaster_type"],
        "severity": row["severity"],
        "severity_color": row["severity_color"],
        "affected_area": row["affected_area"],
        "required_action": row["required_action"],
        "simplified_text": row["simplified_text"],
    }
    translations = get_translations(row)
    visual = {
        "icon": EVENT_ICONS.get(row["disaster_type"], "\u26a0\ufe0f"),
        "color": row["severity_color"],
        "urgency_label": URGENCY_LABEL[row["severity_color"]],
        "pictograms": EVENT_PICTOGRAMS.get(row["disaster_type"], ["\u26a0\ufe0f"]),
        "lines": [
            f"{row['disaster_type'].upper()} - {row['severity'].upper()}",
            row["affected_area"],
            row["required_action"][:120],
        ],
    }
    return {
        "id": row["id"],
        "official_text": row["official_text"],
        "state": row["state"],
        "disaster_type": row["disaster_type"],
        "severity": row["severity"],
        "severity_color": row["severity_color"],
        "urgency_label": URGENCY_LABEL[row["severity_color"]],
        "affected_area": row["affected_area"],
        "required_action": row["required_action"],
        "simplified_text": row["simplified_text"],
        "sample_key": row["sample_key"],
        "translations": translations,
        "visual": visual,
        "has_sms": bool(row["sms_result"]),
        "has_relay": bool(row["relay_result"]),
        "created_at": row["created_at"],
    }


def error_response(message, code=400):
    """Return a friendly JSON error (never a traceback)."""
    log_event("ERROR", f"HTTP {code}: {message}")
    return jsonify({"error": message}), code


# ----------------------------------------------------------------------------
# API ROUTES
# ----------------------------------------------------------------------------
@app.route("/api/alerts", methods=["POST"])
@timed("API")
def api_create_alert():
    """Create an alert from official text + state, running the clarity pipeline."""
    data = request.get_json(silent=True)
    ok, err = validate_alert_input(data or {})
    if not ok:
        return error_response(err, 400)
    text = data["official_text"].strip()
    state = data["state"].strip()
    disaster = data.get("disaster_type") or None

    sample_key, simplified_override = None, None
    for sample in SAMPLE_ALERTS:  # reuse hardcoded translation for known samples
        if sample["official"].strip() == text:
            sample_key = sample["key"]
            simplified_override = TRANSLATIONS[sample_key]["en"]
            state = sample["state"]
            disaster = sample["disaster_type"]
            break

    fields = process_alert(text, state, disaster)
    if simplified_override:
        fields["simplified_text"] = simplified_override

    db = get_db()
    cur = db.execute(
        """INSERT INTO alerts (official_text, state, disaster_type, severity,
           severity_color, affected_area, required_action, simplified_text,
           sample_key, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (text, state, fields["event_type"], fields["severity"],
         fields["severity_color"], fields["affected_area"],
         fields["required_action"], fields["simplified_text"],
         sample_key, datetime.now().isoformat(timespec="seconds")))
    db.commit()
    alert_id = cur.lastrowid
    log_event("API", f"alert #{alert_id} created ({state}, {fields['event_type']})")
    if sample_key is None:
        # Custom alert: translations that work with zero internet.
        # 1) Offline phrase bank builds Hindi + regional instantly (curated).
        # 2) When online, only the novel sentences with no bank match are
        #    translated live and spliced in - curated blocks are never
        #    overwritten by lower-quality machine output.
        texts, blocks, unmatched_idx = build_offline_translations(
            fields["event_type"], fields["severity"], fields["affected_area"],
            fields["required_action"], state)
        store_translations(alert_id, texts)
        if unmatched_idx:
            for code, blist in blocks.items():
                patched = list(blist)
                for idx in unmatched_idx:
                    t = translate_text(blist[idx], code)
                    if t:
                        patched[idx] = t
                texts[code] = " ".join(patched)
            store_translations(alert_id, texts)
            log_event("LANGUAGE",
                      f"live gap-fill for alert #{alert_id}: "
                      f"{len(unmatched_idx)} novel sentence(s)")
    row = db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    return jsonify(alert_to_dict(row)), 201


@app.route("/api/alerts", methods=["GET"])
@timed("API")
def api_list_alerts():
    """List all processed alerts (newest first)."""
    db = get_db()
    rows = db.execute("SELECT * FROM alerts ORDER BY id DESC").fetchall()
    return jsonify([alert_to_dict(r) for r in rows])


@app.route("/api/alerts/<int:alert_id>", methods=["GET"])
@timed("API")
def api_get_alert(alert_id):
    """Return the complete processed alert: official, fields, translations, visual."""
    db = get_db()
    row = db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if row is None:
        return error_response(f"No alert found with id {alert_id}.", 404)
    payload = alert_to_dict(row)
    if row["sms_result"]:
        payload["sms_result"] = json.loads(row["sms_result"])
    if row["relay_result"]:
        payload["relay_result"] = json.loads(row["relay_result"])
    return jsonify(payload)


@app.route("/api/alerts/<int:alert_id>/simulate-delivery", methods=["POST"])
@timed("API")
def api_simulate_delivery(alert_id):
    """Chunk the chosen translation into SMS and simulate delivery."""
    db = get_db()
    row = db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if row is None:
        return error_response(f"No alert found with id {alert_id}.", 404)
    data = request.get_json(silent=True) or {}
    lang = data.get("lang", "en")
    translations = get_translations(row)
    if lang not in translations:
        return error_response(f"Language '{lang}' is not available for this alert.", 400)
    entry = translations[lang]
    result = simulate_sms_delivery(entry["text"])
    result["language"] = entry["language"]
    result["lang_code"] = lang
    result["translation_pending"] = entry["pending"]
    ussd = build_ussd(translations)
    db.execute("UPDATE alerts SET sms_result = ? WHERE id = ?",
               (json.dumps(result, ensure_ascii=False), alert_id))
    db.commit()
    return jsonify({"sms": result, "ussd": ussd})


@app.route("/api/alerts/<int:alert_id>/simulate-relay", methods=["POST"])
@timed("API")
def api_simulate_relay(alert_id):
    """Run the 5-volunteer offline relay with integrity checks."""
    db = get_db()
    row = db.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if row is None:
        return error_response(f"No alert found with id {alert_id}.", 404)
    fields = {
        "event_type": row["disaster_type"],
        "severity": row["severity"],
        "affected_area": row["affected_area"],
        "required_action": row["required_action"],
        "simplified_text": row["simplified_text"],
    }
    result = simulate_relay(fields)
    db.execute("UPDATE alerts SET relay_result = ? WHERE id = ?",
               (json.dumps(result, ensure_ascii=False), alert_id))
    db.commit()
    return jsonify(result)


def tracker_payload(db, alert_id):
    """Build the recipient tracker payload with live statistics."""
    recipients = db.execute(
        "SELECT * FROM recipients ORDER BY id").fetchall()
    recs = [dict(r) for r in recipients]
    stats = {
        "total": len(recs),
        "delivered": sum(1 for r in recs if r["delivery_status"] == "Delivered"),
        "read": sum(1 for r in recs if r["read_status"] == "Read"),
        "acknowledged": sum(1 for r in recs if r["ack_status"] == "Acknowledged"),
        "failed": sum(1 for r in recs if r["delivery_status"] == "Failed"),
        "no_response": sum(1 for r in recs if r["ack_status"] == "No Response"),
    }
    return {"alert_id": alert_id, "stats": stats, "recipients": recs}


@app.route("/api/alerts/<int:alert_id>/tracker", methods=["GET"])
@timed("API")
def api_tracker(alert_id):
    """Return synthetic recipient acknowledgement data + stats."""
    db = get_db()
    if db.execute("SELECT id FROM alerts WHERE id = ?",
                  (alert_id,)).fetchone() is None:
        return error_response(f"No alert found with id {alert_id}.", 404)
    return jsonify(tracker_payload(db, alert_id))


@app.route("/api/alerts/<int:alert_id>/acknowledge", methods=["POST"])
@timed("API")
def api_acknowledge(alert_id):
    """A recipient tapped "I'm Safe" on their phone. Called from the phone view."""
    db = get_db()
    if db.execute("SELECT id FROM alerts WHERE id = ?",
                  (alert_id,)).fetchone() is None:
        return error_response(f"No alert found with id {alert_id}.", 404)
    data = request.get_json(silent=True) or {}
    rid = data.get("recipient_id")
    if not isinstance(rid, int):
        return error_response("Malformed request: 'recipient_id' must be a number.", 400)
    rec = db.execute("SELECT * FROM recipients WHERE id = ?",
                     (rid,)).fetchone()
    if rec is None:
        return error_response(f"No recipient found with id {rid}.", 404)
    db.execute("""UPDATE recipients SET delivery_status='Delivered',
                  read_status='Read', ack_status='Acknowledged' WHERE id = ?""",
               (rid,))
    db.commit()
    log_event("TRACKER", f"recipient #{rid} ({rec['name']}) tapped 'I'm Safe' (phone view)")
    return jsonify({"ok": True, "recipient_id": rid,
                    "tracker": tracker_payload(db, alert_id)})


# ----------------------------------------------------------------------------
# ONE MESSAGE (recipient phone view) - the single bandwidth-aware text one
# villager receives. The operator's pipeline has six stages; the community
# member only ever sees this one message, and confirms from their own phone.
# ----------------------------------------------------------------------------

def sms_encoding(text):
    """Return (encoding_name, chars_per_segment) for an SMS text.

    GSM-7 (plain Latin) packs 160 chars per segment; any non-Latin script
    (Hindi, Malayalam, Tamil, ...) forces Unicode encoding at 70 chars per
    segment. Getting this wrong is the classic low-network demo mistake.
    """
    if all(ord(c) < 128 for c in text):
        return ("GSM-7", 160)
    return ("Unicode", 70)


def compose_one_message(alert_row, recipient):
    """Compose the single message a recipient receives, budgeted for low bandwidth.

    Takes the recipient's language translation and greedily keeps the most
    important whole sentences (header -> severity -> actions -> safety) that
    fit inside 2 SMS segments. The icon is dropped: it eats bytes and renders
    as an empty box on feature phones. Returns the message plus its bandwidth
    cost so the operator can see exactly what the network must carry.
    """
    translations = get_translations(alert_row)
    code = "en"
    want = (recipient["language"] or "").strip().lower()
    for c, t in translations.items():
        if t["language"].lower() == want:
            code = c
            break
    entry = translations[code]
    body = re.sub(r"^[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF"
                  r"\U0001F000-\U0001F2FF\U0001F900-\U0001F9FF]+\s*",
                  "", entry["text"]).strip()
    enc, per_seg = sms_encoding(body)
    budget = per_seg * 2
    # \u0964 is the danda (\u0964) terminating sentences in Hindi and others
    sentences = [s.strip() for s in re.split(r"(?<=[.!?\u0964])\s+", body)
                 if s.strip()]
    msg, included, trimmed = "", 0, 0
    for s in sentences:
        cand = (msg + " " + s).strip()
        if len(cand) <= budget:
            msg, included = cand, included + 1
        else:
            trimmed += 1
    if not msg and sentences:  # first sentence alone exceeds budget: hard cut
        msg = sentences[0][:budget - 1] + "\u2026"
        included, trimmed = 1, len(sentences) - 1
    segments = -(-len(msg) // per_seg) if msg else 0
    return {
        "language": entry["language"],
        "language_code": code,
        "translation_pending": entry["pending"],
        "message": msg,
        "chars": len(msg),
        "bytes": len(msg.encode("utf-8")),
        "encoding": enc,
        "chars_per_segment": per_seg,
        "segments": segments,
        "fits_in_one_sms": segments <= 1,
        "sentences_included": included,
        "sentences_trimmed": trimmed,
    }


@app.route("/register")
def page_register():
    """Citizen registration: name, village, state, language, channel."""
    return render_template("register.html")


@app.route("/u")
def page_user():
    """Citizen home: my alerts, clean message + response buttons."""
    return render_template("user.html")


@app.route("/api/alerts/<int:alert_id>/one-message", methods=["GET"])
@timed("API")
def api_one_message(alert_id):
    """Return one recipient's bandwidth-aware message plus the recipient list."""
    db = get_db()
    row = db.execute("SELECT * FROM alerts WHERE id = ?",
                     (alert_id,)).fetchone()
    if row is None:
        return error_response(f"No alert found with id {alert_id}.", 404)
    recs = [dict(r) for r in
            db.execute("SELECT * FROM recipients ORDER BY id").fetchall()]
    if not recs:
        return error_response("No recipients in the database.", 404)
    rid = request.args.get("recipient_id", type=int)
    rec = next((r for r in recs if r["id"] == rid), None)
    if rec is None:  # default: first recipient still waiting to respond
        rec = next((r for r in recs if r["ack_status"] != "Acknowledged"),
                   recs[0])
    return jsonify({
        "alert_id": alert_id,
        "recipient": {k: rec[k] for k in
                      ("id", "name", "village", "state", "language", "channel",
                       "delivery_status", "read_status", "ack_status")},
        "recipients": [{"id": r["id"], "name": r["name"],
                        "language": r["language"],
                        "ack_status": r["ack_status"]} for r in recs],
        "one_message": compose_one_message(row, rec),
    })


@app.route("/api/alerts/<int:alert_id>/read", methods=["POST"])
@timed("API")
def api_mark_read(alert_id):
    """A recipient tapped 'Got it' on their phone. Called from the phone view."""
    db = get_db()
    if db.execute("SELECT id FROM alerts WHERE id = ?",
                  (alert_id,)).fetchone() is None:
        return error_response(f"No alert found with id {alert_id}.", 404)
    data = request.get_json(silent=True) or {}
    rid = data.get("recipient_id")
    if not isinstance(rid, int):
        return error_response("Malformed request: 'recipient_id' must be a number.", 400)
    rec = db.execute("SELECT * FROM recipients WHERE id = ?",
                     (rid,)).fetchone()
    if rec is None:
        return error_response(f"No recipient found with id {rid}.", 404)
    db.execute("""UPDATE recipients SET delivery_status='Delivered',
                  read_status='Read' WHERE id = ?""", (rid,))
    db.commit()
    log_event("TRACKER",
              f"recipient #{rid} ({rec['name']}) marked message as read (phone view)")
    return jsonify({"ok": True, "recipient_id": rid,
                    "tracker": tracker_payload(db, alert_id)})


@app.route("/api/languages", methods=["GET"])
@timed("API")
def api_languages():
    """Language choices for the registration form."""
    return jsonify([{"code": code, "name": name} for code, name in LANG_CHOICES])


@app.route("/api/register", methods=["POST"])
@timed("API")
def api_register():
    """Register a citizen. Their details are stored and alerts go to them."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("Please submit a valid registration form.", 400)
    fields = ("name", "village", "state", "language", "channel")
    if any(not isinstance(data.get(key, ""), str) for key in fields):
        return error_response("Registration fields must contain text.", 400)
    name, village, state, language, channel = (data.get(key, "").strip() for key in fields)
    if not name:
        return error_response("Please enter your name.", 400)
    if len(name) > 120 or len(village) > 120:
        return error_response("Name and village must each be at most 120 characters.", 400)
    if state not in config.STATE_LANGUAGES:
        return error_response("Please choose a valid state.", 400)
    if language not in [label for _, label in LANG_CHOICES]:
        return error_response("Please choose a language.", 400)
    if channel not in ("SMS", "App", "Community Relay"):
        return error_response("Please choose a valid delivery channel.", 400)
    db = get_db()
    cur = db.execute(
        """INSERT INTO recipients (name, village, state, language, channel,
           delivery_status, read_status, ack_status)
           VALUES (?, ?, ?, ?, ?, 'Delivered', 'Not Read', 'No Response')""",
        (name, village or "—", state, language, channel))
    db.commit()
    rec = db.execute("SELECT * FROM recipients WHERE id = ?",
                     (cur.lastrowid,)).fetchone()
    log_event("REGISTER",
              f"citizen registered: {name} ({village or '—'}, {state}, "
              f"{language}) as recipient #{cur.lastrowid}")
    return jsonify({"ok": True, "recipient": dict(rec)})


@app.route("/api/users/<int:uid>", methods=["GET"])
@timed("API")
def api_user(uid):
    """Return one registered citizen's stored details."""
    db = get_db()
    rec = db.execute("SELECT * FROM recipients WHERE id = ?",
                     (uid,)).fetchone()
    if rec is None:
        return error_response(f"No registered user found with id {uid}.", 404)
    return jsonify(dict(rec))


@app.route("/api/users/<int:uid>/alerts", methods=["GET"])
@timed("API")
def api_user_alerts(uid):
    """Alerts for one citizen: their state's alerts first, message in their
    language, with their read/acknowledge status. This is the citizen side."""
    db = get_db()
    rec = db.execute("SELECT * FROM recipients WHERE id = ?",
                     (uid,)).fetchone()
    if rec is None:
        return error_response(f"No registered user found with id {uid}.", 404)
    rows = db.execute("SELECT * FROM alerts ORDER BY id DESC").fetchall()
    alerts = []
    for row in rows:
        row = dict(row)
        msg = compose_one_message(row, dict(rec))
        alerts.append({
            "id": row["id"],
            "disaster_type": row["disaster_type"],
            "state": row["state"],
            "severity": row["severity"],
            "severity_color": row["severity_color"],
            "urgency_label": URGENCY_LABEL.get(row["severity_color"], row["severity"]),
            "affected_area": row["affected_area"],
            "created_at": row["created_at"],
            "message": msg["message"],
            "language": msg["language"],
            "in_my_state": row["state"] == rec["state"],
            "read_status": rec["read_status"],
            "ack_status": rec["ack_status"],
        })
    alerts.sort(key=lambda a: (not a["in_my_state"], -a["id"]))
    return jsonify({"user": dict(rec), "alerts": alerts})


@app.route("/api/alerts/<int:alert_id>/messages", methods=["GET"])
@timed("API")
def api_alert_messages(alert_id):
    """Organiser preview: the one message composed in each available language."""
    db = get_db()
    row = db.execute("SELECT * FROM alerts WHERE id = ?",
                     (alert_id,)).fetchone()
    if row is None:
        return error_response(f"No alert found with id {alert_id}.", 404)
    row = dict(row)
    out = []
    for code, t in get_translations(row).items():
        msg = compose_one_message(row, {"language": t["language"]})
        out.append({"code": code, "language": t["language"],
                    "pending": t["pending"], "message": msg["message"],
                    "segments": msg["segments"], "chars": msg["chars"]})
    return jsonify({"alert_id": alert_id, "messages": out})


@app.route("/api/sample-alerts", methods=["GET"])
@timed("API")
def api_sample_alerts():
    """Return the two pre-loaded fictional sample alerts for the intake page."""
    return jsonify([{k: s[k] for k in ("key", "title", "state", "disaster_type", "official")}
                    for s in SAMPLE_ALERTS])


@app.route("/api/diagnostics", methods=["GET"])
@timed("API")
def api_diagnostics():
    """Return recent backend events for the diagnostics panel."""
    return jsonify(list(DIAG_BUFFER))


@app.route("/api/reset", methods=["POST"])
@timed("API")
def api_reset():
    """One-click demo reset: wipe DB, recreate schema, reseed everything."""
    import seed as seed_module
    seed_module.reset_database()
    log_event("DB", "demo reset complete (wiped, recreated, reseeded)")
    return jsonify({"ok": True, "message": "Demo data reset complete."})


@app.route("/api/health", methods=["GET"])
def api_health():
    """Backend health: status, DB, counts, memory, port, routes."""
    t0 = time.perf_counter()
    db_ok, alerts_count, recipients_count = False, 0, 0
    try:
        conn = direct_db()
        alerts_count = conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        recipients_count = conn.execute("SELECT COUNT(*) FROM recipients").fetchone()[0]
        conn.close()
        db_ok = True
    except Exception as exc:
        log_event("ERROR", f"health check DB failure: {exc}")
    try:
        import psutil
        memory_mb = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
    except Exception:
        memory_mb = None
    routes = sorted({str(r) for r in app.url_map.iter_rules()
                     if str(r).startswith("/api")})
    payload = {
        "status": "ok" if db_ok else "degraded",
        "db": "connected" if db_ok else "error",
        "alerts_count": alerts_count,
        "recipients_count": recipients_count,
        "memory_mb": memory_mb,
        "port": BOUND_PORT["value"],
        "host": config.HOST,
        "uptime_s": round(time.time() - APP_START_TIME, 1),
        "api_routes": routes,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
    }
    return jsonify(payload)


# ----------------------------------------------------------------------------
# PAGE ROUTES
# ----------------------------------------------------------------------------
@app.route("/")
def page_home():
    """Home / dashboard page."""
    return render_template("index.html")


@app.route("/new")
def page_new_alert():
    """Alert intake page (the processor in 'new alert' mode)."""
    return render_template("processor.html", mode="new", alert_id=None)


@app.route("/alert/<int:alert_id>")
def page_processor(alert_id):
    """Alert processor page showing the six-stage pipeline."""
    return render_template("processor.html", mode="view", alert_id=alert_id)


@app.route("/alert/<int:alert_id>/delivery")
def page_delivery(alert_id):
    """Delivery simulator page (SMS / USSD / relay)."""
    return render_template("delivery.html", alert_id=alert_id)


@app.route("/alert/<int:alert_id>/tracker")
def page_tracker(alert_id):
    """Acknowledgement / receipt tracker dashboard."""
    return render_template("tracker.html", alert_id=alert_id)


@app.route("/debug")
def page_debug():
    """Debug console with timestamped backend logs."""
    return render_template("debug.html")


# ----------------------------------------------------------------------------
# ERROR HANDLERS - friendly pages, never raw tracebacks.
# ----------------------------------------------------------------------------
@app.errorhandler(404)
def handle_404(exc):
    """Render the friendly error page for unknown routes."""
    if request.path.startswith("/api"):
        return jsonify({"error": "Not found."}), 404
    return render_template("error.html", code=404,
                           message="That page does not exist."), 404


@app.errorhandler(500)
def handle_500(exc):
    """Render the friendly error page for server failures."""
    if request.path.startswith("/api"):
        return jsonify({"error": "Something went wrong on the server."}), 500
    return render_template("error.html", code=500,
                           message="Something went wrong on the server."), 500


@app.errorhandler(400)
def handle_400(exc):
    """Render the friendly error page for bad requests."""
    if request.path.startswith("/api"):
        return jsonify({"error": "Bad request."}), 400
    return render_template("error.html", code=400,
                           message="That request was not understood."), 400


# ----------------------------------------------------------------------------
# STARTUP - port fallback, LAN IP detection, printed URLs.
# ----------------------------------------------------------------------------
def get_lan_ip():
    """Detect the laptop's LAN IP without sending any traffic."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))  # no packets are actually sent
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


def find_free_port(start):
    """Find a free port, trying start, start+1, ... so we never crash on a busy port."""
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((config.HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port found.")


def main():
    """Entry point: init DB, pick a port, print URLs, serve."""
    init_db()
    port = find_free_port(config.DEFAULT_PORT)
    BOUND_PORT["value"] = port
    lan_ip = get_lan_ip()
    print("=" * 60)
    print("  LastMile Alert - Emergency Warning Accessibility Platform")
    print("=" * 60)
    print(f"  Open on this laptop : http://127.0.0.1:{port}")
    print(f"  Open on your Wi-Fi  : http://{lan_ip}:{port}")
    print("  (Phones/laptops on the same Wi-Fi can use the second URL.)")
    print("=" * 60)
    log_event("SERVER", f"serving on {config.HOST}:{port} (LAN {lan_ip})")
    app.run(host=config.HOST, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
