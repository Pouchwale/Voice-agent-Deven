"""Database engine + session.

SQLite in WAL mode (fast concurrent reads while calls write transcripts).
Column types are chosen so Prisma can introspect / read the same file:
  - INTEGER autoincrement ids, TEXT, REAL, BOOLEAN
  - DATETIME columns stored as RFC 3339 text ("2026-11-02T10:15:30.123Z"),
    which Prisma's SQLite connector parses natively.
  - JSON payloads stored as TEXT (Prisma `String`).
See frontend/prisma/schema.prisma for the mirrored schema.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import Text, TypeDecorator, UserDefinedType

from .config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - driver hook
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA cache_size=-20000")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _DateTimeSQL(UserDefinedType):
    """Declares the column as DATETIME so Prisma introspects it as DateTime."""

    cache_ok = True

    def get_col_spec(self, **kw: Any) -> str:
        return "DATETIME"


class UTCDateTime(TypeDecorator):
    """Timezone-aware datetime stored as RFC 3339 UTC text (Prisma compatible).

    Reads RFC 3339 text, SQLAlchemy-style "YYYY-MM-DD HH:MM:SS" text, or the
    integer unix-milliseconds Prisma may write.
    """

    impl = _DateTimeSQL
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"

    def process_result_value(self, value: Any, dialect) -> datetime | None:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
        text = str(value).strip()
        if text.isdigit():
            return datetime.fromtimestamp(int(text) / 1000.0, tz=timezone.utc)
        text = text.replace(" ", "T").replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class JSONText(TypeDecorator):
    """JSON stored as TEXT (Prisma reads it as String)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect) -> str | None:
        if value is None:
            return None
        return json.dumps(value, ensure_ascii=False)

    def process_result_value(self, value: Any, dialect) -> Any:
        if value is None or value == "":
            return None
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
