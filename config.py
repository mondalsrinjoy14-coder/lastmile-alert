"""Central configuration for LastMile Alert.

Every tunable value lives here. Nothing else in the codebase hardcodes
ports, limits, paths, or counts - change them here and the app follows.
"""

# ---------------------------------------------------------------- server ---
HOST = "0.0.0.0"          # bind to all interfaces so phones on the LAN can reach it
DEFAULT_PORT = 5000        # first port to try; app.py walks upward if busy

# ------------------------------------------------------- delivery sim ---
SMS_CHUNK_SIZE = 160       # max characters per SMS chunk (GSM standard)
SIMULATED_NETWORK_DELAY = 0.05  # fake seconds of "network" delay between stages

# ------------------------------------------------------- offline relay ---
RELAY_VOLUNTEER_COUNT = 5  # volunteers in the community relay chain

# ------------------------------------------------------- storage ---
DATABASE_PATH = "alerts.db"
DEBUG_LOG_PATH = "debug.log"

# ------------------------------------------------------- validation ---
MAX_ALERT_CHARS = 2000     # official alerts longer than this are rejected

# ------------------------------------------------------- disasters ---
DISASTER_TYPES = ["Flood", "Cyclone", "Earthquake", "Heatwave"]

# ------------------------------------------------------- state -> language ---
# Maps every Indian state / UT to (regional language name, language code).
# The language code is the key used inside data/translations.json.
# To add a language for a state, add the JSON entry - no code change needed.
STATE_LANGUAGES = {
    "Andhra Pradesh": ("Telugu", "te"),
    "Arunachal Pradesh": ("English", "en"),
    "Assam": ("Assamese", "as"),
    "Bihar": ("Hindi", "hi"),
    "Chhattisgarh": ("Hindi", "hi"),
    "Delhi": ("Hindi", "hi"),
    "Goa": ("Konkani", "kok"),
    "Gujarat": ("Gujarati", "gu"),
    "Haryana": ("Hindi", "hi"),
    "Himachal Pradesh": ("Hindi", "hi"),
    "Jammu & Kashmir": ("Urdu", "ur"),
    "Jharkhand": ("Hindi", "hi"),
    "Karnataka": ("Kannada", "kn"),
    "Kerala": ("Malayalam", "ml"),
    "Ladakh": ("Ladakhi", "lad"),
    "Lakshadweep": ("Malayalam", "ml"),
    "Madhya Pradesh": ("Hindi", "hi"),
    "Maharashtra": ("Marathi", "mr"),
    "Manipur": ("Manipuri", "mni"),
    "Meghalaya": ("English", "en"),
    "Mizoram": ("Mizo", "lus"),
    "Nagaland": ("English", "en"),
    "Odisha": ("Odia", "or"),
    "Puducherry": ("Tamil", "ta"),
    "Punjab": ("Punjabi", "pa"),
    "Rajasthan": ("Hindi", "hi"),
    "Sikkim": ("Nepali", "ne"),
    "Tamil Nadu": ("Tamil", "ta"),
    "Telangana": ("Telugu", "te"),
    "Tripura": ("Bengali", "bn"),
    "Uttar Pradesh": ("Hindi", "hi"),
    "Uttarakhand": ("Hindi", "hi"),
    "West Bengal": ("Bengali", "bn"),
    "Andaman & Nicobar": ("Hindi", "hi"),
    "Chandigarh": ("Hindi", "hi"),
    "Dadra and Nagar Haveli and Daman and Diu": ("Gujarati", "gu"),
}
