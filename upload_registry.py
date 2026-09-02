"""
Crash-safe upload registry shared by the YouTube / Instagram / TikTok publishers.

WHY THIS EXISTS
---------------
Every publisher used to do this:

    external_id = upload(item)        # the video is now live on the platform
    registry[key] = {...}             # <-- a crash here loses the record
    save(registry)                    #     and the next run republishes

The file lock the publishers already take prevents two *concurrent* runs from
racing. It does nothing about a crash, a kill, or an HTTP timeout landing
between the external side effect and the local write. That window is small but
it is exactly the window that produces duplicate posts, and duplicates on a
public channel are not recoverable by retrying harder.

The fix is the standard one for "do a remote effect, then record it locally":
write the intent down *before* the effect, and reconcile on the next run.

    begin(key)          -> record {"state": "pending", "attempt_id": ...}
    upload(item)        -> external side effect
    confirm(key, id)    -> record {"state": "confirmed", "external_id": ...}

A "pending" record left behind by a dead process is the signal that the outcome
is UNKNOWN, not that it failed. On the next run, `reconcile()` asks the platform
what actually happened and resolves it. Until it is resolved, the item is
treated as already published, because publishing twice is worse than publishing
late.

DURABILITY
----------
Writes go through a temp file + fsync + os.replace, so the registry is never
observed half-written. A torn or corrupt registry FAILS CLOSED (raises) instead
of degrading to an empty dict: an empty registry means "nothing was ever
published", which would republish the entire back catalogue.

BACKWARD COMPATIBILITY
----------------------
Records written before this module existed have no "state" field. They are read
as confirmed, because that is what they were.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone

CONFIRMED = "confirmed"
PENDING = "pending"
FAILED = "failed"


class RegistryCorrupt(RuntimeError):
    """The registry file exists but could not be parsed.

    Deliberately fatal. The alternative — treating it as empty — silently
    unpublishes the entire history from the pipeline's point of view and
    re-uploads everything on the next run.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load(path: str) -> dict:
    """Read the registry. Missing file -> empty. Unreadable file -> raise."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RegistryCorrupt(
            f"{path} is not valid JSON ({exc}). Refusing to continue: treating it as "
            f"empty would republish everything. Restore it from git or from the "
            f"platform's own upload history before running again."
        ) from exc
    if not isinstance(data, dict):
        raise RegistryCorrupt(f"{path} does not contain a JSON object.")
    return data


def save(path: str, registry: dict) -> None:
    """Write the registry atomically.

    A plain open(path, "w") truncates first: a crash mid-write leaves a
    truncated file, which is the corruption case above. Writing to a temp file
    in the same directory and renaming makes the swap atomic on POSIX.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".registry-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(registry, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Never leave a stray temp file behind on failure.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def state_of(record: dict) -> str:
    """State of a record, defaulting to confirmed for pre-migration entries."""
    return record.get("state", CONFIRMED)


def is_settled(record: dict) -> bool:
    """True if this record should block a re-upload.

    Both confirmed and pending block. Pending blocks because the outcome is
    unknown, and an unknown outcome must not be resolved by trying again.
    """
    return state_of(record) in (CONFIRMED, PENDING)


class Registry:
    """A registry bound to one file, one platform.

    Every mutation is persisted immediately. That is more writes than strictly
    necessary, but the registry is tiny and the cost of losing one is a
    duplicate public post.
    """

    def __init__(self, path: str):
        self.path = path
        self.data = load(path)

    # ---- queries -------------------------------------------------------

    def published_keys(self) -> set:
        return {k for k, rec in self.data.items() if is_settled(rec)}

    def published_source_ids(self) -> set:
        return {
            rec.get("source_id")
            for rec in self.data.values()
            if is_settled(rec) and rec.get("source_id")
        }

    def already_handled(self, key: str, source_id: str | None = None) -> bool:
        """True if this item must not be uploaded again.

        Checks both the filename key and the stable source id, so an item that
        was regenerated under a new filename is still recognised.
        """
        rec = self.data.get(key)
        if rec is not None and is_settled(rec):
            return True
        return bool(source_id) and source_id in self.published_source_ids()

    def pending(self) -> dict:
        return {k: r for k, r in self.data.items() if state_of(r) == PENDING}

    # ---- transitions ---------------------------------------------------

    def begin(self, key: str, source_id: str | None = None, **meta) -> str:
        """Record the intent to upload, BEFORE touching the platform.

        Returns an attempt id. If the process dies after this point, the next
        run sees a pending record and knows the outcome is unknown rather than
        assuming nothing happened.
        """
        attempt_id = uuid.uuid4().hex
        record = dict(self.data.get(key) or {})
        record.update(meta)
        record.update({
            "state": PENDING,
            "attempt_id": attempt_id,
            "source_id": source_id,
            "startedAt": _now(),
        })
        self.data[key] = record
        save(self.path, self.data)
        return attempt_id

    def progress(self, key: str, **meta) -> None:
        """Annota un identificativo intermedio SENZA cambiare stato.

        Serve perche' un record 'pending' che contiene solo l'intenzione non e'
        riconciliabile: non abbiamo niente da chiedere alla piattaforma. Salvando
        appena disponibile l'id intermedio (container Instagram, publish_id
        TikTok) il recovery ha un appiglio concreto invece di dover indovinare.
        """
        record = dict(self.data.get(key) or {})
        record.update(meta)
        record["updatedAt"] = _now()
        self.data[key] = record
        save(self.path, self.data)

    def confirm(self, key: str, external_id: str, **meta) -> None:
        """Record that the platform accepted the upload."""
        record = dict(self.data.get(key) or {})
        record.update(meta)
        record.update({
            "state": CONFIRMED,
            "external_id": external_id,
            "confirmedAt": _now(),
        })
        record.pop("error", None)
        self.data[key] = record
        save(self.path, self.data)

    def fail(self, key: str, reason: str) -> None:
        """Record that the upload definitely did not happen.

        Only call this when the failure is KNOWN — a rejected request, a
        validation error. A timeout or a dropped connection is not a known
        failure: leave those pending so reconciliation can decide.
        """
        record = dict(self.data.get(key) or {})
        record.update({"state": FAILED, "error": reason[:500], "failedAt": _now()})
        self.data[key] = record
        save(self.path, self.data)

    # ---- recovery ------------------------------------------------------

    def reconcile(self, probe) -> list:
        """Resolve pending records left behind by a previous run.

        `probe(key, record)` asks the platform whether this item actually got
        published, and returns the external id if it did, None if it provably
        did not, or raises if it cannot tell.

        A probe that raises leaves the record pending — the item stays blocked,
        which is the safe direction. Returns a list of (key, outcome) for
        reporting.
        """
        outcomes = []
        for key, record in list(self.pending().items()):
            try:
                external_id = probe(key, record)
            except Exception as exc:  # probe failed: stay pending, stay blocked
                outcomes.append((key, f"unresolved ({type(exc).__name__})"))
                continue
            if external_id:
                self.confirm(key, external_id, recoveredAt=_now())
                outcomes.append((key, f"recovered as {external_id}"))
            else:
                # Provably absent on the platform: safe to clear and retry later.
                self.data.pop(key, None)
                save(self.path, self.data)
                outcomes.append((key, "cleared (never landed)"))
        return outcomes
