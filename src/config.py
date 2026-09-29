"""Environment config loading. Fails loudly if required keys are missing."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

REQUIRED_KEYS = [
    "GOOGLE_SHEETS_ID",
    "GOOGLE_SERVICE_ACCOUNT_EMAIL",
    "GOOGLE_PRIVATE_KEY",
    "CLOUDINARY_CLOUD_NAME",
    "CLOUDINARY_UPLOAD_PRESET",
]


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(
            f"Missing required env var: {key}. Copy .env.example to .env and fill it in."
        )
    return value


def _check_all_required():
    missing = [k for k in REQUIRED_KEYS if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            "Missing required env vars: " + ", ".join(missing) +
            ". Copy .env.example to .env and fill it in."
        )


_check_all_required()

GOOGLE_SHEETS_ID = _require("GOOGLE_SHEETS_ID")
GOOGLE_SERVICE_ACCOUNT_EMAIL = _require("GOOGLE_SERVICE_ACCOUNT_EMAIL")
GOOGLE_PRIVATE_KEY = _require("GOOGLE_PRIVATE_KEY").replace("\\n", "\n")

CLOUDINARY_CLOUD_NAME = _require("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_UPLOAD_PRESET = _require("CLOUDINARY_UPLOAD_PRESET")

UTM_SUFFIX = os.getenv("UTM_SUFFIX", "?utm_source=V0.2report&utm_medium=organic&utm_campaign=GMBA")

# Posting goes through the Google Search "Business Profile" panel (search
# this query -> Posts tile -> Add post), not the separate business.google.com
# dashboard — confirmed as the real flow for this account.
GBP_SEARCH_QUERY = os.getenv("GBP_SEARCH_QUERY", "ken research")

BATCH_SIZE_MAX = int(os.getenv("BATCH_SIZE_MAX", "20"))
POSTING_WINDOW_HOURS = float(os.getenv("POSTING_WINDOW_HOURS", "8"))

# Description length bounds (PRD §6.4)
DESCRIPTION_MIN_CHARS = 1400
DESCRIPTION_MAX_CHARS = 1500

# Jitter band applied to the base posting interval (PRD §6.3: ±30-40%)
JITTER_MIN_PCT = 0.30
JITTER_MAX_PCT = 0.40

AUTH_DIR = ROOT_DIR / "auth"
CHATGPT_PROFILE_DIR = AUTH_DIR / "chatgpt_profile"
GBP_PROFILE_DIR = AUTH_DIR / "gbp_profile"
LOGS_DIR = ROOT_DIR / "logs"

# Uploaded as a real file attachment before every image generation — the
# prompt's "logo already provided by me" claim was a false assumption
# without this: ChatGPT frequently refused with "I don't have the logo
# file", verified live as the root cause of most image-generation failures.
KEN_RESEARCH_LOGO_PATH = ROOT_DIR / "assets" / "ken_research_logo.png"

# Real Chrome via CDP attach (see src/browser.py) — separate debug ports so
# both dedicated profiles can be open at once if needed. Deliberately NOT
# 9222/9223: other automation tools on this machine squat the conventional
# CDP ports, and connecting to a foreign Chrome sends prompts into the
# wrong browser (happened live — see browser.py ownership check).
CHATGPT_CDP_PORT = int(os.getenv("CHATGPT_CDP_PORT", "9333"))
GBP_CDP_PORT = int(os.getenv("GBP_CDP_PORT", "9334"))
