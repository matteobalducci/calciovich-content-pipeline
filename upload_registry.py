"""
Publishing state for the YouTube / Instagram / TikTok publishers.

WHY A WRITE-AHEAD REGISTRY
--------------------------
Every publisher used to do this:

    external_id = upload(item)        # the video is now live on the platform
    registry[key] = {...}             # <-- a crash here loses the record
    save(registry)                    #     and the next run republishes

A file lock stops two *concurrent* runs from racing. It does nothing about a
crash, a kill, or an HTTP timeout landing between the external side effect and
the local write. That window is small and it is exactly the window that produces
duplicate public posts, which retrying cannot undo.

So the intent is written down first, and the outcome is reconciled later:

    begin(key)              -> state = pending, BEFORE the external call
    progress(key, id=...)   -> the platform handed us an intermediate id
    confirm(key, id)        -> state = confirmed
    fail(key, reason)       -> state = failed, only for KNOWN non-events

A `pending` record left behind by a dead process means the outcome is UNKNOWN,
not failed, so it keeps blocking re-upload until `reconcile()` asks the platform
what actually happened. Publishing twice is worse than publishing late.

`progress()` matters more than it looks: a pending record holding only the
intent has nothing to ask the platform about. Persisting the container id or
publish id the moment the platform returns it is what makes recovery possible
instead of guesswork.

WHY SQLITE AND NOT JSON
-----------------------
This used to be three JSON files, each read-modify-written in full. Replacing a
file atomically (temp + rename) keeps it from being observed half-written, but it
does not *serialise* anything: two processes that both read, then both write,
silently lose one of the two updates — last writer wins. The publishers' file
lock only guards a script against itself, not `check_outliers.py` against
`aggiorna_youtube_stats.py`.

SQLite gives real transactions, so a read-modify-write is atomic against every
other process on the machine, not just the polite ones. WAL mode lets readers
work while a writer holds the lock, and `BEGIN IMMEDIATE` takes the write lock
up front so two writers queue instead of colliding.

The legacy JSON files are imported automatically on first open and then left
alone as a backup.

The `load()` / `save()` helpers below stay JSON: they are used for small config
and metrics files that are not publishing state, and there atomic replacement is
the right tool.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone

CONFIRMED = "confirmed"
PENDING = "pending"
FAILED = "failed"

DB_NAME = "publish-state.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS uploads (
    platform    TEXT NOT NULL,
    key         TEXT NOT NULL,
    state       TEXT NOT NULL,
    source_id   TEXT,
    external_id TEXT,
    attempt_id  TEXT,
    started_at  TEXT,
    updated_at  TEXT,
    meta        TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (platform, key)
);
CREATE INDEX IF NOT EXISTS idx_uploads_state  ON uploads(platform, state);
CREATE INDEX IF NOT EXISTS idx_uploads_source ON uploads(platform, source_id);
"""


class RegistryCorrupt(RuntimeError):
    """State exists but could not be read.

    Deliberately fatal. The alternative — treating it as empty — makes the
    pipeline believe nothing was ever published and re-upload the entire back
    catalogue.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------- JSON helpers
# Not publishing state: small config and metrics files, where replacing the
# whole file atomically is exactly right.

def load(path: str) -> dict:
    """Read a JSON file. Missing -> empty. Unreadable -> raise."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RegistryCorrupt(
            f"{path} is not valid JSON ({exc}). Refusing to continue: treating it as "
            f"empty would republish everything. Restore it from git or from the "
            f"platform's own history before running again."
        ) from exc
    if not isinstance(data, dict):
        raise RegistryCorrupt(f"{path} does not contain a JSON object.")
    return data


