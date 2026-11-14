"""Single-user login. Tailscale keeps strangers off the network; this password is the second lock."""
import base64
import hashlib
import hmac
import os
import time
from collections import defaultdict

from fastapi import HTTPException, Request

from . import config

_failures: dict[str, list[float]] = defaultdict(list)
MAX_FAILS, WINDOW = 5, 600


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def check_password(pw: str) -> bool:
    stored = config.get_secret("PASSWORD_HASH")
    try:
        _, salt_b64, h_b64 = stored.split("$")
        h = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt_b64), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(h, base64.b64decode(h_b64))
    except ValueError:
        return False


def password_set() -> bool:
    return config.has_secret("PASSWORD_HASH")


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def throttle(request: Request) -> None:
    ip = client_ip(request)
    _failures[ip] = [t for t in _failures[ip] if t > time.time() - WINDOW]
    if len(_failures[ip]) >= MAX_FAILS:
        raise HTTPException(429, "Too many wrong passwords. Wait 10 minutes.")


def record_failure(request: Request) -> None:
    _failures[client_ip(request)].append(time.time())


def logged_in(request: Request) -> bool:
    return bool(request.session.get("auth"))


def require(request: Request) -> None:
    if not logged_in(request):
        raise HTTPException(401, "Please log in.")
