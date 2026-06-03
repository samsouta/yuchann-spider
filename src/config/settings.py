# config/settings.py
import json
import os
from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


# -------- APP CONFIG --------
MAX_CONCURRENT_ACCOUNTS     = _env_int("MAX_CONCURRENT_ACCOUNTS", 10)
BOOTSTRAP_WORKER            = _env_int("BOOTSTRAP_WORKER", 1)
UPDATE_WORKER               = _env_int("UPDATE_WORKER", max(MAX_CONCURRENT_ACCOUNTS - BOOTSTRAP_WORKER, 1))
BOT_TOKEN                   = os.getenv("BOT_TOKEN", "")
ADMIN_ID                    = _env_int("ADMIN_ID", 0)
FILE_NUMBER                 = os.getenv("FILE_NUMBER", "001")

# -------- TIMING --------
MAX_ATTEMPTS                = _env_int("MAX_ATTEMPTS", 2)
MAX_MESSAGES_PER_CHANNEL    = _env_int("MAX_MESSAGES_PER_CHANNEL", 100)
FIRST_IMPORT_SCAN_LIMIT     = _env_int("FIRST_IMPORT_SCAN_LIMIT", 5000)
MIN_DELAY_BETWEEN_CHANNELS  = _env_float("MIN_DELAY_BETWEEN_CHANNELS", 2.0)
MAX_DELAY_BETWEEN_CHANNELS  = _env_float("MAX_DELAY_BETWEEN_CHANNELS", 5.0)
MIN_DELAY_BETWEEN_MESSAGES  = _env_float("MIN_DELAY_BETWEEN_MESSAGES", 2.0)
MAX_DELAY_BETWEEN_MESSAGES  = _env_float("MAX_DELAY_BETWEEN_MESSAGES", 3.0)
PROGRESS_REPORT_INTERVAL    = _env_int("PROGRESS_REPORT_INTERVAL", 30)
RESOLVE_MIN_GAP             = _env_float("RESOLVE_MIN_GAP", 5.0)
EMPTY_CYCLE_THRESHOLD       = _env_int("EMPTY_CYCLE_THRESHOLD", 10)
BOOTSTRAP_NO_REAL_THRESHOLD = _env_int("BOOTSTRAP_NO_REAL_THRESHOLD", 2)
CYCLE_MIN_SECONDS           = _env_int("CYCLE_MIN_SECONDS", 30)
BOOTSTRAP_IDLE_SLEEP_SECONDS = _env_int("BOOTSTRAP_IDLE_SLEEP_SECONDS", 60)
UPDATE_IDLE_SLEEP_SECONDS   = _env_int("UPDATE_IDLE_SLEEP_SECONDS", 60)

# -------- FILES --------
CHANNELS_DIR                = os.getenv("CHANNELS_DIR", "channels")
BOOTSTRAP_CHANNEL_FILE_LIMIT = _env_int("BOOTSTRAP_CHANNEL_FILE_LIMIT", 50)
UPDATE_CHANNEL_FILE_LIMIT   = _env_int("UPDATE_CHANNEL_FILE_LIMIT", 1000)
RETIRED_FILE                = os.getenv("RETIRED_FILE", "channels/retired/retired.json")
SUSPENDED_FILE              = os.getenv("SUSPENDED_FILE", "data/suspended.json")
FAILED_FILE                 = os.getenv("FAILED_FILE", "data/failed_channels.json")
ACCOUNTS_FILE               = os.getenv("ACCOUNTS_FILE", "data/accounts.json")

# -------- LOGGING --------
LOG_LEVEL                   = os.getenv("LOG_LEVEL", "INFO")

# -------- DATABASE --------
DATABASE_URL                = os.getenv("DATABASE_URL", "")

# -------- LOAD ACCOUNTS --------
def load_accounts() -> list:
    with open(ACCOUNTS_FILE, "r") as f:
        accounts = json.load(f)
    return [acc for acc in accounts if acc.get("enable", True)]

ACCOUNTS = load_accounts()
