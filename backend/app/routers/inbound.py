"""Inbound calling: Vobiz setup status + one-click connect (GET/POST/DELETE /api/inbound/setup).

Connect = our Vobiz application "GP3-Voice-Agent-Inbound" (answer + hangup URLs on this backend) with our
DID attached to it. Vobiz is queried with short timeouts and the result is cached for a minute, so the
dashboard never hammers the Vobiz API (it shares the account's rate limit with live call hang-ups).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from ..config import get_settings
from ..db import SessionLocal, utcnow
from ..models import Call
from ..schemas import iso
from ..services import inbound_sync, vobiz
from ..services.agent_config import load_agent_settings
from ..services.call_manager import manager
from ..services.dialer import GLOBAL_MAX_CONCURRENT
from ..services.phone import caller_e164
from ..services.repo import run_db

log = logging.getLogger("inbound.setup")
settings = get_settings()
router = APIRouter(prefix="/api/inbound", tags=["inbound"])

CHECK_TTL = 60.0  # s a Vobiz check is reused by GET ?check=true (POST / DELETE clear it)
_check: dict[str, Any] = {"at": 0.0, "number": None, "data": None}
_lock: tuple[asyncio.AbstractEventLoop, asyncio.Lock] | None = None


def _setup_lock() -> asyncio.Lock:
    """One connect / disconnect at a time (a double click must not create two applications)."""
    global _lock
    loop = asyncio.get_running_loop()
    if _lock is None or _lock[0] is not loop:
        _lock = (loop, asyncio.Lock())
    return _lock[1]


def _our_number() -> str | None:
    return caller_e164(settings.vobiz_from_number)


def _clear_check() -> None:
    _check.update(at=0.0, number=None, data=None)


def _is_ours(app: dict, urls: dict[str, str]) -> bool:
    return app.get("app_name") == vobiz.INBOUND_APP_NAME or app.get("answer_url") == urls["answer"]


def _our_app(apps: list[dict]) -> dict | None:
    """Our application: by name (or created by hand with our answer URL); the one with current URLs first."""
    urls = vobiz.inbound_urls()
    mine = [a for a in apps if _is_ours(a, urls) and a.get("app_id")]
    mine.sort(key=lambda a: (a.get("answer_url") != urls["answer"], a.get("app_name") != vobiz.INBOUND_APP_NAME))
    return mine[0] if mine else None


def _app_name(apps: list[dict] | None, app_id: str | None) -> str | None:
    for a in apps or []:
        if app_id and str(a.get("app_id") or "") == app_id:
            return a.get("app_name") or app_id
    return None


async def _vobiz_check(number: str) -> dict:
    """Applications, our DID and the account's concurrency limit, queried in parallel."""
    apps, num, conc = await asyncio.gather(vobiz.list_applications(), vobiz.find_number(number),
                                           vobiz.account_concurrency(), return_exceptions=True)
    errors = [str(r) for r in (apps, num, conc) if isinstance(r, Exception)]
    if errors:
        log.warning("inbound setup check: %s", "; ".join(errors))
    return {"apps": None if isinstance(apps, Exception) else apps,
            "number_found": None if isinstance(num, Exception) else num is not None,
            "number": num if isinstance(num, dict) else None,
            "concurrency": conc if isinstance(conc, dict) else None,
            "errors": errors}


def _local_facts() -> dict:
    with SessionLocal() as db:
        last = db.scalar(select(func.max(Call.created_at)).where(Call.direction == "inbound",
                                                                 Call.channel == "phone"))
    return {"last_inbound_at": last, "meta": inbound_sync.load_meta()}


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


