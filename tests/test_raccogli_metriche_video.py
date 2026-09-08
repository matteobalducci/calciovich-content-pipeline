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
    monkeypatch.setattr(rmv, "STORICO", str(output / "metriche-video-storico.json"))
    monkeypatch.setattr(metriche_video, "APP_DATA", str(app_dir / "data.json"))
    return output


def write_uploads(repo, uploads):
    (repo / "youtube-uploads.json").write_text(json.dumps(uploads))


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

    Richiede google-auth: la CI di questo repo pubblico gira apposta senza SDK/
    credenziali (solo logica pura, vedi .github/workflows/tests.yml) — questo test
    gira dove il pacchetto e' installato (il repo privato di produzione)."""
    google_auth_exceptions = pytest.importorskip("google.auth.exceptions")
    RefreshError = google_auth_exceptions.RefreshError

    def boom(*a, **kw):
        raise RefreshError("token scaduto")

    monkeypatch.setattr(rmv, "build_plan", boom)
    assert rmv._build_plan_or_none() is None


def test_a_working_token_returns_the_plan_unchanged(repo, monkeypatch):
    sentinel = ("t1", [{"video_id": "a"}])
    monkeypatch.setattr(rmv, "build_plan", lambda: sentinel)
    assert rmv._build_plan_or_none() == sentinel
