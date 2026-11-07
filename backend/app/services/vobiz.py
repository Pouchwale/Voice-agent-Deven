"""Vobiz telephony client (Plivo-compatible REST + VobizXML).

Docs: https://www.vobiz.ai/docs  (REST base https://api.vobiz.ai/api/v1,
auth headers X-Auth-ID / X-Auth-Token, PascalCase paths with trailing slash).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from xml.sax.saxutils import escape

import httpx

from ..config import get_settings

log = logging.getLogger("vobiz")
settings = get_settings()


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
    return f'<?xml version="1.0" encoding="UTF-8"?><Response>{record}{stream}</Response>'


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
