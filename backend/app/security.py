"""Access control for the dashboard API and WebSockets.

Rules (Vobiz webhooks under /vobiz/* are NOT covered here; they carry per-call HMAC tokens):
  * Direct local use (browser on this machine -> localhost, no proxy headers) needs no login,
    so the normal desktop setup keeps working with zero friction.
  * Anything else (through the ngrok tunnel, another PC, a server deployment) needs a session
    cookie (POST /api/auth/login with DASHBOARD_PASSWORD) or `Authorization: Bearer <password>`.
    With no DASHBOARD_PASSWORD set, remote access is refused outright.
  * Browser requests from foreign origins are refused (CSRF / cross-site WebSocket protection).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import defaultdict, deque

from .config import get_settings

settings = get_settings()
COOKIE = "va_session"
SESSION_TTL = 7 * 24 * 3600
PUBLIC_PATHS = ("/api/health", "/api/auth/")
PROTECTED_PREFIXES = ("/api/", "/ws/")
PROTECTED_EXACT = ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost")
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host")


def _secret() -> bytes:
    return hashlib.sha256(("va-session:" + settings.dashboard_password).encode()).digest()


def make_session() -> str:
    issued = str(int(time.time()))
    sig = hmac.new(_secret(), issued.encode(), hashlib.sha256).hexdigest()
    return f"{issued}.{sig}"


def valid_session(value: str | None) -> bool:
    if not value or not settings.dashboard_password or "." not in value:
        return False
    issued, sig = value.split(".", 1)
    if not issued.isdigit() or time.time() - int(issued) > SESSION_TTL:
        return False
    expected = hmac.new(_secret(), issued.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def password_ok(candidate: str | None) -> bool:
    return bool(settings.dashboard_password) and hmac.compare_digest(
        (candidate or "").encode(), settings.dashboard_password.encode())


def is_direct_local(client_host: str | None, headers: dict[str, str]) -> bool:
    """Loopback peer with no proxy headers. ngrok always adds X-Forwarded-For, so tunnel
    traffic (which also arrives from 127.0.0.1) is never treated as local."""
    return (client_host or "") in LOCAL_HOSTS and not any(h in headers for h in PROXY_HEADERS)


def _cookies(headers: dict[str, str]) -> dict[str, str]:
    out = {}
    for part in headers.get("cookie", "").split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            out[k] = v
    return out


def authorized(client_host: str | None, headers: dict[str, str]) -> bool:
    if is_direct_local(client_host, headers) and not settings.require_login_local:
        return True
    if valid_session(_cookies(headers).get(COOKIE)):
        return True
    auth = headers.get("authorization", "")
    return auth.lower().startswith("bearer ") and password_ok(auth[7:].strip())


def needs_auth(path: str) -> bool:
    if path.startswith(PUBLIC_PATHS):
        return False
    return path.startswith(PROTECTED_PREFIXES) or path in PROTECTED_EXACT


# ------------------------------------------------------------------ login rate limit
_attempts: dict[str, deque] = defaultdict(lambda: deque(maxlen=10))


def login_allowed(ip: str) -> bool:
    """At most 5 attempts per minute per client."""
    now = time.time()
    q = _attempts[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= 5:
        return False
    q.append(now)
    return True


class AccessGuard:
    """Pure ASGI middleware covering both HTTP and WebSocket scopes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not needs_auth(scope.get("path", "")):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        client = (scope.get("client") or (None, None))[0]
        is_ws = scope["type"] == "websocket"
        origin = headers.get("origin")
        if origin and origin not in settings.cors_origin_list and (is_ws or scope.get("method") not in SAFE_METHODS):
            return await self._deny(scope, receive, send, 403, "Request from an unknown website was blocked")
        if scope.get("method") == "OPTIONS" or authorized(client, headers):
            return await self.app(scope, receive, send)
        detail = ("Login required" if settings.dashboard_password
                  else "Remote access is disabled: set DASHBOARD_PASSWORD in backend/.env")
        return await self._deny(scope, receive, send, 401, detail)

    @staticmethod
    async def _deny(scope, receive, send, status: int, detail: str) -> None:
        if scope["type"] == "websocket":
            # Accept, then close with 1008 (policy violation): a handshake rejection only reaches
            # browsers as an opaque 1006, and the dashboard needs 1008 to know it must log in.
            # Nothing is ever sent between accept and close.
            message = await receive()
            if message.get("type") == "websocket.connect":
                await send({"type": "websocket.accept"})
                await send({"type": "websocket.close", "code": 1008, "reason": detail[:120]})
            return
        body = json.dumps({"detail": detail}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
