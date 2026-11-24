"""Plivo telephony client (https://www.plivo.com/docs/voice).

Plivo reads the same XML and streams call audio with the same WebSocket protocol as Vobiz, so the
XML builders and the webhook / media-stream router are shared (services/vobiz.py, routers/telephony.py).
What differs is the account REST API, which lives here, with the same function names as vobiz.py:

  REST base  https://api.plivo.com/v1/Account/{auth_id}/...   HTTP Basic auth (auth id : auth token)
  numbers    E.164 digits without '+' (e.g. 919876543210)
  lists      paged with limit (max 20) + offset; response {"meta": {...}, "objects": [...]}

Everything returned to the rest of the app is normalised to the shapes vobiz.py returns.
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
from .phone import caller_e164
# Plivo reads the same XML as Vobiz: share the builders (hangup_xml / play_xml are re-exported as-is).
from .vobiz import MAX_RECORDING_BYTES, XML_HEADER, build_answer_xml, hangup_xml, play_xml  # noqa: F401

log = logging.getLogger("plivo")
settings = get_settings()

INBOUND_APP_NAME = "GP3-Voice-Agent-Inbound"
API_TIMEOUT = 8.0   # setup / call-record queries: never keep the dashboard waiting longer
PAGE = 20           # Plivo's maximum page size for list endpoints
PREFIX = "/plivo"   # our webhook / stream URL prefix for Plivo calls (routers/telephony.py)


class PlivoError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def retryable(self) -> bool:
        """Rate limit / Plivo outage: not the lead's fault, so the attempt should not count."""
        return self.status is not None and (self.status == 429 or self.status >= 500)


def _auth() -> tuple[str, str]:
    return settings.plivo_auth_id, settings.plivo_auth_token


def plivo_number(e164: str) -> str:
    """Plivo takes country code + number without '+', e.g. 919876543210. Accepts any way a number is
    typed in .env ("80 3177 6200", "080-3177-6200", "+91 80 3177 6200" -> "918031776200")."""
    raw = (e164 or "").strip()
    return (caller_e164(raw) or raw).lstrip("+")


def _account_url(path: str) -> str:
    return f"{settings.plivo_base_url.rstrip('/')}/Account/{settings.plivo_auth_id}{path}"


# ------------------------------------------------------------------ secrets in our URLs
def url_token(call_uuid: str) -> str:
    """Per-call secret embedded in webhook / stream URLs (Plivo can't send custom auth)."""
    key = (settings.plivo_auth_token or settings.openai_api_key or "dev-secret").encode()
    return hmac.new(key, call_uuid.encode(), hashlib.sha256).hexdigest()[:32]


def verify_token(call_uuid: str, token: str) -> bool:
    return hmac.compare_digest(url_token(call_uuid), token or "")


def _public(path: str) -> str:
    return settings.public_base_url.rstrip("/") + PREFIX + path


def call_urls(call_uuid: str) -> dict[str, str]:
    tok = url_token(call_uuid)
    return {
        "answer": _public(f"/answer/{call_uuid}/{tok}"),
        "hangup": _public(f"/hangup/{call_uuid}/{tok}"),
        "ring": _public(f"/ring/{call_uuid}/{tok}"),
        "record_action": _public(f"/record-start/{call_uuid}/{tok}"),
        "record_callback": _public(f"/recording/{call_uuid}/{tok}"),
        "stream_status": _public(f"/stream-status/{call_uuid}/{tok}"),
        "stream_ws": settings.public_ws_base.rstrip("/") + f"{PREFIX}/stream/{call_uuid}/{tok}",
    }


def answer_xml(call_uuid: str, max_call_sec: int) -> str:
    """Bidirectional mu-law stream + whole-session recording (Record must come before Stream)."""
    return build_answer_xml(call_urls(call_uuid), max_call_sec, settings.plivo_record_calls, _stream_attrs())


def _stream_attrs() -> str:
    """Plivo's noise cancellation on the customer's audio (cuts fans, traffic, shop noise before speech
    recognition hears it). Level 60-100; Plivo's default is 85."""
    if not settings.plivo_noise_cancellation:
        return ""
    level = min(100, max(60, int(settings.plivo_noise_cancellation_level or 85)))
    return f' noiseCancellation="true" noiseCancellationLevel="{level}"'


def speak_xml(text: str) -> str:
    """Plivo's own Hindi voice reads `text`, then hangs up (the call is answered, so it is billed).
    Plivo's basic WOMAN voice has no Hindi: Polly.Aditi is its Hindi (female) voice."""
    return (f'{XML_HEADER}<Response><Speak voice="Polly.Aditi" language="hi-IN">{escape(text[:3000])}</Speak>'
            f"<Hangup/></Response>")


