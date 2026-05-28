"""
Encryption utilities for webex-vacation-bot.
Key is auto-generated on first run and stored in /data/.key (same dir as SQLite DB).
"""
import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken  # noqa: F401 — re-exported for callers

log = logging.getLogger("vacation-bot.crypto")


def _key_path() -> Path:
    data_dir = Path(os.getenv("SQLITE_PATH", "/data/vacation.db")).parent
    return data_dir / ".key"


def load_or_create_key() -> bytes:
    """Return the Fernet encryption key, generating and persisting it if absent."""
    path = _key_path()
    if path.exists():
        return path.read_bytes().strip()

    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600 — owner read/write only
    log.info(
        "Encryption key generated and saved to %s — back up this file to keep your tokens recoverable",
        path,
    )
    return key


def get_fernet() -> Fernet:
    """Return a ready-to-use Fernet instance backed by the persistent key."""
    return Fernet(load_or_create_key())


def encrypt_str(data: str) -> str:
    """Encrypt *data* and return the Fernet token as a plain string."""
    return get_fernet().encrypt(data.encode()).decode()


def decrypt_str(data: str) -> str:
    """Decrypt a Fernet token string and return the original plaintext.

    Raises:
        InvalidToken: if the data cannot be decrypted (wrong key or not a Fernet token).
    """
    return get_fernet().decrypt(data.encode()).decode()
