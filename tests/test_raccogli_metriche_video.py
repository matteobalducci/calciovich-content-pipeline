"""Fase 1 del layer analytics — logger storico delle metriche per-video.

WHY THIS EXISTS
----------------
Due rischi concreti guidano questi test: (1) due esecuzioni sovrapposte dello
script possono interlacciare le scritture sullo storico — questo repo ha gia'
un incidente documentato con lo stesso pattern I/O in aggiorna_youtube_stats.py,
da cui il lock; (2) lo storico deve restare deduplicato su (video_id, snapshot_at)
anche se lo stesso run viene rilanciato per errore.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import raccogli_metriche_video as rmv  # noqa: E402
import metriche_video  # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Un output/ isolato: youtube-uploads.json + app/data.json fittizi, con i
    percorsi dei moduli reindirizzati li'."""
    output = tmp_path / "output"
    output.mkdir()
    app_dir = tmp_path / "app"
    app_dir.mkdir()

    monkeypatch.setattr(rmv, "OUTPUT", str(output))
    monkeypatch.setattr(rmv, "YT_UPLOADS", str(output / "youtube-uploads.json"))
    monkeypatch.setattr(rmv, "YT_UPLOADS_NEW", str(output / "youtube-calciovich-uploads.json"))
    monkeypatch.setattr(rmv, "STORICO", str(output / "metriche-video-storico.json"))
    monkeypatch.setattr(rmv, "STORICO_NEW", str(output / "metriche-video-storico-calciovich.json"))
    monkeypatch.setattr(metriche_video, "APP_DATA", str(app_dir / "data.json"))
    return output


def write_uploads(repo, uploads):
    (repo / "youtube-uploads.json").write_text(json.dumps(uploads))


def confirm_in_registry(repo, key, video_id, **meta):
    """Simula un upload confermato DOPO la migrazione a SQLite (827ba65, 02/09):
    mai scritto nel JSON legacy, solo in publish-state.db — esattamente lo
    scenario che load_confirmed_youtube_uploads() deve vedere."""
    import upload_registry
    reg = upload_registry.Registry(str(repo / "youtube-uploads.json"))
    reg.confirm(key, external_id=video_id, videoId=video_id, **meta)
    reg.close()


# --- bugfix dibattito di controllo Fase 3: fonte SQLite, non solo JSON -----


def test_build_plan_sees_an_upload_confirmed_only_in_sqlite_not_in_the_legacy_json(repo):
    confirm_in_registry(repo, "short99-nuovo.vert", "vidNEW")
    assert not (repo / "youtube-uploads.json").exists()
    stats = {"vidNEW": {"views": 5, "likes": 0, "comments": 0, "privacy": "public"}}
    _, rows = rmv.build_plan(stats_override=stats)
    assert [r["video_id"] for r in rows] == ["vidNEW"]


# --- build_plan(): filtro pubblico + forma della riga -----------------------


def test_build_plan_skips_videos_not_yet_public(repo):
    write_uploads(repo, {
        "short01-x.vert": {"videoId": "vid1"},
        "short02-x.vert": {"videoId": "vid2"},
    })
    stats = {
        "vid1": {"views": 10, "likes": 1, "comments": 0, "privacy": "public"},
        "vid2": {"views": 0, "likes": 0, "comments": 0, "privacy": "private"},
    }
    _, rows = rmv.build_plan(stats_override=stats)
    assert [r["video_id"] for r in rows] == ["vid1"]


def test_build_plan_row_shape_and_categoria(repo):
    write_uploads(repo, {"short01-x.vert": {"videoId": "vid1"}})
    stats = {"vid1": {"views": 42, "likes": 3, "comments": 2, "privacy": "public"}}
    snapshot_at, rows = rmv.build_plan(stats_override=stats)
    assert rows == [{
        "video_id": "vid1", "key": "short01-x.vert", "categoria": "canonical",
        "snapshot_at": snapshot_at, "views": 42, "likes": 3, "comments": 2,
        "channel_key": "gol-impossibili",
    }]


