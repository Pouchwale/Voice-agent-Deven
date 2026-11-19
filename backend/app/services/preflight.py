"""Checks made just before a phone call is dialed: is there a DEFINITE reason the customer would pick up a
call that cannot work?

Two such reasons were met live on one afternoon. The voice provider's account ran out of credit: the phone
rang, the customer answered, and the call dropped within a second (the provider had refused the greeting
eight seconds earlier). And the tunnel the phone provider reaches this backend through was not running: the
phone rang and nothing ever connected. Both times the dashboard only showed a call that ended by itself.

Rules these checks follow:
  * Only definite evidence refuses a call: the voice provider answering "402 Payment Required", or the
    tunnel service itself saying the link is offline. Anything else (a timeout, an answer that is not
    recognised) lets the call through, exactly as before these checks existed.
  * A healthy call pays nothing for the voice check. It only runs after something went wrong (a call that
    ended in an error, a greeting or clip the voice provider refused), and one good answer switches it off.
  * A refusal is never remembered. Every refused attempt asks again, so the first call after the credit is
    topped up or the tunnel is started goes through.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urlsplit

import httpx

from ..config import get_settings
from . import sarvam

log = logging.getLogger("preflight")
settings = get_settings()

VOICE_TIMEOUT = 4.0   # s the voice provider gets to answer the check
LINK_TIMEOUT = 4.0    # s for the request through the public link
LINK_TRUST_SEC = 60.0  # a link that answered (or could not be judged) is not asked again this soon
NO_CREDIT_STATUS = 402              # the voice provider's answer for an account without credit
TUNNEL_OFFLINE = "ERR_NGROK_3200"   # the tunnel service's own answer for a link whose tunnel is not running

# Shown on the dashboard as the reason a call was not placed (a toast: short enough to read in a few seconds).
REFUSED = "Cannot place call:"  # (same opening as the older "Cannot place call: OPENAI_API_KEY is not set")
NO_CREDIT = "Sarvam (the voice service) has no credits left. Add credits in the Sarvam dashboard, then call again."
NO_TUNNEL = "The ngrok tunnel is not running. Run start-all.bat, then call again."
# Written on a call that ended in an error while the voice provider had no credit (shown on the call's page).
NO_CREDIT_ON_CALL = ("The voice service (Sarvam) had no credits left, so the agent could not speak. "
                     "Add credits in the Sarvam dashboard.")

_CREDIT_REFUSAL = re.compile(r"\bSarvam TTS 402\b|insufficient_quota|quota_exceeded|credits exhausted|"
                             r"no credits available", re.I)

_voice_suspect: str | None = None  # why the next phone call asks the voice provider before it dials
_link_trusted_until = 0.0          # (monotonic) the public link is not asked again before this


def reset() -> None:
    """Forget everything learnt so far (tests)."""
    global _voice_suspect, _link_trusted_until
    _voice_suspect = None
    _link_trusted_until = 0.0


# ---------------------------------------------------------------------- voice provider credit
def is_credit_refusal(err: BaseException | str) -> bool:
    """The voice provider refused for lack of credit. Stricter than a search for "402": that number also
    turns up inside request ids of unrelated errors."""
    return bool(_CREDIT_REFUSAL.search(str(err)))


def voice_suspect() -> str | None:
    return _voice_suspect


def suspect_voice(why: str) -> None:
    """Something just went wrong that a voice provider without credit would explain: the next phone call
    asks the provider before it dials."""
    global _voice_suspect
    if _voice_suspect is None:
        log.warning("the voice provider is checked before the next phone call: %s", why)
    _voice_suspect = why


def note_voice_error(err: BaseException | str) -> None:
    """A synthesis failed somewhere (greeting, cached clip). Only a refusal for lack of credit matters here."""
    if is_credit_refusal(err):
        suspect_voice("it refused a request for lack of credit")


async def _voice_status() -> int:
    """HTTP status of one very small synthesis request, sent straight to the provider: never answered from
    the clip cache, which would report a voice that worked an hour ago. Costs two characters when it works
    and nothing when the account is empty."""
    voice = "priya"  # the same request the "Test providers" button sends (sarvam.check_tts), with a shorter text
    model = sarvam.model_for(voice)
    body = {"text": "जी", "target_language_code": "hi-IN", "speaker": voice, "model": model, "pace": 1.0,
            "speech_sample_rate": 8000}
    if model != sarvam.V4_MODEL:
        body["temperature"] = 0.6
    resp = await sarvam.http().post("/text-to-speech", json=body, headers=sarvam._headers(), timeout=VOICE_TIMEOUT)
    return resp.status_code


async def voice_has_no_credit() -> bool | None:
    """True: the voice provider refuses for lack of credit right now. False: it speaks. None: no clear
    answer (unreachable, slow, another error), which is never a reason to hold a call back."""
    global _voice_suspect
    if not settings.sarvam_ready:
        return None  # a missing key is reported by the checks that existed before these
    try:
        status = await asyncio.wait_for(_voice_status(), VOICE_TIMEOUT + 1.0)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("voice provider check gave no answer (%s): calls are not held back for that", type(e).__name__)
        return None
    if status == NO_CREDIT_STATUS:
        suspect_voice("it answered that the account has no credit")  # whoever asked: the next call asks again
        return True
    if status < 300:
        if _voice_suspect is not None:
            log.info("the voice provider answers again: phone calls are placed without the extra check")
        _voice_suspect = None
        return False
    log.warning("voice provider check answered HTTP %s: calls are not held back for that", status)
    return None


async def _voice_blocker() -> str | None:
    if _voice_suspect is None:
        return None  # nothing has gone wrong: a healthy call is not delayed by a question to the provider
    if await voice_has_no_credit():
        log.error("phone call refused: the voice provider has no credit (checked just now)")
        return NO_CREDIT
    return None


# ---------------------------------------------------------------------- public link (tunnel)
def checkable_link() -> str | None:
    """The public link when it is an ngrok address: the one tunnel whose "offline" answer is known here.
    Any other address (a server's own domain, another tunnel) is never asked and never refused."""
    base = (settings.public_base_url or "").strip().rstrip("/")
    try:
        host = (urlsplit(base).hostname or "").lower()
    except ValueError:
        return None
    return base if base.startswith("https://") and "ngrok" in host else None


def _link_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=LINK_TIMEOUT, follow_redirects=False)


