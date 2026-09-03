"""
Spending ledger for the paid generation providers.

WHY THIS EXISTS
---------------
The README claimed a fixed spending cap and a hard retry limit per item. Neither
existed: `genera_video_ai.py` printed a cost *estimate* before generating and
that was all. Nothing recorded what had actually been spent, so nothing could
enforce a ceiling, and a retry loop on a stubborn item could quietly burn a
month's budget in an afternoon.

A cap you cannot see is not a cap. This keeps a real ledger, in the same SQLite
database as the publishing state, and refuses the call when the month's spend
would go over.

RESERVE FIRST, THEN SPEND
-------------------------
The same shape as the publish registry, for the same reason: the money leaves
before the local record is written, so the record has to come first.

    reservation = reserve(cost)   # recorded as 'reserved'
    ...call the provider...
    reservation.settle(actual)    # what it really cost
    reservation.release()         # only if the call provably never happened

A reservation left behind by a crash stays counted against the budget. That
errs toward under-spending, which is the harmless direction: the worst case is
a generation postponed to next month, not an unnoticed overrun.

RETRIES
-------
Attempts are counted per item, so a scene that keeps failing stops costing money
after a few tries instead of retrying until the budget is gone.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone

from upload_registry import DB_NAME, _Transaction

DEFAULT_MONTHLY_CAP_USD = 20.0
DEFAULT_MAX_ATTEMPTS_PER_ITEM = 3

RESERVED = "reserved"
SETTLED = "settled"
RELEASED = "released"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spend (
    id          TEXT PRIMARY KEY,
    provider    TEXT NOT NULL,
    item        TEXT,
    state       TEXT NOT NULL,
    amount_usd  REAL NOT NULL,
    month       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    settled_at  TEXT,
    -- Azzerare i tentativi di un item NON deve cancellare la spesa: i soldi
    -- sono usciti davvero e vanno contati fino a fine mese. Qui si separa
    -- "quanto e' costato" da "quante volte ci ho gia' provato".
    reset_at    TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_spend_month ON spend(provider, month, state);
CREATE INDEX IF NOT EXISTS idx_spend_item  ON spend(provider, item);
"""


class BudgetExceeded(RuntimeError):
    """The call was refused because it would push the month over the cap."""


class TooManyAttempts(RuntimeError):
    """This item has already been retried enough times to stop paying for it."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


class Reservation:
    """One reserved spend, waiting to be settled or released."""

    def __init__(self, ledger: "Budget", reservation_id: str, amount: float):
        self.ledger = ledger
        self.id = reservation_id
        self.amount = amount

    def settle(self, actual_usd: float | None = None, note: str | None = None) -> None:
        """Record what the call really cost. Defaults to the reserved amount."""
        self.ledger._finish(self.id, SETTLED,
                            self.amount if actual_usd is None else actual_usd, note)

    def release(self, note: str | None = None) -> None:
        """Give the money back — ONLY when the provider provably did no work.

        A timeout is not proof: the provider may well have generated and billed.
        Leaving an ambiguous reservation in place costs us a little headroom and
        nothing else.
        """
        self.ledger._finish(self.id, RELEASED, 0.0, note)


class Budget:
    """Monthly spending ledger for one provider."""

    def __init__(self, output_dir: str, provider: str,
                 monthly_cap_usd: float = DEFAULT_MONTHLY_CAP_USD,
                 max_attempts_per_item: int = DEFAULT_MAX_ATTEMPTS_PER_ITEM):
        os.makedirs(output_dir, exist_ok=True)
        self.db_path = os.path.join(output_dir, DB_NAME)
        self.provider = provider
        self.cap = monthly_cap_usd
        self.max_attempts = max_attempts_per_item
        self.conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)

    # ---- queries -------------------------------------------------------

    def spent_this_month(self) -> float:
        """Settled spend plus reservations still outstanding.

        Counting live reservations is deliberate: two processes generating at
        once must not each see the same headroom and both take it.
        """
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) AS total FROM spend "
            "WHERE provider = ? AND month = ? AND state IN (?, ?)",
            (self.provider, _month(), SETTLED, RESERVED),
        ).fetchone()
        return float(row["total"])

    def remaining(self) -> float:
        return max(0.0, self.cap - self.spent_this_month())

    def attempts(self, item: str) -> int:
        """Tentativi a pagamento non ancora azzerati.

        I record azzerati restano nel registro e continuano a pesare sul budget
        del mese — la spesa e' avvenuta — ma non contano piu' come tentativi.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM spend WHERE provider = ? AND item = ? "
            "AND state IN (?, ?) AND reset_at IS NULL",
            (self.provider, item, SETTLED, RESERVED),
        ).fetchone()
        return int(row["n"])

    # ---- the gate ------------------------------------------------------

    def reserve(self, amount_usd: float, item: str | None = None,
                note: str | None = None) -> Reservation:
        """Take budget for a call that is about to happen, or refuse it.

        The check and the reservation are one transaction, so two concurrent
        generations cannot both squeeze through the last few dollars.
        """
        reservation_id = uuid.uuid4().hex
        with _Transaction(self.conn):
            if item is not None and self.attempts(item) >= self.max_attempts:
                raise TooManyAttempts(
                    f"'{item}' ha già consumato {self.max_attempts} tentativi a pagamento: "
                    f"smetto di spenderci sopra. Correggi il prompt o l'asset, poi azzera "
                    f"i tentativi con reset_item()."
                )
            spent = self.spent_this_month()
            if spent + amount_usd > self.cap:
                raise BudgetExceeded(
                    f"${amount_usd:.2f} sforerebbe il tetto mensile: "
                    f"spesi ${spent:.2f} di ${self.cap:.2f} "
                    f"(restano ${self.cap - spent:.2f})."
                )
            self.conn.execute(
                "INSERT INTO spend (id, provider, item, state, amount_usd, month, "
                "created_at, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (reservation_id, self.provider, item, RESERVED, amount_usd,
                 _month(), _now(), note),
            )
        return Reservation(self, reservation_id, amount_usd)

    def _finish(self, reservation_id: str, state: str, amount: float,
                note: str | None) -> None:
        with _Transaction(self.conn):
            self.conn.execute(
                "UPDATE spend SET state = ?, amount_usd = ?, settled_at = ?, "
                "note = COALESCE(?, note) WHERE id = ? AND state = ?",
                (state, amount, _now(), note, reservation_id, RESERVED),
            )

    def reset_item(self, item: str) -> None:
        """Azzera i tentativi di un item, dopo aver corretto il problema.

        La spesa gia' sostenuta resta contata: azzerare i tentativi non
        restituisce i soldi, li ha spesi il fornitore.
        """
        with _Transaction(self.conn):
            self.conn.execute(
                "UPDATE spend SET reset_at = ? WHERE provider = ? AND item = ? "
                "AND reset_at IS NULL",
                (_now(), self.provider, item))

    def summary(self) -> str:
        spent = self.spent_this_month()
        return (f"budget {self.provider} {_month()}: "
                f"${spent:.2f} / ${self.cap:.2f}  (restano ${self.cap - spent:.2f})")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
        return False