def test_build_plan_shares_one_snapshot_at_across_all_rows_of_the_same_run(repo):
    write_uploads(repo, {
        "short01-x.vert": {"videoId": "vid1"},
        "short02-x.vert": {"videoId": "vid2"},
    })
    stats = {
        "vid1": {"views": 1, "likes": 0, "comments": 0, "privacy": "public"},
        "vid2": {"views": 2, "likes": 0, "comments": 0, "privacy": "public"},
    }
    _, rows = rmv.build_plan(stats_override=stats)
    assert len({r["snapshot_at"] for r in rows}) == 1


def test_build_plan_skips_uploads_with_no_video_id(repo):
    write_uploads(repo, {"pending-x.vert": {}})
    _, rows = rmv.build_plan(stats_override={})
    assert rows == []


# --- merge_records(): dedup su (video_id, snapshot_at) ----------------------


def test_merge_records_appends_new_rows():
    merged, added = rmv.merge_records([], [
        {"video_id": "a", "snapshot_at": "t1", "views": 1},
    ])
    assert added == 1
    assert merged[0]["video_id"] == "a"


def test_merge_records_deduplicates_the_same_video_and_snapshot():
    existing = [{"video_id": "a", "snapshot_at": "t1", "views": 1}]
    new_rows = [{"video_id": "a", "snapshot_at": "t1", "views": 999}]
    merged, added = rmv.merge_records(existing, new_rows)
    assert added == 0
    assert merged == existing  # la riga vecchia non viene sovrascritta da un rerun


def test_merge_records_keeps_two_snapshots_of_the_same_video():
    existing = [{"video_id": "a", "snapshot_at": "t1", "views": 1}]
    new_rows = [{"video_id": "a", "snapshot_at": "t2", "views": 2}]
    merged, added = rmv.merge_records(existing, new_rows)
    assert added == 1
    assert len(merged) == 2


# --- collection_lock(): il rischio di scrittori concorrenti -----------------


def test_collection_lock_refuses_a_second_concurrent_acquisition(repo, monkeypatch):
    with rmv.collection_lock():
        with pytest.raises(SystemExit):
            with rmv.collection_lock():
                pass  # non deve mai arrivare qui: il lock deve bloccare prima


def test_collection_lock_releases_cleanly_for_the_next_run(repo):
    with rmv.collection_lock():
        pass
    with rmv.collection_lock():
        pass  # non deve sollevare: il lock precedente e' stato rilasciato


# --- token scaduto: degradare, non crashare su un LaunchAgent non presidiato ------


def test_an_expired_token_degrades_cleanly_instead_of_crashing(repo, monkeypatch):
    """youtube_token.json e' condiviso con carica_youtube.py: se scade, questo script
    non puo' riaprire un browser (gira non presidiato via LaunchAgent). Deve saltare
    il run senza propagare un traceback grezzo nel log.

    Richiede google-auth: la CI del repo pubblico gemello gira apposta senza SDK/
    credenziali (solo logica pura) — questo test gira dove il pacchetto e' installato
    (questo repo di produzione)."""
    google_auth_exceptions = pytest.importorskip("google.auth.exceptions")
    RefreshError = google_auth_exceptions.RefreshError

    def boom(*a, **kw):
        raise RefreshError("token scaduto")

    monkeypatch.setattr(rmv, "build_plan", boom)
    assert rmv._build_plan_or_none() is None


def test_a_working_token_returns_the_plan_unchanged(repo, monkeypatch):
    sentinel = ("t1", [{"video_id": "a"}])
    monkeypatch.setattr(rmv, "build_plan", lambda **kw: sentinel)
    assert rmv._build_plan_or_none() == sentinel


# ---------------------------------------------------------------- one collection per channel (06/10/2026)

def confirm_new(repo, key, video_id):
    import upload_registry
    reg = upload_registry.Registry(str(repo / "youtube-calciovich-uploads.json"))
    reg.confirm(key, external_id=video_id, videoId=video_id)
    reg.close()


def source(name):
    return next(s for s in rmv.sources() if s["profile"].key == name)


def test_each_channel_reads_only_its_own_registry_and_labels_its_rows(repo):
    confirm_in_registry(repo, "short01-x.vert", "vOLD")
    confirm_new(repo, "short01-x.vert", "vNEW")                       # same content, republished
    stats = {"vOLD": {"views": 5, "likes": 0, "comments": 0, "privacy": "public"},
             "vNEW": {"views": 2, "likes": 0, "comments": 0, "privacy": "public"}}
    _, old_rows = rmv.build_plan(stats_override=stats, source=source("gol-impossibili"))
    _, new_rows = rmv.build_plan(stats_override=stats, source=source("calciovich"))
    assert [(r["video_id"], r["channel_key"]) for r in old_rows] == [("vOLD", "gol-impossibili")]
    assert [(r["video_id"], r["channel_key"]) for r in new_rows] == [("vNEW", "calciovich")]