async def _status(check: bool) -> dict:
    cfg = load_agent_settings()
    number = _our_number()
    urls = vobiz.inbound_urls()
    blockers = manager.inbound_blockers()
    ai_missing = [b for b in blockers if "API_KEY" in b]
    public_ok = settings.public_base_url.startswith("https://")
    vobiz_ready = bool(settings.vobiz_ready and number)
    local = await run_db(_local_facts)

    data = None
    if vobiz_ready and number:
        fresh = _check["data"] is not None and _check["number"] == number and time.monotonic() - _check["at"] < CHECK_TTL
        if check and not fresh:
            data = await _vobiz_check(number)
            _check.update(at=time.monotonic(), number=number, data=data)
        elif _check["data"] is not None and _check["number"] == number:
            data = _check["data"]  # ?check=false: the last known Vobiz state, never a new Vobiz call

    apps = (data or {}).get("apps")
    num = (data or {}).get("number") or {}
    num_known = data is not None and data.get("number_found") is not None  # the number query worked
    app = _our_app(apps) if apps is not None else None
    number_app_id = str(num.get("application_id") or "") or None
    voice = num.get("voice_enabled")
    out: dict[str, Any] = {
        "enabled": bool(cfg.get("inbound_enabled", True)),
        "number": number,
        "answer_url": urls["answer"],
        "hangup_url": urls["hangup"],
        "public_url_ok": public_ok,
        "vobiz_ready": vobiz_ready,
        "ai_ready": not ai_missing,
        "checked": data is not None and not data["errors"],
        "app_id": str(app["app_id"]) if app else None,
        "app_name": app.get("app_name") if app else None,
        "app_urls_match": None if apps is None else bool(
            app and app.get("answer_url") == urls["answer"] and app.get("hangup_url") == urls["hangup"]),
        "number_linked": bool(app and number_app_id == str(app["app_id"])) if apps is not None and num_known else None,
        "number_app_id": number_app_id,
        "number_app_name": _app_name(apps, number_app_id),
        "number_on_trunk": bool(num.get("trunk_group_id")) if num else None,
        "number_voice_enabled": voice if isinstance(voice, bool) else None,
        "number_status": str(num["status"]) if num.get("status") else None,
        "previous_app_id": local["meta"].get("_inbound_previous_app_id"),
        "concurrency_max": _int_or_none(((data or {}).get("concurrency") or {}).get("max_concurrent")),
        "last_inbound_at": iso(local["last_inbound_at"]),
        "rejected_requests": vobiz.inbound_rejections["count"],
        "last_rejected_at": iso(vobiz.inbound_rejections["last_at"]),
    }
    out["problems"] = _problems(out, cfg, data, num, blockers)
    out["ready"] = bool(out["enabled"] and public_ok and vobiz_ready and out["ai_ready"]
                        and out["app_urls_match"] and out["number_linked"])
    return out


def _problems(s: dict, cfg: dict, data: dict | None, num: dict, blockers: list[str]) -> list[str]:
    """Plain-English list for the Inbound page (most urgent first)."""
    p: list[str] = []
    n = s["number"] or "our number"
    ai_missing = [b.split(" ")[0] for b in blockers if "API_KEY" in b]
    if ai_missing:
        p.append(f"{' and '.join(ai_missing)} {'is' if len(ai_missing) == 1 else 'are'} not set: callers are being "
                 "turned away (they get the unavailable message).")
    if not s["enabled"]:
        how = "a busy tone" if cfg.get("inbound_off_action") == "reject" else "the unavailable message"
        p.append(f"Inbound calling is switched off in Settings: callers hear {how}.")
    if not s["public_url_ok"]:
        p.append("PUBLIC_BASE_URL is not an https address, so Vobiz can't reach this backend: start the tunnel "
                 "and set PUBLIC_BASE_URL in backend/.env.")
    if not settings.vobiz_ready:
        p.append("Vobiz is not configured (VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN / VOBIZ_FROM_NUMBER in backend/.env).")
    elif not s["number"]:
        p.append(f"VOBIZ_FROM_NUMBER ({settings.vobiz_from_number}) is not a valid phone number.")
    for err in (data or {}).get("errors") or []:
        p.append(f"Couldn't check Vobiz: {err}")
    if data is not None and data.get("apps") is not None:
        if s["app_id"] is None:
            p.append(f"Not connected yet: calls to {n} don't reach the AI agent. Click \"Connect inbound on Vobiz\".")
        elif s["app_urls_match"] is False:
            p.append("The Vobiz application still points to an old address (the tunnel URL changed): "
                     "click Connect again.")
    if data is not None and data.get("number_found") is False:
        p.append(f"{n} was not found in this Vobiz account (check VOBIZ_FROM_NUMBER).")
    if num:
        if s["number_app_id"] and s["number_app_id"] != s["app_id"]:
            p.append(f"Calls to {n} go to another Vobiz application, '{s['number_app_name'] or s['number_app_id']}'. "
                     "Connecting moves them to the AI agent.")
        elif not s["number_app_id"] and s["app_id"]:
            p.append(f"{n} is not attached to the agent's application: click Connect.")
        if s["number_on_trunk"]:
            p.append(f"{n} is assigned to a SIP trunk, so its calls may go to the trunk instead of the AI agent.")
        if s["number_voice_enabled"] is False:
            p.append(f"Voice is disabled for {n} in Vobiz, so it can't receive calls.")
        if num.get("is_blocked") is True:
            p.append(f"{n} is blocked in Vobiz.")
        if s["number_status"] and s["number_status"].lower() != "active":
            p.append(f"{n} is not active in Vobiz (status: {s['number_status']}).")
        if num.get("is_trial_number") is True:
            p.append("Trial numbers can't receive inbound calls: use a purchased number.")
    if s["number"] and s["number"].startswith(("+91140", "+91160")):
        p.append("140 / 160-series numbers are outbound-only: they can't receive inbound calls.")
    if s["concurrency_max"] is not None and s["concurrency_max"] < GLOBAL_MAX_CONCURRENT:
        p.append(f"Your Vobiz account allows {s['concurrency_max']} calls at once (the agent can use up to "
                 f"{GLOBAL_MAX_CONCURRENT}): callers above that limit are turned away by Vobiz before they reach "
                 "the agent.")
    if s["rejected_requests"]:
        p.append(f"Vobiz is calling an old inbound URL ({s['rejected_requests']} request(s) refused since the "
                 "backend started): click Connect again.")
    p.append("The agent answers only while this computer, the backend and the tunnel are running.")
    return p


