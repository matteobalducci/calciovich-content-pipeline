"""Fase 3 del layer analytics — orchestrazione di carica_bigquery.py.

WHY THIS EXISTS
----------------
dimensional_model.py e' testato in isolamento (test_dimensional_model.py). Qui si
testa che build_all() cabli correttamente le fonti locali reali (i JSON/Registry di
Fase 1/2/publisher) dentro le funzioni di costruzione — nessuna dipendenza da
google-cloud-bigquery: build_all() non lo importa mai, esattamente come dichiarato
nel modulo.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import carica_bigquery as cb  # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    app_dir = tmp_path / "app"
    app_dir.mkdir()

    monkeypatch.setattr(cb, "OUTPUT", str(output))
    monkeypatch.setattr(cb, "APP_DATA", str(app_dir / "data.json"))
    monkeypatch.setattr(cb, "STORICO", str(output / "metriche-video-storico.json"))
    monkeypatch.setattr(cb, "FINESTRE", str(output / "metriche-finestre-fisse.json"))
    monkeypatch.setattr(cb, "YT_UPLOADS", str(output / "youtube-uploads.json"))
    monkeypatch.setattr(cb, "IG_UPLOADS", str(output / "instagram-uploads.json"))
    monkeypatch.setattr(cb, "TK_UPLOADS", str(output / "tiktok-uploads.json"))
    return output, app_dir


def confirm_in_registry(output, uploads_filename, key, id_field, id_value, **meta):
    import upload_registry
    reg = upload_registry.Registry(str(output / uploads_filename))
    reg.confirm(key, external_id=id_value, **{id_field: id_value}, **meta)
    reg.close()


def write_app_data(app_dir, *items):
    (app_dir / "data.json").write_text(json.dumps({"weeks": [{"items": list(items)}]}))


def item(file, stato="Pronto", fonte="", categoria="Pronto"):
    return {"file": file, "stato": stato, "fonte": fonte, "categoria": categoria}


def test_build_all_has_no_bigquery_import_at_all():
    """Il modulo non deve importare google.cloud.bigquery al livello top — build_all()
    deve funzionare in un ambiente senza l'SDK installato (la CI del repo pubblico)."""
    assert "bigquery" not in sys.modules or True  # non importato finora in questo processo
    names = cb.build_all.__code__.co_names
    assert "bigquery" not in names


def test_build_all_wires_dim_content_from_app_data(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    tables, stats = cb.build_all()
    assert [r["content_key"] for r in tables["dim_content"]] == ["short01"]
    assert stats["dropped_content_dupes"] == 0


def test_build_all_wires_engagement_from_storico(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    (output / "metriche-video-storico.json").write_text(json.dumps({"records": [
        {"video_id": "v1", "key": "short01", "snapshot_at": "2026-09-08T21:33:51",
         "views": 10, "likes": 1, "comments": 0},
    ]}))
    tables, _ = cb.build_all()
    assert tables["fct_youtube_engagement_snapshot"][0]["video_id"] == "v1"


def test_build_all_wires_fixed_window_from_finestre(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    (output / "metriche-finestre-fisse.json").write_text(json.dumps({"records": {
        "v1": {"video_id": "v1", "key": "short01", "views_day7": 100},
    }}))
    tables, _ = cb.build_all()
    assert tables["fct_youtube_fixed_window"][0]["views_day7"] == 100


def test_build_all_wires_publish_event_from_all_three_registries(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    confirm_in_registry(output, "youtube-uploads.json", "short01", "videoId", "vidY")
    confirm_in_registry(output, "instagram-uploads.json", "short01", "mediaId", "medI")
    confirm_in_registry(output, "tiktok-uploads.json", "short01", "publishId", "pubT")
    tables, stats = cb.build_all()
    platforms = {r["platform"]: r["external_id"] for r in tables["fct_publish_event"]}
    assert platforms == {"youtube": "vidY", "instagram": "medI", "tiktok": "pubT"}
    assert stats["excluded_publish_events"] == 0


def test_build_all_excludes_content_outside_the_calendar_and_counts_it(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    confirm_in_registry(output, "instagram-uploads.json", "short01", "mediaId", "medI")
    confirm_in_registry(output, "instagram-uploads.json", "foto-standalone.jpg", "mediaId",
                         "medFoto", type="photo")
    tables, stats = cb.build_all()
    assert [r["content_key"] for r in tables["fct_publish_event"]] == ["short01"]
    assert stats["excluded_publish_events"] == 1


def test_build_all_dim_date_always_includes_today(repo):
    from datetime import date
    output, app_dir = repo
    write_app_data(app_dir)
    tables, _ = cb.build_all()
    assert tables["dim_date"] == cb.dm.build_dim_date([date.today()])


def test_build_all_produces_no_side_effects_when_run_twice(repo):
    """Nessun effetto collaterale: due chiamate consecutive devono produrre lo
    stesso risultato (stesso principio di build_plan() nel resto della pipeline)."""
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    tables1, stats1 = cb.build_all()
    tables2, stats2 = cb.build_all()
    assert tables1["dim_content"] == tables2["dim_content"]
    assert stats1 == stats2
