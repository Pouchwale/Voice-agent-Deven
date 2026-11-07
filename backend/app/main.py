"""FastAPI entry point:  uvicorn app.main:app --host 0.0.0.0 --port 8100"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from . import models  # noqa: F401  (register tables)
from .config import get_settings
from .db import Base, engine
from .routers import (analytics, auth, calls, campaigns, dashboard, leads, live, settings as settings_router,
                      vobiz)
from .security import AccessGuard
from .services.analysis import analyze_call
from .services.call_manager import manager
from .services.dialer import dialer
from .services.events import bus, spawn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
settings = get_settings()


def _migrate() -> None:
    """Small idempotent in-place migrations for existing databases (run after crash recovery)."""
    from sqlalchemy import text
    with engine.begin() as conn:
        # the "closed" interest tier was removed: it meant "very interested / agreed" -> hot
        conn.execute(text("UPDATE leads SET interest='hot' WHERE interest='closed'"))
        conn.execute(text("UPDATE calls SET interest='hot' WHERE interest='closed'"))
        # at most ONE live call per lead, enforced by the database (no double-dialing races)
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_calls_active_lead ON calls(lead_id) WHERE lead_id IS NOT NULL "
            "AND status IN ('queued','initiated','ringing','in_progress')"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_campaign_leads_last_call_uuid "
                          "ON campaign_leads(last_call_uuid)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_campaign_leads_status ON campaign_leads(status)"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    bus.bind_loop(asyncio.get_running_loop())
    Base.metadata.create_all(engine)
    recovered, to_analyze = manager.recover_on_startup()
    if recovered:
        log.warning("closed %d call(s) left active by a previous run", recovered)
    _migrate()
    for call_uuid in to_analyze:  # answered calls whose analysis was lost in the restart
        spawn(analyze_call(call_uuid))
    if settings.public_base_url.startswith("https://") and not settings.dashboard_password:
        log.warning("Dashboard is local-only: set DASHBOARD_PASSWORD to allow access through %s",
                    settings.public_base_url)
    manager.start_watchdog()
    dialer.start()
    missing = manager.provider_problems(phone=True)
    if missing:
        log.warning("Not ready for phone calls: %s", "; ".join(missing))
    yield
    await dialer.stop()
    await manager.shutdown()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
# Order matters: the last added is the outermost. CORS must wrap the guard so 401s carry CORS headers.
app.add_middleware(AccessGuard)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, leads.router, calls.router, campaigns.router, dashboard.router, analytics.router,
          settings_router.router, live.router, vobiz.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok"}
