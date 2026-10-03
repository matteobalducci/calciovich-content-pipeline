"""Controllo di ritmo di pubblicazione (stato_pipeline._cadence_flag & co).

WHY THIS EXISTS: il vecchio flag leggeva la coda delle clip AI (ferma al 05/08) e
segnalava "59 giorni senza attivita'" su un canale che pubblicava ogni giorno
(2026-10-03). Il nuovo legge il registro vivo delle pubblicazioni; qui si fissa che
(a) non avvisi quando il ritmo e' sano, (b) avvisi quando uno slot giornaliero salta
e (c) la chiave del watchdog non cambi a ogni controllo.
"""
import datetime
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stato_pipeline as sp  # noqa: E402

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 10, 3, 15, 0, tzinfo=UTC)


def hrs(h):
    return NOW - datetime.timedelta(hours=h)


def test_healthy_daily_rhythm_has_no_flag():
    flag, last = sp._cadence_flag("YouTube", [hrs(26), hrs(50)], NOW)
    assert flag is None and last == hrs(26)


def test_first_missed_daily_slot_warns():
    flag, _ = sp._cadence_flag("YouTube", [hrs(31)], NOW)
    assert flag["level"] == "warn" and "31 ore" in flag["text"]


def test_second_missed_slot_is_an_error():
    flag, _ = sp._cadence_flag("YouTube", [hrs(55)], NOW)
    assert flag["level"] == "error"


def test_a_video_scheduled_within_the_grace_window_suppresses_the_warning():
    soon = NOW + datetime.timedelta(hours=3)
    assert sp._cadence_flag("YouTube", [hrs(33), soon], NOW)[0] is None


def test_a_video_scheduled_far_in_the_future_does_not_suppress_it():
    far = NOW + datetime.timedelta(hours=40)
    assert sp._cadence_flag("YouTube", [hrs(33), far], NOW)[0]["level"] == "warn"


def test_no_public_content_at_all_warns():
    flag, last = sp._cadence_flag("Instagram", [NOW + datetime.timedelta(hours=5)], NOW)
    assert flag["level"] == "warn" and last is None


def test_youtube_private_without_publishat_is_not_public():
    assert sp._public_time("youtube", "k", "2026-10-01T10:00:00+00:00", {"privacy": "private"}) is None


def test_youtube_uses_publishat_when_scheduled():
    t = sp._public_time("youtube", "k", "2026-10-01T10:00:00+00:00",
                        {"privacy": "private", "publishAt": "2026-10-02T13:00:00Z"})
    assert t == datetime.datetime(2026, 10, 2, 13, 0, tzinfo=UTC)


def test_instagram_stories_do_not_count_as_a_publication():
    assert sp._public_time("instagram", "stories/2026-10-03/x.mp4", "2026-10-03T14:00:00+00:00", {}) is None
    assert sp._public_time("instagram", "short41.vert", "2026-10-03T14:00:00+00:00", {}) is not None


def test_publish_times_reads_only_confirmed_rows_from_the_registry(tmp_path, monkeypatch):
    db = tmp_path / "publish-state.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE uploads (platform TEXT, key TEXT, state TEXT, source_id TEXT, "
              "external_id TEXT, attempt_id TEXT, started_at TEXT, updated_at TEXT, meta TEXT)")
    rows = [
        ("youtube", "a", "confirmed", json.dumps({"publishAt": "2026-10-02T13:00:00Z", "privacy": "private"})),
        ("youtube", "b", "failed", json.dumps({"publishAt": "2026-10-03T13:00:00Z"})),
        ("tiktok", "c", "confirmed", "{}"),
    ]
    for plat, key, state, meta in rows:
        c.execute("INSERT INTO uploads VALUES (?,?,?,?,?,?,?,?,?)",
                  (plat, key, state, None, None, None, "2026-10-01T00:00:00+00:00",
                   "2026-10-01T00:00:00+00:00", meta))
    c.commit()
    c.close()
    monkeypatch.setattr(sp, "PUBLISH_DB", str(db))
    assert sp._publish_times("youtube") == [datetime.datetime(2026, 10, 2, 13, 0, tzinfo=UTC)]


def test_unreadable_registry_becomes_a_visible_warning_not_silence(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "PUBLISH_DB", str(tmp_path / "missing.db"))
    flags, last = sp._publish_cadence_flags(NOW)
    assert last is None and len(flags) == 2 and all(f["level"] == "warn" for f in flags)


def test_flag_key_ignores_changing_numbers_but_keeps_the_level():
    a = sp.flag_key("warn", "YouTube: nessun nuovo video pubblico da 31 ore (ultimo: 02/10 15:00)")
    b = sp.flag_key("warn", "YouTube: nessun nuovo video pubblico da 32 ore (ultimo: 02/10 15:00)")
    assert a == b
    assert a != sp.flag_key("error", "YouTube: nessun nuovo video pubblico da 55 ore (ultimo: 02/10 15:00)")
