"""Dashboard login (needed only for access from outside this computer)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ..config import get_settings
from ..security import (COOKIE, SESSION_TTL, authorized, is_direct_local, login_allowed, make_session,
                        password_ok)

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


class Login(BaseModel):
    password: str


def _headers(request: Request) -> dict[str, str]:
    return {k.lower(): v for k, v in request.headers.items()}


@router.get("/status")
def status(request: Request):
    client = request.client.host if request.client else None
    return {
        "authenticated": authorized(client, _headers(request)),
        # "local" = this browser is exempt from login (direct use on this computer, unless
        # REQUIRE_LOGIN_LOCAL is on). The dashboard skips the login page when it is true.
        "local": is_direct_local(client, _headers(request)) and not settings.require_login_local,
        "password_set": bool(settings.dashboard_password),
    }


@router.post("/login")
def login(body: Login, request: Request, response: Response):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
    if not login_allowed(ip):
        raise HTTPException(429, "Too many attempts, wait a minute")
    if not settings.dashboard_password:
        raise HTTPException(400, "No DASHBOARD_PASSWORD is configured in backend/.env")
    if not password_ok(body.password):
        raise HTTPException(401, "Wrong password")
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(COOKIE, make_session(), max_age=SESSION_TTL, httponly=True, samesite="lax",
                        secure=secure, path="/")
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}
