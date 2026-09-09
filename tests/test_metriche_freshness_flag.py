"""Guardia di freschezza per raccogli_metriche_video.py.

WHY THIS EXISTS
----------------
Questo repo ha gia' avuto due buchi silenziosi di raccolta dati (23 giorni, poi
11 giorni) prima che qualcuno se ne accorgesse. La guardia deve accorgersi da
sola — a grana-ore, non a grana-giorno come gli altri controlli in questo file,
perche' a 4 raccolte/giorno un controllo giornaliero nasconderebbe quasi 24h di
buco prima di segnalarlo.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stato_pipeline  # noqa: E402


def write_storico(path, snapshot_ats):
    path.write_text(json.dumps({
        "records": [{"video_id": f"v{i}", "snapshot_at": s} for i, s in enumerate(snapshot_ats)]
    }))


def test_no_storico_file_is_a_warning_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(stato_pipeline, "METRICHE_STORICO_PATH", str(tmp_path / "missing.json"))
    flag = stato_pipeline._metriche_freshness_flag()
    assert flag["level"] == "warn"
    assert "nessuna metrica" in flag["text"].lower()


def test_a_recent_snapshot_raises_no_flag(tmp_path, monkeypatch):
    storico = tmp_path / "storico.json"
    recent = (datetime.datetime.now() - datetime.timedelta(hours=2)).isoformat(timespec="seconds")
    write_storico(storico, [recent])
    monkeypatch.setattr(stato_pipeline, "METRICHE_STORICO_PATH", str(storico))
    assert stato_pipeline._metriche_freshness_flag() is None


def test_a_snapshot_older_than_36_hours_warns(tmp_path, monkeypatch):
    storico = tmp_path / "storico.json"
    stale = (datetime.datetime.now() - datetime.timedelta(hours=40)).isoformat(timespec="seconds")
    write_storico(storico, [stale])
    monkeypatch.setattr(stato_pipeline, "METRICHE_STORICO_PATH", str(storico))
    flag = stato_pipeline._metriche_freshness_flag()
    assert flag["level"] == "warn"


def test_a_snapshot_older_than_72_hours_errors(tmp_path, monkeypatch):
    storico = tmp_path / "storico.json"
    very_stale = (datetime.datetime.now() - datetime.timedelta(hours=80)).isoformat(timespec="seconds")
    write_storico(storico, [very_stale])
    monkeypatch.setattr(stato_pipeline, "METRICHE_STORICO_PATH", str(storico))
    flag = stato_pipeline._metriche_freshness_flag()
    assert flag["level"] == "error"


def test_uses_the_most_recent_of_several_snapshots(tmp_path, monkeypatch):
    storico = tmp_path / "storico.json"
    old = (datetime.datetime.now() - datetime.timedelta(hours=80)).isoformat(timespec="seconds")
    recent = (datetime.datetime.now() - datetime.timedelta(hours=1)).isoformat(timespec="seconds")
    write_storico(storico, [old, recent])
    monkeypatch.setattr(stato_pipeline, "METRICHE_STORICO_PATH", str(storico))
    assert stato_pipeline._metriche_freshness_flag() is None


def test_compute_status_surfaces_the_freshness_flag_in_the_dashboard(tmp_path, monkeypatch):
    """Verifica che il flag arrivi davvero fino a compute_status(), letto da
    app_server.py per la dashboard — non solo che la funzione isolata funzioni."""
    storico = tmp_path / "storico.json"
    very_stale = (datetime.datetime.now() - datetime.timedelta(hours=80)).isoformat(timespec="seconds")
    write_storico(storico, [very_stale])
    monkeypatch.setattr(stato_pipeline, "METRICHE_STORICO_PATH", str(storico))
    monkeypatch.setattr(stato_pipeline, "ANALYTICS_CONSENT_STATUS_PATH", str(tmp_path / "no-consent.json"))
    monkeypatch.setattr(stato_pipeline, "FINESTRE_FISSE_PATH", str(tmp_path / "no-finestre.json"))
    monkeypatch.setattr(stato_pipeline, "QUEUE_PATH", str(tmp_path / "no-queue.json"))
    monkeypatch.setattr(stato_pipeline, "IG_UPLOADS_PATH", str(tmp_path / "no-ig.json"))
    monkeypatch.setattr(stato_pipeline, "KNOWN_ISSUES_PATH", str(tmp_path / "no-issues.json"))
    monkeypatch.setattr(stato_pipeline, "LAUNCHAGENTS", str(tmp_path / "no-agents"))

    status = stato_pipeline.compute_status()
    texts = [f["text"] for f in status["flags"]]
    assert any("raccolta metriche video" in t.lower() for t in texts)
