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
from .routers import (analytics, auth, calls, campaigns, dashboard, inbound, inbound_stats, leads, live,
                      settings as settings_router, vobiz)
from .security import AccessGuard
from .services import inbound_audio, inbound_sync
from .services.agent_config import load_agent_settings
from .services.analysis import analyze_call
from .services.call_manager import manager
from .services.dialer import dialer
from .services.events import bus, spawn

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")
settings = get_settings()


def _backup_database(reason: str) -> None:
    """Copy the SQLite file (online backup API, consistent even with WAL) before changing its schema."""
    import sqlite3
    from datetime import datetime
    from pathlib import Path

    db_path = engine.url.database
    if engine.url.get_backend_name() != "sqlite" or not db_path or db_path == ":memory:":
        return
    src_path = Path(db_path)
    if not src_path.exists():
        return
    dest = src_path.with_name(f"{src_path.stem}.before-{reason}-{datetime.now():%Y%m%d-%H%M%S}.db")
    src = sqlite3.connect(str(src_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    log.warning("migration: database backed up to %s", dest)


def _add_columns() -> None:
    """Add every mapped column an older database lacks (SQLite has no ADD COLUMN IF NOT EXISTS).

    Must run BEFORE crash recovery, whose ORM queries select every mapped column and would fail with
    "no such column" on an older database. Takes a backup first when anything is about to change.
    """
    from sqlalchemy import text
    plan: list[tuple[str, str, str]] = []
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table.name})"))}
            if not existing:
                continue  # brand-new table: create_all made it with every column
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable and column.server_default is None:
                    log.error("migration: can't add NOT NULL column %s.%s without a default", table.name, column.name)
                    continue
                plan.append((table.name, column.name, column.type.compile(dialect=engine.dialect)))
    if not plan:
        return
    _backup_database("add-columns")
    with engine.begin() as conn:
        for table_name, column_name, sql_type in plan:
            conn.execute(text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {sql_type}'))
            log.warning("migration: added %s.%s", table_name, column_name)


def _migrate() -> None:
    """Small idempotent in-place migrations for existing databases (run after crash recovery)."""
    from sqlalchemy import text
    with engine.begin() as conn:
        # the "closed" interest tier was removed: it meant "very interested / agreed" -> hot
        conn.execute(text("UPDATE leads SET interest='hot' WHERE interest='closed'"))
        conn.execute(text("UPDATE calls SET interest='hot' WHERE interest='closed'"))
        # rows from before inbound calling existed are outbound (filters compare direction exactly)
        conn.execute(text("UPDATE calls SET direction='outbound' WHERE direction IS NULL OR direction=''"))
        # At most ONE live PHONE call per lead, enforced by the database (no double-dialing races).
        # Older databases have the version without the channel filter: replace it once.
        old = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_calls_active_lead'"))
        old_sql = (old.scalar() or "")
        if old_sql and "channel" not in old_sql:
            conn.execute(text("DROP INDEX uq_calls_active_lead"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_calls_active_lead ON calls(lead_id) WHERE lead_id IS NOT NULL "
            "AND channel = 'phone' AND status IN ('queued','initiated','ringing','in_progress')"))
        # one row per Vobiz inbound call (answer-webhook retries can't create a second row)
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_calls_inbound_provider ON calls(provider_call_id) "
            "WHERE direction = 'inbound' AND provider_call_id IS NOT NULL AND provider_call_id <> ''"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_campaign_leads_last_call_uuid "
                          "ON campaign_leads(last_call_uuid)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_campaign_leads_status ON campaign_leads(status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_calls_direction ON calls(direction, id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_calls_from_number ON calls(from_number)"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    bus.bind_loop(asyncio.get_running_loop())
    Base.metadata.create_all(engine)
    _add_columns()
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
    inbound_audio.schedule_prewarm(load_agent_settings())  # first-caller greeting + unavailable message
    inbound_sync.start()  # calls that rang while we were offline -> missed calls to call back
    missing = manager.provider_problems(phone=True)
    if missing:
        log.warning("Not ready for phone calls: %s", "; ".join(missing))
    yield
    await inbound_sync.stop()
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
          settings_router.router, live.router, vobiz.router, inbound.router, inbound_stats.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok"}
