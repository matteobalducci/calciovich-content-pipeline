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


# ---------------------------------------------------------------- two channels (06/10 -> cloud 09/10/2026)

import types  # noqa: E402

import pytest  # noqa: E402

import carica_bigquery as cb  # noqa: E402
from metriche_video import ChannelMismatch  # noqa: E402

OLD, NEW = "gol-impossibili", "calciovich"
PUBLIC = {"views": 5, "likes": 1, "comments": 0, "privacy": "public"}


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


def test_each_channel_selects_only_its_own_videos():
    client = FakeClient([{"video_id": "v1", "content_key": "k"}])
    assert rsc._known_video_content_keys(client, None, OLD) == {"v1": "k"}
    assert rsc._known_video_content_keys(client, None, NEW) == {"v1": "k"}
    assert "COALESCE(channel_key, 'gol-impossibili') = 'gol-impossibili'" in client.sql[0]   # NULL storici = canale originale
    assert "channel_key = 'calciovich'" in client.sql[1] and "COALESCE" not in client.sql[1]


def test_an_unknown_channel_is_refused():
    with pytest.raises(ValueError):
        rsc._known_video_content_keys(FakeClient([]), None, "altro")


def _stub_cloud_main(monkeypatch, known, stats, tokens=(OLD, NEW), existing=frozenset()):
    """Tutto cio' che main() tocca fuori dal proprio codice. known/stats: {canale: ...}; stats puo' essere
    un'eccezione da sollevare per quel canale. tokens: canali il cui file token 'esiste' nel checkout."""
    written, seen = {}, {}
    token_files = {rsc.CHANNEL_TOKENS[k][0] for k in tokens}

    class FakeBQ:
        pass

    monkeypatch.setattr(rsc.cb, "_bigquery_client", lambda *a, **k: object())
    monkeypatch.setattr(rsc.os.path, "exists",
                        lambda p: p == rsc.cb.KEY_PATH or os.path.basename(p) in token_files)
    def fake_known(c, b, ch=OLD):
        result = known.get(ch, {})
        if isinstance(result, BaseException):
            raise result
        return result
    monkeypatch.setattr(rsc, "_known_video_content_keys", fake_known)

    def fake_fetch(ids, **kw):
        channel = {v: k for k, v in cb.CHANNEL_IDS.items()}[kw["expect_channel_id"]]
        seen[channel] = kw
        result = stats[channel]
        if isinstance(result, BaseException):
            raise result
        return result
    monkeypatch.setattr(rsc, "fetch_stats", fake_fetch)
    monkeypatch.setattr(rsc.cb, "_existing_engagement_keys", lambda c: set(existing))
    monkeypatch.setattr(rsc.cb, "_ensure_table", lambda *a, **k: None)
    monkeypatch.setattr(rsc.cb, "_schemas", lambda b: {"fct_youtube_engagement_snapshot": []})
    monkeypatch.setattr(rsc.cb, "_write_table",
                        lambda c, b, name, rows, schema, write_disposition: written.update(rows=rows, mode=write_disposition))
    monkeypatch.setitem(sys.modules, "google.cloud", types.SimpleNamespace(bigquery=FakeBQ))
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", FakeBQ)
    monkeypatch.setitem(sys.modules, "google.oauth2", types.SimpleNamespace(service_account=object()))
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", object())
    return written, seen


def test_both_channels_are_collected_with_their_own_label_and_ownership_check(monkeypatch):
    written, seen = _stub_cloud_main(
        monkeypatch, {OLD: {"vOLD": "kO"}, NEW: {"vNEW": "kN"}}, {OLD: {"vOLD": PUBLIC}, NEW: {"vNEW": PUBLIC}})
    rsc.main()
    assert written["mode"] == "WRITE_APPEND"
    assert {r["video_id"]: r["channel_key"] for r in written["rows"]} == {"vOLD": OLD, "vNEW": NEW}
    assert seen[OLD]["expect_channel_id"] == cb.CHANNEL_IDS[OLD]             # un token sbagliato viene rifiutato
    assert seen[NEW]["expect_channel_id"] == cb.CHANNEL_IDS[NEW]
    assert seen[OLD]["token_path"] != seen[NEW]["token_path"]               # un token per canale, mai condiviso


def test_the_optional_channel_without_a_token_is_skipped_with_a_warning_not_an_error(monkeypatch, capsys):
    written, seen = _stub_cloud_main(monkeypatch, {OLD: {"vOLD": "kO"}}, {OLD: {"vOLD": PUBLIC}}, tokens=(OLD,))
    rsc.main()                                                                # nessun SystemExit
    assert [r["channel_key"] for r in written["rows"]] == [OLD] and NEW not in seen
    assert "::warning::" in capsys.readouterr().out


