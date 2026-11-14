"""Vobiz telephony client (Plivo-compatible REST + VobizXML).

Docs: https://www.vobiz.ai/docs  (REST base https://api.vobiz.ai/api/v1,
auth headers X-Auth-ID / X-Auth-Token, PascalCase paths with trailing slash).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import urllib.parse
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape

import httpx

from ..config import get_settings

log = logging.getLogger("vobiz")
settings = get_settings()

XML_HEADER = '<?xml version="1.0" encoding="UTF-8"?>'
INBOUND_APP_NAME = "GP3-Voice-Agent-Inbound"  # letters, digits, '-' and '_' only (Vobiz rule)
API_TIMEOUT = 8.0  # setup / call-record queries: never keep the dashboard waiting longer


class VobizError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def retryable(self) -> bool:
        """Rate limit / Vobiz outage: not the lead's fault, so the attempt should not count."""
        return self.status is not None and (self.status == 429 or self.status >= 500)


def _headers() -> dict[str, str]:
    return {
        "X-Auth-ID": settings.vobiz_auth_id,
        "X-Auth-Token": settings.vobiz_auth_token,
        "Content-Type": "application/json",
    }


def vobiz_number(e164: str) -> str:
    """Vobiz examples use country code + number without '+', e.g. 919876543210."""
    return e164.lstrip("+")


def url_token(call_uuid: str) -> str:
    """Per-call secret embedded in webhook / stream URLs (Vobiz can't send custom auth)."""
    key = (settings.vobiz_auth_token or settings.openai_api_key or "dev-secret").encode()
    return hmac.new(key, call_uuid.encode(), hashlib.sha256).hexdigest()[:32]


def verify_token(call_uuid: str, token: str) -> bool:
    return hmac.compare_digest(url_token(call_uuid), token or "")


def _public(path: str) -> str:
    return settings.public_base_url.rstrip("/") + path


# ------------------------------------------------------------------ inbound webhooks
def inbound_token() -> str:
    """Secret in the static inbound URLs. Changing INBOUND_TOKEN_SALT revokes a leaked URL."""
    return url_token(settings.inbound_token_salt)


def verify_inbound_token(token: str) -> bool:
    # Without an auth token the secret would be derived from a guessable fallback: refuse everything.
    return bool(settings.vobiz_auth_token) and hmac.compare_digest(inbound_token(), token or "")


def inbound_urls() -> dict[str, str]:
    tok = inbound_token()
    return {"answer": _public(f"/vobiz/inbound/{tok}/answer"), "hangup": _public(f"/vobiz/inbound/{tok}/hangup")}


def inbound_audio_url(key: str) -> str:
    return _public(f"/vobiz/inbound/{inbound_token()}/audio/{key}.wav")


# Inbound requests refused for a wrong / old secret since startup (shown by the inbound setup check:
# usually Vobiz still calling the URL of an earlier tunnel or salt).
inbound_rejections: dict[str, Any] = {"count": 0, "last_at": None}


def note_inbound_rejection() -> None:
    inbound_rejections["count"] += 1
    inbound_rejections["last_at"] = datetime.now(timezone.utc)


# ------------------------------------------------------------------ VobizXML replies
def hangup_xml(reason: str | None = None) -> str:
    """reason="busy" as the FIRST element rejects the call with a busy tone (never answered, not billed)."""
    attr = f' reason="{escape(reason)}"' if reason else ""
    return f"{XML_HEADER}<Response><Hangup{attr}/></Response>"


def speak_xml(text: str) -> str:
    """Vobiz's own Hindi voice reads `text`, then hangs up (the call is answered, so it is billed)."""
    return (f'{XML_HEADER}<Response><Speak voice="WOMAN" language="hi-IN">{escape(text[:3000])}</Speak>'
            f"<Hangup/></Response>")


def play_xml(url: str) -> str:
    return f"{XML_HEADER}<Response><Play>{escape(url)}</Play><Hangup/></Response>"


def call_urls(call_uuid: str) -> dict[str, str]:
    tok = url_token(call_uuid)
    return {
        "answer": _public(f"/vobiz/answer/{call_uuid}/{tok}"),
        "hangup": _public(f"/vobiz/hangup/{call_uuid}/{tok}"),
        "ring": _public(f"/vobiz/ring/{call_uuid}/{tok}"),
        "record_action": _public(f"/vobiz/record-start/{call_uuid}/{tok}"),
        "record_callback": _public(f"/vobiz/recording/{call_uuid}/{tok}"),
        "stream_status": _public(f"/vobiz/stream-status/{call_uuid}/{tok}"),
        "stream_ws": settings.public_ws_base.rstrip("/") + f"/vobiz/stream/{call_uuid}/{tok}",
    }