# ------------------------------------------------------------------ inbound webhooks
def inbound_token() -> str:
    """Secret in the static inbound URLs. Changing INBOUND_TOKEN_SALT revokes a leaked URL."""
    return url_token(settings.inbound_token_salt)


def verify_inbound_token(token: str) -> bool:
    # Without an auth token the secret would be derived from a guessable fallback: refuse everything.
    return bool(settings.plivo_auth_token) and hmac.compare_digest(inbound_token(), token or "")


def inbound_urls() -> dict[str, str]:
    tok = inbound_token()
    return {"answer": _public(f"/inbound/{tok}/answer"), "hangup": _public(f"/inbound/{tok}/hangup")}


def inbound_audio_url(key: str) -> str:
    return _public(f"/inbound/{inbound_token()}/audio/{key}.wav")


inbound_rejections: dict[str, Any] = {"count": 0, "last_at": None}


def note_inbound_rejection() -> None:
    inbound_rejections["count"] += 1
    inbound_rejections["last_at"] = datetime.now(timezone.utc)


# ------------------------------------------------------------------ REST
def _client(timeout: float) -> httpx.AsyncClient:
    """One place to build API clients (tests swap in a fake Plivo transport here)."""
    return httpx.AsyncClient(timeout=timeout)


_HINTS = {
    400: "Plivo rejected the request",
    401: "auth failed — check PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN",
    402: "insufficient Plivo credit",
    403: "not allowed for this account (trial accounts can only call verified numbers)",
    404: "not found",
    429: "Plivo rate limit, try again in a minute",
}


async def api(method: str, path: str, *, params: dict | None = None, body: dict | None = None,
              timeout: float = API_TIMEOUT) -> dict:
    """Call /Account/{auth_id}{path}. Raises PlivoError (with the HTTP status) on any failure."""
    if not (settings.plivo_auth_id and settings.plivo_auth_token):
        raise PlivoError("Plivo is not configured (PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN).")
    try:
        async with _client(timeout) as client:
            resp = await client.request(method, _account_url(path), params=params, json=body, auth=_auth())
    except httpx.HTTPError as e:
        raise PlivoError(f"Plivo could not be reached ({type(e).__name__})") from e
    if resp.status_code >= 300:
        hint = _HINTS.get(resp.status_code, "")
        raise PlivoError(f"Plivo {method} {path.split('?')[0]} failed ({resp.status_code}"
                         f"{', ' + hint if hint else ''}): {resp.text[:200]}", resp.status_code)
    if not resp.content:
        return {}
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


