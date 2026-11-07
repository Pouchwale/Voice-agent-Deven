"""Add a few sample leads so the dashboard isn't empty on first run.

    python -m app.seed

Only inserts leads (no fake calls). Sample leads are marked Do-Not-Call so they can
never be dialed by mistake — their numbers could belong to real people.
"""
from __future__ import annotations

from sqlalchemy import select

from .db import Base, SessionLocal, engine
from .models import Lead

SAMPLES = [
    ("Rahul Sharma", "+919999900001", "Sharma Namkeen", "Ahmedabad", "IndiaMART", "Namkeen brand, center seal pouches"),
    ("Priya Patel", "+919999900002", "Organic Roots", "Pune", "Website", "Rice & pulses, needs stand-up zipper pouch"),
    ("Arjun Mehta", "+919999900003", "BrewBox Coffee", "Bengaluru", "Instagram", "Premium coffee, quad seal"),
    ("Sneha Reddy", "+919999900004", "Pawsome Treats", "Hyderabad", "Referral", "Pet food, large pouches"),
    ("Vikram Singh", "+919999900005", "Desi Masala Co", "Jaipur", "Trade fair", "Spices, small sachets"),
]


def main() -> None:
    Base.metadata.create_all(engine)
    added = 0
    with SessionLocal() as db:
        existing = set(db.scalars(select(Lead.phone)).all())
        for name, phone, company, city, source, notes in SAMPLES:
            if phone in existing:
                continue
            db.add(Lead(name=name, phone=phone, company=company, city=city, source=source, status="dnc",
                        notes=f"SAMPLE LEAD (not a real customer, never dialed). {notes}"))
            added += 1
        db.commit()
    print(f"Added {added} sample lead(s).")


if __name__ == "__main__":
    main()
