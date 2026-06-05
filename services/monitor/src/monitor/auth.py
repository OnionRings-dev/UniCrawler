from __future__ import annotations

import hashlib
import hmac
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode

from fastapi import HTTPException, Request, status

from monitor.state import cfg

COOKIE_NAME = "unicrawler_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


def verify_password(password: str) -> bool:
    expected = cfg().admin_password_hash
    if not expected:
        return False
    actual = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(actual, expected)


def make_session(username: str) -> str:
    expires = int(time.time()) + SESSION_TTL_SECONDS
    body = f"{username}:{expires}"
    sig = hmac.new(cfg().session_secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlsafe_b64encode(f"{body}:{sig}".encode("utf-8")).decode("ascii")


def read_session(token: str | None) -> str | None:
    if not token:
        return None
    try:
        raw = urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, expires_raw, sig = raw.rsplit(":", 2)
        body = f"{username}:{expires_raw}"
        expected = hmac.new(cfg().session_secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(expires_raw) < int(time.time()):
            return None
        if username != cfg().admin_username:
            return None
        return username
    except Exception:
        return None


def require_admin(request: Request) -> str:
    username = read_session(request.cookies.get(COOKIE_NAME))
    if not username:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
    return username