async def make_call(call_uuid: str, to_e164: str, max_call_sec: int, caller_name: str = "") -> str:
    """Place an outbound call. Returns Plivo's request_uuid (the call's CallUUID arrives on answer)."""
    if not settings.plivo_ready:
        raise PlivoError("Plivo is not configured (PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN / PLIVO_FROM_NUMBER).")
    u = call_urls(call_uuid)
    body: dict[str, Any] = {
        "from": plivo_number(settings.plivo_from_number),
        "to": plivo_number(to_e164),
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
    try:
        data = await api("POST", "/Call/", body=body, timeout=15)
    except PlivoError as e:
        raise PlivoError(f"Plivo make-call failed: {e}", e.status) from e
    uid = data.get("request_uuid") or data.get("call_uuid") or ""
    if isinstance(uid, list):  # bulk-call shape: one destination here
        uid = uid[0] if uid else ""
    return str(uid)


async def hangup_call(provider_call_id: str) -> bool:
    """Hang up a live call; a call still being dialled (or ringing) is cancelled as a request."""
    if not (settings.plivo_ready and provider_call_id):
        return False
    pid = urllib.parse.quote(provider_call_id, safe="")
    for path in (f"/Call/{pid}/", f"/Request/{pid}/"):
        try:
            await api("DELETE", path, timeout=10)
            return True
        except PlivoError as e:
            if e.status != 404:
                log.warning("hangup failed: %s", e)
                return False
    return True  # 404 on both: the call is already gone


async def ended_call(provider_call_id: str) -> dict | None:
    """Plivo's record of a call that is over, shaped like the hangup webhook's form; None while the call is still
    being dialled, ringing or live (Plivo has no record until it ends) or when Plivo can't be asked.

    The watchdog's fallback for a hangup webhook that never arrives: on 2026-11-20 the tunnel passed none of
    Plivo's callbacks for ~90 s, and three campaign calls Plivo had already ended (ring timeout, an answer it
    could not connect) kept their campaign's slots busy until someone hung them up by hand."""
    if not (settings.plivo_ready and provider_call_id):
        return None
    pid = urllib.parse.quote(provider_call_id, safe="")
    try:
        d = await api("GET", f"/Call/{pid}/", timeout=10)
    except PlivoError:
        return None
    if not d.get("end_time"):
        return None
    error = str(d.get("hangup_source") or "").lower() == "error"
    return {"HangupCauseCode": str(d.get("hangup_cause_code") or ""), "HangupCause": str(d.get("hangup_cause_name") or ""),
            "CallStatus": "failed" if error else "completed", "Answered": bool(d.get("answer_time"))}


MAX_REDIRECTS = 4


def _host(url: str) -> str:
    try:
        return (httpx.URL(url).host or "").lower()
    except Exception:  # noqa: BLE001
        return ""


def is_plivo_host(url: str) -> bool:
    host = _host(url)
    return url.startswith("https://") and (host == "plivo.com" or host.endswith(".plivo.com"))


def _is_recording_store(url: str) -> bool:
    """Plivo also hands out recording links on its Amazon S3 storage (public, no credentials)."""
    host = _host(url)
    return url.startswith("https://") and (host == "amazonaws.com" or host.endswith(".amazonaws.com"))


def is_recording_url(url: str) -> bool:
    return is_plivo_host(url) or _is_recording_store(url)


async def download_recording(record_url: str) -> bytes | None:
    """Download a Plivo recording. Credentials go ONLY to Plivo hosts (never to a redirect target such
    as a storage bucket), other hosts are refused, and the size is capped."""
    if not is_recording_url(record_url):
        log.warning("refusing recording download from non-Plivo URL %s", record_url[:80])
        return None
    url = record_url
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
            for _ in range(MAX_REDIRECTS):
                auth = _auth() if is_plivo_host(url) and settings.plivo_auth_id else None
                async with client.stream("GET", url, auth=auth) as resp:
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
    if not (settings.plivo_auth_id and settings.plivo_auth_token):
        return False, "PLIVO_AUTH_ID / PLIVO_AUTH_TOKEN not set"
    try:
        await api("GET", "/", timeout=10)
    except PlivoError as e:
        return False, str(e)[:200]
    if not settings.plivo_from_number:
        return True, "credentials OK (PLIVO_FROM_NUMBER missing)"
    try:
        num = await find_number(settings.plivo_from_number)
    except PlivoError as e:
        return True, f"credentials OK, but the number check failed: {str(e)[:120]}"
    if num is None:
        return False, f"credentials OK, but {settings.plivo_from_number} is not a number on this Plivo account"
    return True, f"credentials OK, {settings.plivo_from_number} found on the account"


# ------------------------------------------------------------------ account REST (inbound setup, call records)
def _id_from_uri(value: Any) -> str:
    """Plivo links objects by URI (/v1/Account/MA.../Application/12345/): keep the trailing id."""
    s = str(value or "").strip().rstrip("/")
    return s.rsplit("/", 1)[-1] if s else ""


async def _list(path: str, params: dict | None = None, max_pages: int = 50) -> list[dict]:
    out: list[dict] = []
    for page in range(max_pages):
        data = await api("GET", path, params={**(params or {}), "limit": PAGE, "offset": page * PAGE})
        objects = [o for o in (data.get("objects") or []) if isinstance(o, dict)]
        out.extend(objects)
        meta = data.get("meta") or {}
        total = meta.get("total_count")
        if not objects or not meta.get("next") or (isinstance(total, int) and len(out) >= total):
            break
        await asyncio.sleep(0.1)  # stay well inside the account's API rate limit
    return out


def _app(o: dict) -> dict:
    return {**o, "app_id": str(o.get("app_id") or ""), "app_name": o.get("app_name"),
            "answer_url": o.get("answer_url"), "hangup_url": o.get("hangup_url")}


async def list_applications(max_pages: int = 50) -> list[dict]:
    """Every application on the account."""
    return [_app(o) for o in await _list("/Application/", max_pages=max_pages)]


_APP_FIELDS = ("app_name", "answer_url", "answer_method", "hangup_url", "hangup_method", "fallback_answer_url",
               "fallback_method", "message_url", "message_method", "default_number_app", "default_endpoint_app",
               "subaccount", "log_incoming_messages", "public_uri")


async def create_application(fields: dict) -> str:
    body = {k: v for k, v in fields.items() if k in _APP_FIELDS}  # drops Vobiz-only fields
    data = await api("POST", "/Application/", body=body)
    app_id = str(data.get("app_id") or "")
    if not app_id:
        raise PlivoError("Plivo created the application but returned no app_id")
    return app_id


async def update_application(app_id: str, fields: dict) -> None:
    body = {k: v for k, v in fields.items() if k in _APP_FIELDS and k != "app_name"}
    await api("POST", f"/Application/{urllib.parse.quote(app_id, safe='')}/", body=body)


async def find_number(e164: str) -> dict | None:
    """Our number as Plivo sees it, normalised to the fields the inbound setup reads
    (application_id, voice_enabled, status, ...), or None if it isn't on this account."""
    try:
        o = await api("GET", f"/Number/{urllib.parse.quote(plivo_number(e164), safe='')}/")
    except PlivoError as e:
        if e.status == 404:
            return None
        raise
    if not o:
        return None
    app = o.get("application")
    app_id = _id_from_uri(app) if isinstance(app, str) else str((app or {}).get("app_id") or "") \
        if isinstance(app, dict) else ""
    voice = o.get("voice_enabled")
    return {
        **o,
        "e164": "+" + plivo_number(str(o.get("number") or e164)),
        "application_id": app_id or None,
        "voice_enabled": voice if isinstance(voice, bool) else None,
        "status": "active" if o.get("active", True) not in (False, "false") else "inactive",
        "trunk_group_id": None,
    }


async def attach_number(e164: str, app_id: str) -> None:
    """Route inbound calls to this number into `app_id` (replaces any earlier binding)."""
    await api("POST", f"/Number/{urllib.parse.quote(plivo_number(e164), safe='')}/", body={"app_id": app_id})


async def detach_number(e164: str) -> None:
    """Unlink the number from any application (Plivo: app_id set to an empty value)."""
    await api("POST", f"/Number/{urllib.parse.quote(plivo_number(e164), safe='')}/", body={"app_id": ""})


async def account_concurrency() -> dict:
    """Plivo has no concurrency endpoint: the limit is unknown here (the setup check then skips its warning)."""
    return {}


def _plivo_time(day: str, end: bool) -> str:
    return f"{day} {'23:59' if end else '00:00'}"


async def inbound_call_records(to_e164: str, start_date: str, end_date: str, max_pages: int = 50) -> list[dict]:
    """Inbound call records (CDRs) to our number between two YYYY-MM-DD dates, normalised to the field
    names the back-fill reads (uuid, call_direction, destination_number, start_time, end_time,
    caller_id_number, caller_id_name)."""
    rows = await _list("/Call/", {"call_direction": "inbound", "to_number": plivo_number(to_e164),
                                  "end_time__gte": _plivo_time(start_date, False),
                                  "end_time__lte": _plivo_time(end_date, True)}, max_pages=max_pages)
    out = []
    for r in rows:
        out.append({
            **r,
            "uuid": r.get("call_uuid"),
            "call_direction": r.get("call_direction") or "inbound",
            "destination_number": r.get("to_number"),
            "start_time": r.get("initiation_time") or r.get("answer_time") or r.get("end_time"),
            "end_time": r.get("end_time"),
            "caller_id_number": r.get("from_number"),
            "caller_id_name": r.get("caller_name"),
        })
    return out


# Plivo HangupCauseCode -> our call status / end reason, for calls that ended before they were answered
# (https://www.plivo.com/docs/voice/troubleshooting/hangup-causes). Plivo's hangup webhook may carry only
# CallStatus / HangupCause; the router falls back to those when no code is present.
_NO_ANSWER = ("no_answer", "no_answer")
_BUSY = ("busy", "busy")
_FAILED = ("failed", "error")
HANGUP_CODE_MAP: dict[str, tuple[str, str]] = {
    "6010": _NO_ANSWER,   # ring timeout
    "3000": _NO_ANSWER,   # destination unavailable / unreachable (switched off): try again later
    "1000": ("canceled", "no_answer"),  # cancelled through the API before it was answered
    "3010": _BUSY, "3100": _BUSY,       # busy line / all endpoints busy
    "3020": _BUSY, "3110": _BUSY,       # call declined by the customer
    **{code: _FAILED for code in (
        "2000", "2010", "3050", "3120",                  # invalid / out-of-service / unknown number
        "2030", "2040", "2050", "2060", "2070",          # barred / loop / India media-anchoring rules
        "3030", "3040", "3130", "3140",                  # caller ID not allowed / blocked / spam / DNO
        "1010", "1020", "5030",                          # no credit / dial limit / concurrency limit
        "3070", "3080", "3090", "5000", "5010", "5020",  # carrier / system errors
    )},
}