def answer_xml(call_uuid: str, max_call_sec: int) -> str:
    """Bidirectional mu-law stream + whole-session recording (Record must come before Stream)."""
    u = call_urls(call_uuid)
    record = ""
    if settings.vobiz_record_calls:
        record = (
            f'<Record recordSession="true" redirect="false" playBeep="false" '
            f'maxLength="{max(600, max_call_sec + 120)}" fileFormat="mp3" '
            f'action="{escape(u["record_action"])}" method="POST" '
            f'callbackUrl="{escape(u["record_callback"])}" callbackMethod="POST"/>'
        )
    stream = (
        f'<Stream bidirectional="true" audioTrack="inbound" keepCallAlive="true" '
        f'contentType="audio/x-mulaw;rate=8000" streamTimeout="{max_call_sec + 120}" '
        f'statusCallbackUrl="{escape(u["stream_status"])}" statusCallbackMethod="POST">'
        f'{escape(u["stream_ws"])}</Stream>'
    )
    return f"{XML_HEADER}<Response>{record}{stream}</Response>"


async def make_call(call_uuid: str, to_e164: str, max_call_sec: int, caller_name: str = "") -> str:
    """Place an outbound call. Returns Vobiz request_uuid (== CallUUID)."""
    if not settings.vobiz_ready:
        raise VobizError("Vobiz is not configured (VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN / VOBIZ_FROM_NUMBER).")
    u = call_urls(call_uuid)
    body = {
        "from": vobiz_number(settings.vobiz_from_number),
        "to": vobiz_number(to_e164),
        "answer_url": u["answer"],
        "answer_method": "POST",
        "hangup_url": u["hangup"],
        "hangup_method": "POST",
        "ring_url": u["ring"],
        "ring_method": "POST",
        "ring_timeout": 35,
        "time_limit": max_call_sec + 60,
    }
    if caller_name:
        body["caller_name"] = caller_name[:50]
    url = f"{settings.vobiz_base_url}/Account/{settings.vobiz_auth_id}/Call/"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=body, headers=_headers())
    if resp.status_code >= 300:
        detail = resp.text[:300]
        hint = {
            401: "auth failed — check VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN",
            402: "insufficient Vobiz balance",
            404: "wrong auth id or path",
            429: "Vobiz rate / concurrency limit hit",
        }.get(resp.status_code, "")
        raise VobizError(f"Vobiz make-call failed ({resp.status_code}) {hint}: {detail}", resp.status_code)
    data = resp.json()
    return data.get("request_uuid") or data.get("call_uuid") or ""


async def hangup_call(provider_call_id: str) -> bool:
    if not (settings.vobiz_ready and provider_call_id):
        return False
    url = f"{settings.vobiz_base_url}/Account/{settings.vobiz_auth_id}/Call/{provider_call_id}/"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(url, headers=_headers())
        return resp.status_code in (200, 202, 204, 404)
    except httpx.HTTPError as e:
        log.warning("hangup failed: %s", e)
        return False


MAX_RECORDING_BYTES = 200 * 1024 * 1024


def _is_vobiz_host(url: str) -> bool:
    host = (httpx.URL(url).host or "").lower()
    return url.startswith("https://") and (host == "vobiz.ai" or host.endswith(".vobiz.ai"))


async def download_recording(record_url: str) -> bytes | None:
    """Download a Vobiz recording. Credentials go ONLY to Vobiz hosts (never to a redirect target
    such as a storage bucket), and the size is capped."""
    if not _is_vobiz_host(record_url):
        log.warning("refusing recording download from non-Vobiz URL %s", record_url[:80])
        return None
    auth = {k: v for k, v in _headers().items() if k != "Content-Type"}
    url = record_url
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            for _ in range(4):
                headers = auth if _is_vobiz_host(url) else {}
                async with client.stream("GET", url, headers=headers) as resp:
                    if resp.is_redirect:
                        url = str(resp.url.join(resp.headers.get("location", "")))
                        continue
                    if resp.status_code != 200:
                        log.warning("recording download %s -> %s", record_url[:80], resp.status_code)
                        return None
                    data = bytearray()
                    async for chunk in resp.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_RECORDING_BYTES:
                            log.warning("recording larger than %d bytes, skipped", MAX_RECORDING_BYTES)
                            return None
                    return bytes(data) or None
    except httpx.HTTPError as e:
        log.warning("recording download failed: %s", e)
    return None


async def check_auth() -> tuple[bool, str]:
    if not (settings.vobiz_auth_id and settings.vobiz_auth_token):
        return False, "VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN not set"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.vobiz_base_url}/auth/me", headers=_headers())
        if resp.status_code == 200:
            return True, "credentials OK" + ("" if settings.vobiz_from_number else " (VOBIZ_FROM_NUMBER missing)")
        return False, f"HTTP {resp.status_code}: {resp.text[:150]}"
    except httpx.HTTPError as e:
        return False, str(e)