def test_a_scheduled_private_copy_on_the_new_channel_is_not_recorded_yet(repo):
    confirm_new(repo, "short02-x.vert", "vSCHED")
    stats = {"vSCHED": {"views": 0, "likes": 0, "comments": 0, "privacy": "private"}}
    _, rows = rmv.build_plan(stats_override=stats, source=source("calciovich"))
    assert rows == []


def test_histories_are_written_to_separate_files(repo, monkeypatch):
    confirm_in_registry(repo, "short01-x.vert", "vOLD")
    confirm_new(repo, "short01-x.vert", "vNEW")
    stats = {"vOLD": {"views": 5, "likes": 0, "comments": 0, "privacy": "public"},
             "vNEW": {"views": 2, "likes": 0, "comments": 0, "privacy": "public"}}
    real = rmv.build_plan
    monkeypatch.setattr(rmv, "build_plan", lambda **kw: real(stats_override=stats, **kw))
    monkeypatch.setattr(sys, "argv", ["raccogli_metriche_video.py"])
    assert rmv.main() == 0
    old = json.load(open(repo / "metriche-video-storico.json"))["records"]
    new = json.load(open(repo / "metriche-video-storico-calciovich.json"))["records"]
    assert {r["video_id"] for r in old} == {"vOLD"} and {r["video_id"] for r in new} == {"vNEW"}
    assert {r["channel_key"] for r in old} == {"gol-impossibili"} and {r["channel_key"] for r in new} == {"calciovich"}


def test_no_file_is_created_for_a_channel_with_nothing_public_yet(repo, monkeypatch):
    confirm_in_registry(repo, "short01-x.vert", "vOLD")
    stats = {"vOLD": {"views": 5, "likes": 0, "comments": 0, "privacy": "public"}}
    real = rmv.build_plan
    monkeypatch.setattr(rmv, "build_plan", lambda **kw: real(stats_override=stats, **kw))
    monkeypatch.setattr(sys, "argv", ["raccogli_metriche_video.py"])
    rmv.main()
    assert not (repo / "metriche-video-storico-calciovich.json").exists()


def test_one_channel_failing_does_not_stop_the_other_but_the_exit_code_says_so(repo, monkeypatch, capsys):
    confirm_in_registry(repo, "short01-x.vert", "vOLD")
    confirm_new(repo, "short01-x.vert", "vNEW")
    stats = {"vOLD": {"views": 5, "likes": 0, "comments": 0, "privacy": "public"}}
    real = rmv.build_plan

    def flaky(**kw):
        if kw["source"]["profile"].key == "calciovich":
            raise rmv.ChannelMismatch("il token appartiene a ['UCaltro'], atteso UCnuovo")
        return real(stats_override=stats, **kw)
    monkeypatch.setattr(rmv, "build_plan", flaky)
    monkeypatch.setattr(sys, "argv", ["raccogli_metriche_video.py"])
    assert rmv.main() == 1
    assert json.load(open(repo / "metriche-video-storico.json"))["records"][0]["video_id"] == "vOLD"
    assert not (repo / "metriche-video-storico-calciovich.json").exists()
    assert "nessuna riga scritta" in capsys.readouterr().out


class FakeChannels:
    def __init__(self, ids):
        self.ids = ids

    def list(self, **kw):
        assert kw.get("mine") is True
        return self

    def execute(self):
        return {"items": [{"id": i} for i in self.ids]}


class FakeYT:
    def __init__(self, ids):
        self._c = FakeChannels(ids)

    def channels(self):
        return self._c


def test_owner_guard_accepts_the_right_channel():
    metriche_video.check_owner(FakeYT(["UCok"]), "UCok")


@pytest.mark.parametrize("owned", [["UCaltro"], []])
def test_owner_guard_refuses_a_token_of_another_channel_or_of_no_channel(owned):
    with pytest.raises(metriche_video.ChannelMismatch):
        metriche_video.check_owner(FakeYT(owned), "UCok")
