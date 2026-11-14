"""Secrets live in DATA_DIR/.env (never committed). Everything else lives in the database."""
import os
import secrets
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
ENV_FILE = DATA_DIR / ".env"
DB_FILE = DATA_DIR / "assistant.db"

_lock = threading.Lock()
_cache: dict[str, str] = {}


def _load() -> None:
    _cache.clear()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            _cache[k.strip()] = v.strip().strip('"')


def _save() -> None:
    lines = ["# Assistant secrets - written by the setup wizard. Never commit this file."]
    lines += [f'{k}="{v}"' for k, v in sorted(_cache.items())]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        ENV_FILE.chmod(0o600)
    except OSError:
        pass


def get_secret(key: str, default: str = "") -> str:
    return _cache.get(key) or os.environ.get(key, default)


def set_secret(key: str, value: str) -> None:
    value = value.strip().replace("\n", "").replace('"', "")
    with _lock:
        if value:
            _cache[key] = value
        else:
            _cache.pop(key, None)
        _save()


def has_secret(key: str) -> bool:
    return bool(get_secret(key))


def init() -> None:
    _load()
    if not _cache.get("SESSION_SECRET"):
        set_secret("SESSION_SECRET", secrets.token_urlsafe(48))


init()