async def _link_answer(url: str) -> tuple[int, str]:
    """(HTTP status, the tunnel service's error code or "") of one request through the public link."""
    async with _link_client() as client:
        resp = await client.get(url, headers={"ngrok-skip-browser-warning": "1"})
    return resp.status_code, (resp.headers.get("ngrok-error-code") or "").strip()


async def _link_blocker() -> str | None:
    global _link_trusted_until
    base = checkable_link()
    if base is None or time.monotonic() < _link_trusted_until:
        return None
    try:
        status, code = await asyncio.wait_for(_link_answer(f"{base}/{settings.provider}/ping"), LINK_TIMEOUT + 1.0)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("public link check gave no answer (%s): calls are not held back for that", type(e).__name__)
        _link_trusted_until = time.monotonic() + LINK_TRUST_SEC  # a slow check must not slow every call down
        return None
    if code.upper() == TUNNEL_OFFLINE:
        log.error("phone call refused: the tunnel for %s is not running (%s)", base, code)
        return NO_TUNNEL  # never remembered: the next attempt asks again
    if status != 200:
        log.warning("public link check answered HTTP %s %s: calls are not held back for that", status, code)
    _link_trusted_until = time.monotonic() + LINK_TRUST_SEC
    return None


# ---------------------------------------------------------------------- the check itself
async def call_blockers() -> list[str]:
    """Definite reasons a phone call dialed now would be picked up as a silent line, in the words shown on
    the dashboard. Empty when there is none, and also when a check could not tell. Never raises."""
    try:
        found = await asyncio.gather(_voice_blocker(), _link_blocker(), return_exceptions=True)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        log.exception("pre-dial checks failed: the call goes ahead")
        return []
    for item in found:
        if isinstance(item, asyncio.CancelledError):
            raise item
        if isinstance(item, BaseException):
            log.error("a pre-dial check failed (%r): the call goes ahead", item)
    return [item for item in found if isinstance(item, str) and item]
