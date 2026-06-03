# utils/channel_list.py
import json
import os
from utils.logger import get_logger, log_info

logger = get_logger(__name__)


def load_batch_channels() -> list:
    """
    Load ONLY the batch file specified by FILE_NUMBER in .env
    e.g. FILE_NUMBER=001 → channels/batch_001.json
    """
    from config.settings import CHANNELS_DIR, FILE_NUMBER

    batch_file = os.path.join(CHANNELS_DIR, f"batch_{FILE_NUMBER}.json")

    if not os.path.exists(batch_file):
        raise FileNotFoundError(f"❌ Batch file not found: {batch_file}")

    with open(batch_file, "r") as f:
        channels = json.load(f)

    # deduplicate
    seen = set()
    unique = []
    for ch in channels:
        username = ch.get("username")
        if username and username not in seen:
            seen.add(username)
            unique.append(ch)

    log_info(
        logger,
        "Batch channels loaded",
        "batch_channels_loaded",
        batch_file=f"batch_{FILE_NUMBER}.json",
        total_channels=len(unique),
    )
    return unique


def load_retired_usernames() -> set:
    """Load retired usernames for exclusion check."""
    from config.settings import RETIRED_FILE

    if not os.path.exists(RETIRED_FILE):
        return set()

    try:
        with open(RETIRED_FILE, "r") as f:
            retired = json.load(f)
        return {ch.get("username") for ch in retired if ch.get("username")}
    except (json.JSONDecodeError, Exception):
        return set()
