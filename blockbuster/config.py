"""Config and data directories, following the XDG base dir spec."""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "blockbuster"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "blockbuster"
POSTER_DIR = DATA_DIR / "posters"
BACKUP_DIR = DATA_DIR / "backups"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "movies.db"
CONFIG_PATH = CONFIG_DIR / "config.json"


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    POSTER_DIR.mkdir(parents=True, exist_ok=True)


def backup_now() -> Path:
    """Copy the live database to a timestamped file under BACKUP_DIR."""
    ensure_dirs()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"movies-{ts}.db"
    shutil.copy2(DB_PATH, dest)
    return dest


def load_config() -> dict:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(cfg: dict) -> None:
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


def poster_full_path(poster_path: str | None) -> Path | None:
    """Resolve a stored poster reference to an absolute path.

    Rows store just the filename (relative to POSTER_DIR) so the database
    stays portable across machines/home directories. Older rows may carry
    a stale absolute path baked in from a previous $HOME; fall back to
    looking up the same filename under the current POSTER_DIR.
    """
    if not poster_path:
        return None
    p = Path(poster_path)
    if not p.is_absolute():
        return POSTER_DIR / p
    if p.exists():
        return p
    return POSTER_DIR / p.name


def get_api_key() -> str | None:
    env_key = os.environ.get("OMDB_API_KEY")
    if env_key:
        return env_key
    return load_config().get("omdb_api_key")


def set_api_key(key: str) -> None:
    cfg = load_config()
    cfg["omdb_api_key"] = key
    save_config(cfg)


def get_watchmode_key() -> str | None:
    env_key = os.environ.get("WATCHMODE_API_KEY")
    if env_key:
        return env_key
    return load_config().get("watchmode_api_key")


def set_watchmode_key(key: str) -> None:
    cfg = load_config()
    cfg["watchmode_api_key"] = key
    save_config(cfg)
