"""
SMS-2 IDOS — CONFIGURATION (Configuration over Coding — LAW-5)
Environment-driven. Defaults run instantly on any intranet server with zero
external services. Switch SMS2_DATABASE_URL to PostgreSQL for production
scale without touching source code.
"""
import os

APP_NAME = "SMS-2 IDOS"
APP_VERSION = "1.0"
APP_MOTTO = "People First. Production Always. Knowledge Forever."

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("SMS2_DATA_DIR", os.path.join(BASE_DIR, "data"))
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_URL = os.environ.get(
    "SMS2_DATABASE_URL",
    f"sqlite:///{os.path.join(DATA_DIR, 'sms2_idos.db')}",
)

SESSION_HOURS = int(os.environ.get("SMS2_SESSION_HOURS", "12"))
BACKUP_DIR = os.environ.get("SMS2_BACKUP_DIR", os.path.join(DATA_DIR, "backups"))
os.makedirs(BACKUP_DIR, exist_ok=True)

# AI adapter: 'none' (deterministic engines only — always available) or
# future: 'local', 'cloud'. Production never blocks on this (11.16).
AI_PROVIDER = os.environ.get("SMS2_AI_PROVIDER", "none")
