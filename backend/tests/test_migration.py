"""Upgrading an existing (pre-inbound) database: columns added, indexes replaced, data untouched."""
from __future__ import annotations

import sqlite3

from sqlalchemy import create_engine, text

from app import main as main_mod
from app.services import call_manager as cm_mod

# The calls/leads DDL as it was before inbound calling (no caller_type, old active-lead index).
OLD_SCHEMA = """
CREATE TABLE leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(200), phone VARCHAR(32) UNIQUE, company VARCHAR(200),
  city VARCHAR(120), email VARCHAR(200), source VARCHAR(120), notes TEXT, status VARCHAR(20),
  interest VARCHAR(20), rating INTEGER, attempts INTEGER, last_called_at VARCHAR, last_summary TEXT,
  extra TEXT, created_at VARCHAR, updated_at VARCHAR);
CREATE TABLE campaigns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(200), status VARCHAR(20), concurrency INTEGER,
  max_attempts INTEGER, retry_delay_min INTEGER, respect_call_window BOOLEAN, created_at VARCHAR,
  started_at VARCHAR, completed_at VARCHAR);
CREATE TABLE calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, uuid VARCHAR(40) UNIQUE, provider_call_id VARCHAR(80),
  lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL, campaign_id INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
  channel VARCHAR(16), direction VARCHAR(16), status VARCHAR(20), end_reason VARCHAR(40), from_number VARCHAR(32),
  to_number VARCHAR(32), started_at VARCHAR, answered_at VARCHAR, ended_at VARCHAR, duration_sec FLOAT,
  recording_path VARCHAR(300), recording_url VARCHAR(500), analysis_status VARCHAR(20), summary TEXT, rating INTEGER,
  interest VARCHAR(20), qualified BOOLEAN, sentiment VARCHAR(20), next_action TEXT, callback_time VARCHAR(120),
  qualification TEXT, key_points TEXT, objections TEXT, metrics TEXT, error TEXT, created_at VARCHAR);
CREATE UNIQUE INDEX uq_calls_active_lead ON calls(lead_id) WHERE lead_id IS NOT NULL
  AND status IN ('queued','initiated','ringing','in_progress');
INSERT INTO leads (id, name, phone, status, interest, attempts, created_at, updated_at)
  VALUES (1, 'Old Lead', '+919811100001', 'called', 'warm', 1, '2026-11-01T10:00:00.000Z', '2026-11-01T10:00:00.000Z');
INSERT INTO calls (uuid, lead_id, channel, direction, status, analysis_status, interest, created_at, started_at, answered_at)
  VALUES ('old-done', 1, 'phone', 'outbound', 'completed', 'done', 'warm', '2026-11-01T10:00:00.000Z',
          '2026-11-01T10:00:00.000Z', '2026-11-01T10:00:05.000Z');
INSERT INTO calls (uuid, lead_id, channel, direction, status, analysis_status, interest, created_at, started_at)
  VALUES ('old-live', 1, 'phone', NULL, 'ringing', 'pending', 'unknown', '2026-11-01T11:00:00.000Z',
          '2026-11-01T11:00:00.000Z');
"""


def test_add_columns_migrates_old_database(tmp_path, monkeypatch):
    db_file = tmp_path / "old.db"
    raw = sqlite3.connect(db_file)
    raw.executescript(OLD_SCHEMA)
    raw.commit()
    raw.close()

    from sqlalchemy.orm import sessionmaker

    old_engine = create_engine(f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr(main_mod, "engine", old_engine)
    monkeypatch.setattr(cm_mod, "SessionLocal", sessionmaker(bind=old_engine, expire_on_commit=False))

    for _ in range(2):  # the real startup order, twice: everything must be idempotent
        main_mod.Base.metadata.create_all(old_engine)
        main_mod._add_columns()
        cm_mod.manager.recover_on_startup()
        main_mod._migrate()

    with old_engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(calls)"))}
        assert "caller_type" in cols
        idx = dict(conn.execute(text("SELECT name, sql FROM sqlite_master WHERE type='index'")).all())
        assert "channel" in idx["uq_calls_active_lead"]
        assert "uq_calls_inbound_provider" in idx and "ix_calls_direction" in idx
        rows = dict(conn.execute(text("SELECT uuid, status FROM calls")).all())
        assert rows["old-done"] == "completed"
        assert rows["old-live"] not in ("queued", "initiated", "ringing", "in_progress")  # stale call closed
        assert conn.execute(text("SELECT COUNT(*) FROM calls WHERE direction IS NULL")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM leads")).scalar() == 1
    backups = list(tmp_path.glob("old.before-add-columns-*.db"))
    assert len(backups) == 1  # one backup, only on the run that actually changed the schema
    old_engine.dispose()
