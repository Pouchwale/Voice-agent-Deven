"""Activity log: every change made through the dashboard (who, when, what). Read-only: nothing here, or
anywhere else, edits or deletes an entry."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..db import utcnow
from ..services import audit
from ..services.csvsafe import csv_response

router = APIRouter(prefix="/api/activity", tags=["activity"])


@router.get("")
def list_activity(limit: int = 50, before_id: int | None = None, area: str | None = None, q: str | None = None):
    """Newest first. `area` narrows to one part of the dashboard, `q` searches the description and the name,
    `before_id` loads the entries older than that one."""
    if area and area not in audit.AREAS:
        raise HTTPException(422, f"area must be one of: {', '.join(audit.AREAS)}")
    return audit.list_entries(max(1, min(limit, 200)), before_id, area or None, (q or "").strip()[:100] or None)


@router.get("/export.csv")
def export_csv():
    """The whole log as a spreadsheet file (oldest first)."""
    name = f"activity_log_{audit._ist(utcnow(), '%Y%m%d')}.csv"
    return csv_response(audit.EXPORT_HEADERS, audit.export_rows(), name)
