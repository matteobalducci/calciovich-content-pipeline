"""Tests for the crash-safe upload registry.

The interesting cases are the ones that only happen when something dies at a
bad moment, so most of these simulate a crash by simply not calling the next
step and then constructing a fresh Registry over the same file.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from upload_registry import (  # noqa: E402
    CONFIRMED,
    FAILED,
    PENDING,
    Registry,
    RegistryCorrupt,
    is_settled,
    load,
    save,
    state_of,
)


@pytest.fixture
def path(tmp_path):
    return str(tmp_path / "uploads.json")


# --- reading -------------------------------------------------------------


def test_missing_file_reads_as_empty(path):
    assert load(path) == {}


def test_corrupt_file_raises_instead_of_returning_empty(path):
    """The whole point: an unreadable registry must not look like 'nothing
    was ever published', which would republish the entire back catalogue."""
    with open(path, "w") as fh:
        fh.write('{"a": {"state": "confirmed"')  # truncated mid-write
    with pytest.raises(RegistryCorrupt):
        load(path)


def test_non_object_json_raises(path):
    with open(path, "w") as fh:
        json.dump(["not", "a", "dict"], fh)
    with pytest.raises(RegistryCorrupt):
        load(path)


def test_legacy_records_without_state_count_as_confirmed(path):
    """Records written before this module existed have no 'state' field."""
    with open(path, "w") as fh:
        json.dump({"ep1.mp4": {"videoId": "abc", "source_id": "s1"}}, fh)
    reg = Registry(path)
    assert state_of(reg.data["ep1.mp4"]) == CONFIRMED
    assert reg.already_handled("ep1.mp4")
    assert reg.already_handled("renamed.mp4", source_id="s1")


# --- writing -------------------------------------------------------------


def test_save_is_atomic_and_leaves_no_temp_files(path, tmp_path):
    save(path, {"a": {"state": CONFIRMED}})
    assert load(path) == {"a": {"state": CONFIRMED}}
    leftovers = [p for p in os.listdir(tmp_path) if p.startswith(".registry-")]
    assert leftovers == []


def test_save_does_not_destroy_previous_content_on_failure(path):
    """A failed write must leave the previous registry intact, not truncated."""
    save(path, {"a": {"state": CONFIRMED}})

    class Unserialisable:
        pass

    with pytest.raises(TypeError):
        save(path, {"b": Unserialisable()})
    assert load(path) == {"a": {"state": CONFIRMED}}


# --- the crash window ----------------------------------------------------


def test_begin_blocks_reupload_even_though_upload_never_confirmed(path):
    """The core scenario. begin() is called, the platform accepts the upload,
    and the process dies before confirm(). A fresh run must NOT republish."""
    reg = Registry(path)
    reg.begin("ep1.mp4", source_id="s1")
    # process dies here — no confirm()

    fresh = Registry(path)
    assert state_of(fresh.data["ep1.mp4"]) == PENDING
    assert fresh.already_handled("ep1.mp4"), "pending must block a re-upload"


def test_pending_blocks_by_source_id_too(path):
    reg = Registry(path)
    reg.begin("ep1-v1.mp4", source_id="s1")
    fresh = Registry(path)
    assert fresh.already_handled("ep1-v2.mp4", source_id="s1")


def test_confirm_promotes_and_records_external_id(path):
    reg = Registry(path)
    reg.begin("ep1.mp4", source_id="s1")
    reg.confirm("ep1.mp4", "yt-123", url="https://youtu.be/yt-123")

    fresh = Registry(path)
    rec = fresh.data["ep1.mp4"]
    assert rec["state"] == CONFIRMED
    assert rec["external_id"] == "yt-123"
    assert rec["url"] == "https://youtu.be/yt-123"
    assert rec["source_id"] == "s1"
    assert fresh.pending() == {}


def test_known_failure_does_not_block_a_later_retry(path):
    """A rejected request is a KNOWN non-event: the item must stay eligible."""
    reg = Registry(path)
    reg.begin("ep1.mp4", source_id="s1")
    reg.fail("ep1.mp4", "400 invalid video format")

    fresh = Registry(path)
    assert state_of(fresh.data["ep1.mp4"]) == FAILED
    assert not fresh.already_handled("ep1.mp4")
    assert not is_settled(fresh.data["ep1.mp4"])


# --- reconciliation ------------------------------------------------------


def test_reconcile_recovers_an_upload_that_actually_landed(path):
    reg = Registry(path)
    reg.begin("ep1.mp4", source_id="s1")

    fresh = Registry(path)
    outcomes = fresh.reconcile(lambda key, rec: "yt-999")

    assert fresh.data["ep1.mp4"]["state"] == CONFIRMED
    assert fresh.data["ep1.mp4"]["external_id"] == "yt-999"
    assert outcomes == [("ep1.mp4", "recovered as yt-999")]


def test_reconcile_clears_an_upload_that_provably_never_landed(path):
    reg = Registry(path)
    reg.begin("ep1.mp4", source_id="s1")

    fresh = Registry(path)
    fresh.reconcile(lambda key, rec: None)

    assert "ep1.mp4" not in fresh.data
    assert not fresh.already_handled("ep1.mp4", source_id="s1")


def test_reconcile_keeps_it_blocked_when_the_probe_cannot_tell(path):
    """If we cannot ask the platform — network down, quota exhausted — the
    item must stay pending and stay blocked. Publishing twice is worse than
    publishing late."""
    reg = Registry(path)
    reg.begin("ep1.mp4", source_id="s1")

    def broken_probe(key, rec):
        raise ConnectionError("API unreachable")

    fresh = Registry(path)
    outcomes = fresh.reconcile(broken_probe)

    assert fresh.data["ep1.mp4"]["state"] == PENDING
    assert fresh.already_handled("ep1.mp4")
    assert outcomes == [("ep1.mp4", "unresolved (ConnectionError)")]


def test_reconcile_ignores_confirmed_and_failed_records(path):
    reg = Registry(path)
    reg.begin("a.mp4", source_id="a")
    reg.confirm("a.mp4", "yt-a")
    reg.begin("b.mp4", source_id="b")
    reg.fail("b.mp4", "rejected")

    calls = []

    def probe(key, rec):
        calls.append(key)
        return None

    Registry(path).reconcile(probe)
    assert calls == []


def test_reconcile_is_idempotent_across_repeated_runs(path):
    reg = Registry(path)
    reg.begin("ep1.mp4", source_id="s1")

    for _ in range(3):
        fresh = Registry(path)
        fresh.reconcile(lambda key, rec: "yt-1")

    final = Registry(path)
    assert final.data["ep1.mp4"]["external_id"] == "yt-1"
    assert final.pending() == {}
