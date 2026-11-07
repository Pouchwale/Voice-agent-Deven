"""CSV export helpers: Excel-safe cells (no formula injection) and a UTF-8 BOM writer."""
from __future__ import annotations

import csv
import io
from typing import Any, Iterable

from fastapi.responses import StreamingResponse

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def cell(value: Any) -> Any:
    """Neutralise values Excel/Sheets would execute as a formula (OWASP CSV injection).

    Lead names and companies come from third-party lead lists, so `=HYPERLINK(...)` or
    `+cmd|...` must be written as text. Side benefit: +91 numbers stay text in Excel.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_START):
        return "'" + value
    return value


def csv_response(headers: list[str], rows: Iterable[Iterable[Any]], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM so Excel shows Hindi correctly
    w = csv.writer(buf)
    w.writerow(headers)
    for row in rows:
        w.writerow([cell(v) for v in row])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})
