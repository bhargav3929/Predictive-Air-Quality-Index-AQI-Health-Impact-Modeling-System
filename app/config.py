"""Settings read from environment / .env, every one with a default."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")

PLACEHOLDER = "your_groq_key_here"
VERSION = "1.0.0"


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() in ("1", "true", "yes")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except ValueError:
        return default


def _str(name: str, default: str) -> str:
    return (os.getenv(name) or "").strip() or default


PROJECT_NAME = _str("PROJECT_NAME", "AeroPulse - Air Quality Forecasting & Respiratory Health Advisory")
PROJECT_SLUG = _str("PROJECT_SLUG", "aeropulse-aqi-health")
PROJECT_NUMBER = _int("PROJECT_NUMBER", 4)
HOST = _str("HOST", "127.0.0.1")
PORT = _int("PORT", 8000 + PROJECT_NUMBER)
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 8)
GROQ_MODEL = _str("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_FALLBACK_MODEL = _str("GROQ_FALLBACK_MODEL", "llama-3.3-70b-versatile")
DEMO_MODE = _bool("DEMO_MODE", False)
DEMO_TICKET_SECRET = os.getenv("DEMO_TICKET_SECRET", "").strip()
DEMO_DAILY_CAP = _int("DEMO_DAILY_CAP", 200)
DEMO_MAX_UPLOAD_MB = _int("DEMO_MAX_UPLOAD_MB", 3)
TRUST_PROXY = _bool("TRUST_PROXY", False)


def key_usable(k: str | None) -> bool:
    k = (k or "").strip()
    return bool(k) and k != PLACEHOLDER and " " not in k and k.startswith("gsk_")


def mask(k: str) -> str:
    return "gsk_…" + k[-4:]