def save(path: str, payload: dict) -> None:
    """Write a JSON file atomically.

    A plain open(path, "w") truncates first, so a crash mid-write leaves a
    truncated file. Writing to a temp file in the same directory and renaming
    makes the swap atomic on POSIX.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".registry-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------- record shape

def state_of(record: dict) -> str:
    """State of a record, defaulting to confirmed for pre-migration entries."""
    return record.get("state", CONFIRMED)


def is_settled(record: dict) -> bool:
    """True if this record should block a re-upload.

    Confirmed and pending both block. Pending blocks because the outcome is
    unknown, and an unknown outcome must not be resolved by trying again.
    """
    return state_of(record) in (CONFIRMED, PENDING)


def _row_to_record(row: sqlite3.Row) -> dict:
    """Flatten a row back into the dict shape callers already expect."""
    record = json.loads(row["meta"] or "{}")
    record["state"] = row["state"]
    if row["source_id"] is not None:
        record["source_id"] = row["source_id"]
    if row["external_id"] is not None:
        record["external_id"] = row["external_id"]
    if row["attempt_id"] is not None:
        record["attempt_id"] = row["attempt_id"]
    return record


_RESERVED = {"state", "source_id", "external_id", "attempt_id"}


class Registry:
    """Publishing state for one platform.

    Constructed with the legacy JSON path so callers did not have to change:
    the platform name comes from the filename, and the database lives beside it.
    """

    def __init__(self, path: str):
        self.json_path = path
        directory = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(directory, exist_ok=True)
        self.db_path = os.path.join(directory, DB_NAME)
        self.platform = os.path.basename(path).replace("-uploads.json", "").replace(".json", "")
        self._connect()
        self._migrate_legacy_json()

    def _connect(self) -> None:
        try:
            self.conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        except sqlite3.Error as exc:
            raise RegistryCorrupt(f"cannot open {self.db_path}: {exc}") from exc
        self.conn.row_factory = sqlite3.Row
        # WAL: readers do not block the writer and vice versa. Without it a
        # dashboard reading the state can lock out the publisher writing it.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.executescript(_SCHEMA)

    def _migrate_legacy_json(self) -> None:
        """Import the old JSON registry once, then leave it alone as a backup."""
        if not os.path.exists(self.json_path):
            return
        already = self.conn.execute(
            "SELECT COUNT(*) FROM uploads WHERE platform = ?", (self.platform,)
        ).fetchone()[0]
        if already:
            return
        legacy = load(self.json_path)
        if not legacy:
            return
        # Una sola transazione: un crash a meta' import lascerebbe righe parziali,
        # e alla riapertura il COUNT(*) > 0 farebbe saltare il resto per sempre.
        # Il re-check dentro la transazione copre due import concorrenti.
        with self._txn():
            if self.conn.execute("SELECT COUNT(*) FROM uploads WHERE platform = ?",
                                 (self.platform,)).fetchone()[0]:
                return
            for key, record in legacy.items():
                if not isinstance(record, dict):
                    continue
                self._write(key, dict(record), state=state_of(record))
        print(f"↪︎  importati {len(legacy)} record da {os.path.basename(self.json_path)} "
              f"in {DB_NAME} (il file JSON resta come backup)")

    # ---- internals -----------------------------------------------------

    def _write(self, key: str, record: dict, state: str) -> None:
        meta = {k: v for k, v in record.items() if k not in _RESERVED}
        self.conn.execute(
            """
            INSERT INTO uploads (platform, key, state, source_id, external_id,
                                 attempt_id, started_at, updated_at, meta)
            VALUES (?, ?, ?, ?, ?, ?, COALESCE((SELECT started_at FROM uploads
                        WHERE platform = ? AND key = ?), ?), ?, ?)
            ON CONFLICT(platform, key) DO UPDATE SET
                state       = excluded.state,
                source_id   = COALESCE(excluded.source_id, uploads.source_id),
                external_id = COALESCE(excluded.external_id, uploads.external_id),
                attempt_id  = COALESCE(excluded.attempt_id, uploads.attempt_id),
                updated_at  = excluded.updated_at,
                meta        = excluded.meta
            """,
            (self.platform, key, state, record.get("source_id"),
             record.get("external_id"), record.get("attempt_id"),
             self.platform, key, _now(), _now(), json.dumps(meta, ensure_ascii=False)),
        )

    def _read(self, key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM uploads WHERE platform = ? AND key = ?", (self.platform, key)
        ).fetchone()
        return _row_to_record(row) if row else None

    # ---- queries -------------------------------------------------------

    @property
    def data(self) -> dict:
        """Every record for this platform, in the dict shape callers expect."""
        rows = self.conn.execute(
            "SELECT * FROM uploads WHERE platform = ?", (self.platform,)
        ).fetchall()
        return {row["key"]: _row_to_record(row) for row in rows}

    def published_keys(self) -> set:
        return {k for k, rec in self.data.items() if is_settled(rec)}

    def published_source_ids(self) -> set:
        rows = self.conn.execute(
            "SELECT DISTINCT source_id FROM uploads "
            "WHERE platform = ? AND source_id IS NOT NULL AND state IN (?, ?)",
            (self.platform, CONFIRMED, PENDING),
        ).fetchall()
        return {row["source_id"] for row in rows}

    def already_handled(self, key: str, source_id: str | None = None) -> bool:
        """True if this item must not be uploaded again.

        Checks the filename key and the stable source id, so an item regenerated
        under a new filename is still recognised.
        """
        record = self._read(key)
        if record is not None and is_settled(record):
            return True
        return bool(source_id) and source_id in self.published_source_ids()

    def pending(self) -> dict:
        rows = self.conn.execute(
            "SELECT * FROM uploads WHERE platform = ? AND state = ?",
            (self.platform, PENDING),
        ).fetchall()
        return {row["key"]: _row_to_record(row) for row in rows}

    # ---- transitions ---------------------------------------------------
    # Each one is a single transaction: the read, the merge and the write cannot
    # be interleaved with another process doing the same thing.

    def claim(self, key: str, source_id: str | None = None, **meta) -> str | None:
        """Verifica e prende in carico l'item in UNA sola transazione.

        `already_handled()` seguito da `begin()` sono due transazioni distinte:
        fra l'una e l'altra un altro processo puo' aver preso in carico lo stesso
        item, e `begin()` sovrascriverebbe il suo record — anche se confirmed.
        Il lock dei publisher protegge uno script da se stesso, non dagli altri
        utilizzatori del registro.

        Restituisce l'attempt_id se l'item e' stato preso in carico, None se
        risulta gia' sistemato (confirmed o pending) e va saltato.
        """
        attempt_id = uuid.uuid4().hex
        with self._txn():
            existing = self._read(key)
            if existing is not None and is_settled(existing):
                return None
            if source_id:
                row = self.conn.execute(
                    "SELECT key FROM uploads WHERE platform = ? AND source_id = ? "
                    "AND state IN (?, ?)",
                    (self.platform, source_id, CONFIRMED, PENDING)).fetchone()
                if row:
                    return None
            record = dict(existing or {})
            record.update(meta)
            record.update({"attempt_id": attempt_id, "source_id": source_id,
                           "startedAt": _now()})
            record.pop("error", None)
            self._write(key, record, PENDING)
        return attempt_id

    def begin(self, key: str, source_id: str | None = None, **meta) -> str:
        """Record the intent to upload, BEFORE touching the platform."""
        attempt_id = uuid.uuid4().hex
        with self._txn():
            record = self._read(key) or {}
            record.update(meta)
            record.update({"attempt_id": attempt_id, "source_id": source_id,
                           "startedAt": _now()})
            record.pop("error", None)
            self._write(key, record, PENDING)
        return attempt_id

    def progress(self, key: str, **meta) -> None:
        """Note an intermediate id WITHOUT changing state.

        Without this a pending record holds only the intent, and reconciliation
        has nothing to ask the platform about.
        """
        with self._txn():
            record = self._read(key) or {}
            record.update(meta)
            self._write(key, record, state_of(record) if record else PENDING)

    def confirm(self, key: str, external_id: str, **meta) -> None:
        """Record that the platform accepted the upload."""
        with self._txn():
            record = self._read(key) or {}
            record.update(meta)
            record.update({"external_id": external_id, "confirmedAt": _now()})
            record.pop("error", None)
            self._write(key, record, CONFIRMED)

    def fail(self, key: str, reason: str) -> None:
        """Record that the upload definitely did not happen.

        Only for KNOWN failures — a rejected request, a validation error. A
        timeout or a dropped connection is not a known failure: leave those
        pending so reconciliation can decide.
        """
        with self._txn():
            record = self._read(key) or {}
            record.update({"error": reason[:500], "failedAt": _now()})
            self._write(key, record, FAILED)

    # ---- recovery ------------------------------------------------------

    def reconcile(self, probe) -> list:
        """Resolve pending records left behind by a previous run.

        `probe(key, record)` asks the platform whether the item actually got
        published: it returns the external id if it did, None if it provably did
        not, or raises if it cannot tell.

        A probe that raises leaves the record pending — the item stays blocked,
        which is the safe direction. Returns (key, outcome) pairs for reporting.
        """
        outcomes = []
        for key, record in self.pending().items():
            try:
                external_id = probe(key, record)
            except Exception as exc:  # cannot tell: stay pending, stay blocked
                outcomes.append((key, f"unresolved ({type(exc).__name__})"))
                continue
            if external_id:
                self.confirm(key, external_id, recoveredAt=_now())
                outcomes.append((key, f"recovered as {external_id}"))
            else:
                with self._txn():
                    self.conn.execute(
                        "DELETE FROM uploads WHERE platform = ? AND key = ?",
                        (self.platform, key))
                outcomes.append((key, "cleared (never landed)"))
        return outcomes

    # ---- plumbing ------------------------------------------------------

    def _txn(self):
        return _Transaction(self.conn)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


class _Transaction:
    """BEGIN IMMEDIATE takes the write lock up front, so two writers queue
    instead of both reading, both merging and one silently losing."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, *_):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