# ------------------------------------------------------------------ account REST (inbound setup, call records)
def _client(timeout: float) -> httpx.AsyncClient:
    """One place to build API clients (tests swap in a fake Vobiz transport here)."""
    return httpx.AsyncClient(timeout=timeout)


def _quote_number(e164: str) -> str:
    return urllib.parse.quote(e164, safe="")  # '+' must travel as %2B (Vobiz docs)


async def api(method: str, path: str, *, params: dict | None = None, body: dict | None = None,
              timeout: float = API_TIMEOUT) -> Any:
    """Call /Account/{auth_id}{path}. Raises VobizError (with the HTTP status) on any failure."""
    if not (settings.vobiz_auth_id and settings.vobiz_auth_token):
        raise VobizError("Vobiz is not configured (VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN).")
    url = f"{settings.vobiz_base_url}/Account/{settings.vobiz_auth_id}{path}"
    try:
        async with _client(timeout) as client:
            resp = await client.request(method, url, params=params, json=body, headers=_headers())
    except httpx.HTTPError as e:
        raise VobizError(f"Vobiz could not be reached ({type(e).__name__})") from e
    if resp.status_code >= 300:
        hint = {401: "check VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN", 404: "not found",
                429: "Vobiz rate limit, try again in a minute"}.get(resp.status_code, "")
        raise VobizError(f"Vobiz {method} {path.split('?')[0]} failed ({resp.status_code}"
                         f"{', ' + hint if hint else ''}): {resp.text[:200]}", resp.status_code)
    if not resp.content:
        return {}
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}  # every endpoint used here answers with an object


async def list_applications(max_pages: int = 20) -> list[dict]:
    """Every application on the account (pages of 100)."""
    apps: list[dict] = []
    for page in range(max_pages):
        data = await api("GET", "/Application/", params={"limit": 100, "offset": page * 100})
        objects = [o for o in (data.get("objects") or []) if isinstance(o, dict)]
        apps.extend(objects)
        total = (data.get("meta") or {}).get("total_count")
        if not objects or len(objects) < 100 or (isinstance(total, int) and len(apps) >= total):
            break
    return apps


async def create_application(fields: dict) -> str:
    data = await api("POST", "/Application/", body=fields)
    app_id = str(data.get("app_id") or "")
    if not app_id:
        raise VobizError("Vobiz created the application but returned no app_id")
    return app_id


async def update_application(app_id: str, fields: dict) -> None:
    await api("POST", f"/Application/{urllib.parse.quote(app_id, safe='')}/", body=fields)


async def find_number(e164: str) -> dict | None:
    """Our DID as Vobiz sees it (application_id, trunk_group_id, voice_enabled, status...), or None."""
    data = await api("GET", "/numbers", params={"search": e164, "page": 1, "per_page": 25})
    items = data.get("items") or data.get("objects") or []
    digits = e164.lstrip("+")
    for item in items:
        if isinstance(item, dict) and str(item.get("e164") or item.get("number") or "").lstrip("+") == digits:
            return item
    return None


async def attach_number(e164: str, app_id: str) -> None:
    """Route inbound calls to this number into `app_id` (replaces any earlier binding)."""
    await api("POST", f"/numbers/{_quote_number(e164)}/application", body={"application_id": app_id})


async def detach_number(e164: str) -> None:
    await api("DELETE", f"/numbers/{_quote_number(e164)}/application")


async def account_concurrency() -> dict:
    return await api("GET", "/concurrency")


async def inbound_call_records(to_e164: str, start_date: str, end_date: str, max_pages: int = 20) -> list[dict]:
    """Inbound call records (CDRs) to our DID between two YYYY-MM-DD dates, all pages."""
    records: list[dict] = []
    for page in range(1, max_pages + 1):
        data = await api("GET", "/cdr", params={"call_direction": "inbound", "to_number": to_e164,
                                                "start_date": start_date, "end_date": end_date,
                                                "page": page, "per_page": 100})
        rows = [r for r in (data.get("data") or []) if isinstance(r, dict)]
        records.extend(rows)
        if not rows or not (data.get("pagination") or {}).get("has_next"):
            break
        await asyncio.sleep(0.2)  # stay well inside the account's API rate limit
    return records


# Vobiz HangupCauseCode -> our call status / end reason
HANGUP_CODE_MAP = {
    "3000": ("no_answer", "no_answer"),
    "6010": ("no_answer", "no_answer"),
    "3010": ("busy", "busy"),
    "3020": ("busy", "busy"),
    "2000": ("failed", "error"),
    "1010": ("failed", "error"),
    "2070": ("failed", "error"),
}
