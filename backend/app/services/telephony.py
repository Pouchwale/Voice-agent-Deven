"""The active phone provider, chosen by TELEPHONY_PROVIDER in backend/.env ("plivo" or "vobiz").

Plivo and Vobiz read the same XML and stream call audio with the same WebSocket protocol, so the
webhooks, the media stream and the conversation engine are shared. Only the account REST API
(placing / hanging up calls, applications, numbers, call records, recordings) differs, and each
provider module exposes the same functions for it:

    make_call, hangup_call, check_auth, list_applications, create_application, update_application,
    find_number, attach_number, detach_number, account_concurrency, inbound_call_records,
    call_urls, answer_xml, hangup_xml, speak_xml, play_xml, url_token, verify_token,
    inbound_token, verify_inbound_token, inbound_urls, inbound_audio_url, note_inbound_rejection,
    inbound_rejections, INBOUND_APP_NAME, HANGUP_CODE_MAP

Use `telephony.<name>` everywhere outside the provider modules; it resolves to the active provider
at call time (tests can still patch `vobiz.<name>` / `plivo.<name>` directly).
"""
from __future__ import annotations

from types import ModuleType
from typing import Any

from ..config import get_settings
from . import plivo, vobiz

settings = get_settings()

# `except telephony.ProviderError` catches a failure from either provider.
ProviderError = (vobiz.VobizError, plivo.PlivoError)


def active() -> ModuleType:
    return plivo if settings.provider == "plivo" else vobiz


def name() -> str:
    return settings.provider_name


def is_retryable(err: BaseException) -> bool:
    """Rate limit / provider outage: not the lead's fault, so the attempt should not count."""
    return isinstance(err, ProviderError) and bool(err.retryable)


async def download_recording(url: str) -> bytes | None:
    """A recording belongs to the provider that made the call, so pick by its host, not by the switch
    (calls made before a switch keep their recordings)."""
    if plivo.is_recording_url(url):
        return await plivo.download_recording(url)
    return await vobiz.download_recording(url)


def __getattr__(attr: str) -> Any:  # PEP 562: telephony.make_call -> <active provider>.make_call
    if attr.startswith("__"):
        raise AttributeError(attr)
    return getattr(active(), attr)