@router.get("/setup")
async def get_setup(check: bool = True):
    return await _status(check)


@router.post("/setup")
async def connect(force: bool = False):
    if not settings.public_base_url.startswith("https://"):
        raise HTTPException(400, "Set PUBLIC_BASE_URL to the https address of your tunnel first: "
                                 "Vobiz can't reach localhost.")
    number = _our_number()
    if not settings.vobiz_ready or not number:
        raise HTTPException(400, "Vobiz is not configured (VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN / VOBIZ_FROM_NUMBER "
                                 "in backend/.env).")
    async with _setup_lock():
        _clear_check()
        try:
            await _connect(number, force)
        except HTTPException:
            raise
        except vobiz.VobizError as e:
            raise HTTPException(502, f"Vobiz refused the change: {e}") from e
        except Exception as e:  # noqa: BLE001
            log.exception("inbound connect failed")
            raise HTTPException(502, f"Connecting failed: {e}") from e
        finally:
            _clear_check()
    return await _status(True)


async def _connect(number: str, force: bool) -> None:
    apps, num = await asyncio.gather(vobiz.list_applications(), vobiz.find_number(number))
    if num is None:
        raise HTTPException(400, f"{number} was not found in this Vobiz account (check VOBIZ_FROM_NUMBER).")
    app = _our_app(apps)
    app_id = str(app["app_id"]) if app else None
    bound = str(num.get("application_id") or "") or None
    on_trunk = bool(num.get("trunk_group_id"))
    if ((bound and bound != app_id) or (on_trunk and not bound)) and not force:
        where = _app_name(apps, bound) or (f"application {bound}" if bound else "a SIP trunk")
        raise HTTPException(409, f"Calls to {number} currently go to '{where}'. "
                                 "Connecting moves them to the AI agent.")
    urls = vobiz.inbound_urls()
    fields = {"answer_url": urls["answer"], "answer_method": "POST",
              "hangup_url": urls["hangup"], "hangup_method": "POST"}
    if app_id:
        await vobiz.update_application(app_id, fields)  # e.g. the tunnel URL changed
    else:
        app_id = await vobiz.create_application({"app_name": vobiz.INBOUND_APP_NAME, **fields,
                                                 "application_type": "XML"})
    if bound == app_id:
        await run_db(lambda: inbound_sync.save_meta(_inbound_app_id=app_id))
        return
    meta: dict[str, Any] = {"_inbound_app_id": app_id, "_inbound_connected_at": iso(utcnow())}
    if bound:  # remembered before the switch: Disconnect restores it
        meta["_inbound_previous_app_id"] = bound
    await run_db(lambda: inbound_sync.save_meta(**meta))
    await vobiz.attach_number(number, app_id)
    log.warning("inbound: %s attached to application %s (was %s)", number, app_id,
                bound or ("a SIP trunk" if on_trunk else "nothing"))


@router.delete("/setup")
async def disconnect():
    number = _our_number()
    if not settings.vobiz_ready or not number:
        raise HTTPException(400, "Vobiz is not configured (VOBIZ_AUTH_ID / VOBIZ_AUTH_TOKEN / VOBIZ_FROM_NUMBER "
                                 "in backend/.env).")
    async with _setup_lock():
        _clear_check()
        try:
            await _disconnect(number)
        except vobiz.VobizError as e:
            raise HTTPException(502, f"Vobiz refused the change: {e}") from e
        except Exception as e:  # noqa: BLE001
            log.exception("inbound disconnect failed")
            raise HTTPException(502, f"Disconnecting failed: {e}") from e
        finally:
            _clear_check()
    return await _status(True)


async def _disconnect(number: str) -> None:
    apps, num = await asyncio.gather(vobiz.list_applications(), vobiz.find_number(number))
    app = _our_app(apps)
    bound = str((num or {}).get("application_id") or "") or None
    meta = await run_db(inbound_sync.load_meta)
    if app and bound == str(app["app_id"]):  # never touch a number someone routed elsewhere meanwhile
        previous = meta.get("_inbound_previous_app_id")
        if previous and previous != bound and any(str(a.get("app_id")) == previous for a in apps):
            await vobiz.attach_number(number, previous)
            log.warning("inbound: %s re-attached to its previous application %s", number, previous)
        else:
            await vobiz.detach_number(number)
            log.warning("inbound: %s detached from the agent's application", number)
    await run_db(lambda: inbound_sync.save_meta(_inbound_previous_app_id=None, _inbound_connected_at=None))