def test_the_required_channel_without_a_token_fails_but_the_other_channel_is_still_written(monkeypatch):
    written, _ = _stub_cloud_main(monkeypatch, {NEW: {"vNEW": "kN"}}, {NEW: {"vNEW": PUBLIC}}, tokens=(NEW,))
    with pytest.raises(SystemExit) as exc:
        rsc.main()
    assert OLD in str(exc.value) and "assente" in str(exc.value)
    assert [r["channel_key"] for r in written["rows"]] == [NEW]               # i dati sani non si perdono


def test_a_wrong_owner_token_on_one_channel_fails_loudly_and_does_not_block_the_other(monkeypatch):
    written, _ = _stub_cloud_main(
        monkeypatch, {OLD: {"vOLD": "kO"}, NEW: {"vNEW": "kN"}},
        {OLD: {"vOLD": PUBLIC}, NEW: ChannelMismatch("il token appartiene a ['UCsbagliato']")})
    with pytest.raises(SystemExit) as exc:
        rsc.main()
    assert NEW in str(exc.value) and "ChannelMismatch" in str(exc.value)
    assert [r["channel_key"] for r in written["rows"]] == [OLD]


@pytest.mark.parametrize("stats", [
    {},                                                                       # l'API non restituisce nessun video
    {"vOLD": {"views": 5, "likes": 1, "comments": 0, "privacy": "private"}},  # tutti non pubblici
])
def test_known_videos_but_no_publishable_row_fails_instead_of_a_green_empty_run(monkeypatch, stats):
    """Un run verde deve significare righe scritte (e' cosi' che il bug dei fusi passo' inosservato)."""
    written, _ = _stub_cloud_main(monkeypatch, {OLD: {"vOLD": "kO"}}, {OLD: stats}, tokens=(OLD,))
    with pytest.raises(SystemExit) as exc:
        rsc.main()
    assert "nessun video pubblico" in str(exc.value) and not written


def test_nothing_new_to_write_fails_instead_of_a_green_empty_run(monkeypatch):
    snapshot = "2026-10-09T10:00:00"
    monkeypatch.setattr(rsc, "_snapshot_at_now", lambda: snapshot)
    written, _ = _stub_cloud_main(monkeypatch, {OLD: {"vOLD": "kO"}}, {OLD: {"vOLD": PUBLIC}}, tokens=(OLD,),
                                  existing={(OLD, "vOLD", snapshot)})
    with pytest.raises(SystemExit) as exc:
        rsc.main()
    assert "nessuna riga scritta" in str(exc.value) and not written


def test_a_bigquery_error_on_one_channel_does_not_stop_the_other_but_fails_the_job(monkeypatch):
    written, seen = _stub_cloud_main(
        monkeypatch, {OLD: RuntimeError("BigQuery: 503"), NEW: {"vNEW": "kN"}}, {OLD: {}, NEW: {"vNEW": PUBLIC}})
    with pytest.raises(SystemExit) as exc:
        rsc.main()
    assert OLD in str(exc.value) and "RuntimeError" in str(exc.value)
    assert [r["channel_key"] for r in written["rows"]] == [NEW]               # il canale sano e' stato scritto
    assert OLD not in seen                                                    # e il canale rotto non ha letto nulla


def test_the_book_channel_with_a_token_but_no_known_videos_is_a_problem_not_a_silent_success(monkeypatch):
    written, _ = _stub_cloud_main(monkeypatch, {OLD: {"vOLD": "kO"}, NEW: {}}, {OLD: {"vOLD": PUBLIC}, NEW: {}})
    with pytest.raises(SystemExit) as exc:
        rsc.main()
    assert NEW in str(exc.value) and "nessun video noto" in str(exc.value)
    assert [r["channel_key"] for r in written["rows"]] == [OLD]


def test_one_channel_with_nothing_new_fails_even_if_the_other_channel_wrote(monkeypatch):
    """Il controllo 'zero righe scritte' e' per canale: un canale che non scrive nulla di nuovo non si nasconde
    dietro l'altro che ha scritto."""
    snapshot = "2026-10-09T10:00:00"
    monkeypatch.setattr(rsc, "_snapshot_at_now", lambda: snapshot)
    written, _ = _stub_cloud_main(
        monkeypatch, {OLD: {"vOLD": "kO"}, NEW: {"vNEW": "kN"}}, {OLD: {"vOLD": PUBLIC}, NEW: {"vNEW": PUBLIC}},
        existing={(OLD, "vOLD", snapshot)})                                   # il vecchio canale e' gia' scritto
    with pytest.raises(SystemExit) as exc:
        rsc.main()
    assert OLD in str(exc.value) and "nessuna riga scritta" in str(exc.value) and NEW not in str(exc.value)
    assert [r["channel_key"] for r in written["rows"]] == [NEW]                # l'altro canale e' stato scritto
