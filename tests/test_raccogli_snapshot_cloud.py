"""raccogli_snapshot_cloud.py — convenzione oraria di snapshot_at.

WHY THIS EXISTS: il Mac scrive snapshot_at come ora locale italiana senza fuso; un
runner GitHub e' in UTC. Con datetime.now() nudo le due scritture avevano convenzioni
diverse nella stessa colonna e il filtro per stringa scartava un giro cloud riuscito
(run "success" con 0 righe scritte, 2026-10-03).
"""
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import raccogli_snapshot_cloud as rsc  # noqa: E402


def test_snapshot_at_has_the_same_naive_iso_format_as_the_mac_side():
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", rsc._snapshot_at_now())


def test_snapshot_at_is_rome_wall_clock_not_utc():
    got = datetime.fromisoformat(rsc._snapshot_at_now())
    rome = datetime.now(ZoneInfo("Europe/Rome")).replace(tzinfo=None)
    assert abs((rome - got).total_seconds()) < 5


# ---------------------------------------------------------------- two channels (06/10/2026)

class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def result(self):
        return self._rows


class FakeClient:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def query(self, sql):
        self.sql.append(sql)
        return FakeResult(self.rows)


def test_the_cloud_job_selects_only_the_original_channels_videos():
    client = FakeClient([{"video_id": "vOLD", "content_key": "k"}])
    got = rsc._known_video_content_keys(client, None)
    sql = client.sql[0]
    assert got == {"vOLD": "k"}
    assert "COALESCE(channel_key, 'gol-impossibili') = 'gol-impossibili'" in sql    # legacy NULL rows are the original channel


def test_the_cloud_job_labels_its_rows_with_the_original_channel_and_checks_ownership(monkeypatch):
    import carica_bigquery as cb
    written = {}
    seen = {}

    class FakeBQ:
        pass

    monkeypatch.setattr(rsc.cb, "_bigquery_client", lambda *a, **k: object())
    monkeypatch.setattr(rsc.os.path, "exists", lambda p: True)
    monkeypatch.setattr(rsc, "_known_video_content_keys", lambda c, b: {"vOLD": "k"})

    def fake_fetch(ids, **kw):
        seen.update(kw)
        return {"vOLD": {"views": 5, "likes": 1, "comments": 0, "privacy": "public"}}
    monkeypatch.setattr(rsc, "fetch_stats", fake_fetch)
    monkeypatch.setattr(rsc.cb, "_existing_engagement_keys", lambda c: set())
    monkeypatch.setattr(rsc.cb, "_ensure_table", lambda *a, **k: None)
    monkeypatch.setattr(rsc.cb, "_schemas", lambda b: {"fct_youtube_engagement_snapshot": []})
    monkeypatch.setattr(rsc.cb, "_write_table",
                        lambda c, b, name, rows, schema, write_disposition: written.update(rows=rows, mode=write_disposition))
    import types
    fake_modules = {"google.cloud": types.SimpleNamespace(bigquery=FakeBQ),
                    "google.oauth2": types.SimpleNamespace(service_account=object())}
    monkeypatch.setitem(sys.modules, "google.cloud", fake_modules["google.cloud"])
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", FakeBQ)
    monkeypatch.setitem(sys.modules, "google.oauth2", fake_modules["google.oauth2"])
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", object())
    rsc.main()
    assert written["mode"] == "WRITE_APPEND"
    assert [r["channel_key"] for r in written["rows"]] == ["gol-impossibili"]
    assert seen["expect_channel_id"] == cb.CHANNEL_IDS["gol-impossibili"]            # wrong-channel secret is refused
