"""One persistent campaign ledger. SQLite transactions protect concurrent reservations."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING, InvalidOperation
from pathlib import Path

from .common import Blocked, EvaluationError, canonical, now


def units(value) -> int:
    return int((Decimal(str(value)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


class Budget:
    def __init__(self, path: Path, cap=500, concurrency=2, provider_concurrency=1, *, provider_managed=False):
        self.provider_managed = provider_managed
        self.path, self.cap = path, units(cap)
        self.concurrency, self.provider_concurrency = concurrency, provider_concurrency
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, provider TEXT, reserved INTEGER, actual INTEGER, state TEXT, detail TEXT)")
            row = db.execute("SELECT value FROM meta WHERE key='cap'").fetchone()
            if not self.provider_managed and row and int(row[0]) != self.cap:
                raise Blocked("Campaign cap cannot silently reset between stages/runs")
            db.execute("INSERT OR IGNORE INTO meta VALUES ('cap', ?)", (str(self.cap),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, request_id: str, provider: str, upper_bound: dict | None) -> None:
        if self.provider_managed:
            upper_bound = dict.fromkeys(("input", "output", "reasoning", "images", "other"), 0)
        if upper_bound is None or set(upper_bound) != {"input", "output", "reasoning", "images", "other"}:
            raise Blocked("Unknown/incomplete billing upper bound")
        if any(v is None or not Decimal(str(v)).is_finite() or Decimal(str(v)) < 0 for v in upper_bound.values()):
            raise Blocked("Unknown/invalid pricing")
        amount = sum(units(v) for v in upper_bound.values())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self.provider_managed and db.execute("SELECT 1 FROM reservations WHERE state='UNKNOWN'").fetchone():
                raise Blocked("Unresolved provider charge blocks new requests")
            rows = db.execute("SELECT provider,reserved,actual,state FROM reservations").fetchall()
            active = [r for r in rows if r[3] == "RESERVED"]
            used = sum(r[2] if r[2] is not None else r[1] for r in rows)
            if len(active) >= self.concurrency or sum(r[0] == provider for r in active) >= self.provider_concurrency:
                raise Blocked("Concurrency limit")
            if not self.provider_managed and used + amount > self.cap:
                raise Blocked("Campaign budget exhausted")
            try:
                db.execute("INSERT INTO reservations VALUES (?,?,?,?,?,?)", (request_id, provider, amount, None,
                    "RESERVED", canonical({"bound": None if self.provider_managed else upper_bound, "cost_control": "PROVIDER_MANAGED" if self.provider_managed else "LOCAL_CAP", "time": now()})))
            except sqlite3.IntegrityError as exc:
                raise Blocked("Request already reserved/charged; reconcile before retry") from exc

    def settle(self, request_id: str, actual: float | None, *, force_unknown=False) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT reserved,state FROM reservations WHERE id=?", (request_id,)).fetchone()
            if not row or row[1] != "RESERVED":
                raise EvaluationError("Reservation missing or already settled")
            try:
                cost = units(actual) if actual is not None and not isinstance(actual, bool) else None
                if cost is not None and not 0 <= cost <= 2**63 - 1:
                    cost = None
            except (ValueError, TypeError, OverflowError, InvalidOperation):
                # Malformed/oversized charges are uncertain, not zero. Commit
                # UNKNOWN before raising so other requests cannot continue.
                cost = None
            state = "SETTLED" if not force_unknown and cost is not None and 0 <= cost <= row[0] else "UNKNOWN"
            if self.provider_managed:
                state = "SETTLED" if cost is not None else "UNPRICED"
            db.execute("UPDATE reservations SET actual=?,state=? WHERE id=?", (cost, state, request_id))
        if state == "UNKNOWN":
            raise Blocked("Billing missing or exceeded verified bound; preserve reservation and stop")

    def summary(self) -> dict:
        with self.connect() as db:
            rows = db.execute("SELECT reserved,actual,state FROM reservations").fetchall()
        used = sum(a if a is not None else r for r, a, _ in rows)
        return {"cap_cny": None if self.provider_managed else self.cap / 1e6, "cost_control": "PROVIDER_MANAGED" if self.provider_managed else "LOCAL_CAP", "unpriced_requests": sum(a is None for _, a, _ in rows), "accounted_cny": used / 1e6,
                "warning": False if self.provider_managed else used >= self.cap * .8, "states": [r[2] for r in rows]}
