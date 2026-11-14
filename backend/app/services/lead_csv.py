"""Reading lead CSV files (Excel exports, any header names). Used by lead import and campaign uploads."""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import HTTPException, UploadFile

COLUMN_ALIASES = {
    "name": ("name", "full name", "fullname", "customer", "customer name", "contact name", "lead name", "naam"),
    "phone": ("phone", "mobile", "number", "contact", "phone number", "mobile number", "contact number",
              "whatsapp", "mobile no", "phone no", "cell"),
    "company": ("company", "business", "brand", "company name", "business name", "firm", "organization",
                "organisation"),
    "city": ("city", "location", "town", "district", "area", "place"),
    "email": ("email", "e-mail", "email id", "mail"),
    "source": ("source", "lead source", "campaign", "channel"),
    "notes": ("notes", "remarks", "comment", "comments", "note", "requirement", "description"),
}
TEXT_FIELDS = ("name", "company", "city", "email", "source", "notes")
MAX_UPLOAD = 20 * 1024 * 1024

TEMPLATE_CSV = ("name,phone,company,city,email,source,notes\n"
                "Rahul Sharma,9876543210,Sharma Snacks,Ahmedabad,rahul@example.com,IndiaMART,Namkeen brand\n"
                "Priya Patel,+91 98250 12345,Organic Roots,Pune,,Website,Needs stand-up pouches\n")


def map_columns(headers: list[str]) -> dict[str, str]:
    """field -> CSV header, matched case-insensitively against COLUMN_ALIASES."""
    mapping: dict[str, str] = {}
    norm = {h: h.strip().lower().replace("_", " ") for h in headers}
    for field, aliases in COLUMN_ALIASES.items():
        for h, n in norm.items():
            if h in mapping.values():
                continue
            if n in aliases:
                mapping[field] = h
                break
    if "phone" not in mapping:  # fuzzy: any header containing phone/mobile/number
        for h, n in norm.items():
            if any(k in n for k in ("phone", "mobile", "number", "contact")) and h not in mapping.values():
                mapping["phone"] = h
                break
    return mapping


def decode(raw: bytes) -> str:
    """UTF-8 (with/without BOM), UTF-16 only when it has a BOM, else Excel's Windows-1252."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def read_upload(file: UploadFile) -> bytes:
    raw = file.file.read(MAX_UPLOAD + 1)
    if len(raw) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 20 MB)")
    if not raw.strip():
        raise HTTPException(400, "The file is empty")
    return raw


def parse(raw: bytes) -> tuple[list[tuple[int, dict[str, Any]]], dict[str, str]]:
    """All data rows as (spreadsheet line number, row) plus the column mapping.

    Reads the whole file before anything is written, so a malformed file imports nothing.
    """
    text = decode(raw)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    csv.field_size_limit(1_000_000)
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    mapping = map_columns(headers)
    if "phone" not in mapping:
        raise HTTPException(400, f"No phone column found. Columns seen: {', '.join(headers) or 'none'}")
    rows: list[tuple[int, dict[str, Any]]] = []
    line = 1
    try:
        for line, row in enumerate(reader, start=2):
            rows.append((line, row))
    except csv.Error as e:
        raise HTTPException(400, f"The CSV is malformed near row {line + 1}: {e}. Nothing was imported.") from None
    return rows, mapping


def raw_phone(row: dict[str, Any], mapping: dict[str, str]) -> str:
    return (row.get(mapping["phone"]) or "").strip()


def lead_fields(row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """name/company/city/email/source/notes (None when blank) + `extra` for unmapped columns."""
    data: dict[str, Any] = {f: (str(row.get(mapping[f]) or "")).strip()[:500] or None
                            for f in TEXT_FIELDS if f in mapping}
    extra = {k: str(v)[:500] for k, v in row.items()
             if k and k not in mapping.values() and v not in (None, "")}
    data["extra"] = extra or None
    return data
