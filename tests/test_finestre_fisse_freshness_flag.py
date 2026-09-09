"""Guardia di freschezza per raccogli_finestre_fisse.py (Fase 2 del layer
analytics).

WHY THIS EXISTS
----------------
Trovato durante un dibattito di controllo sul codice appena completato: senza
questa guardia, un guasto silenzioso (metriche-finestre-fisse.json corrotto, o
errori di rete ripetuti durante il refresh del token che fanno saltare la
raccolta senza toccare il consenso — quindi senza far scattare la guardia di
consenso) non produce alcun segnale in dashboard. Il fix statistico che è
l'intero scopo della Fase 2 smetterebbe di funzionare senza che nessuno se ne
accorga, tornando silenziosamente al confronto lifetime-vs-lifetime che doveva
correggere.
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stato_pipeline  # noqa: E402


def write_storico(path, last_run_at):
    path.write_text(json.dumps({"records": {}, "last_run_at": last_run_at}))


def test_no_file_is_a_warning_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(stato_pipeline, "FINESTRE_FISSE_PATH", str(tmp_path / "missing.json"))
    flag = stato_pipeline._finestre_fisse_freshness_flag()
    assert flag["level"] == "warn"
    assert "nessuna raccolta finestre fisse" in flag["text"].lower()


def test_a_recent_run_raises_no_flag(tmp_path, monkeypatch):
    p = tmp_path / "finestre.json"
    recent = (datetime.datetime.now() - datetime.timedelta(hours=2)).isoformat(timespec="seconds")
    write_storico(p, recent)
    monkeypatch.setattr(stato_pipeline, "FINESTRE_FISSE_PATH", str(p))
    assert stato_pipeline._finestre_fisse_freshness_flag() is None


def test_a_run_older_than_36_hours_warns(tmp_path, monkeypatch):
    p = tmp_path / "finestre.json"
    stale = (datetime.datetime.now() - datetime.timedelta(hours=40)).isoformat(timespec="seconds")
    write_storico(p, stale)
    monkeypatch.setattr(stato_pipeline, "FINESTRE_FISSE_PATH", str(p))
    flag = stato_pipeline._finestre_fisse_freshness_flag()
    assert flag["level"] == "warn"


def test_a_run_older_than_72_hours_errors(tmp_path, monkeypatch):
    p = tmp_path / "finestre.json"
    very_stale = (datetime.datetime.now() - datetime.timedelta(hours=80)).isoformat(timespec="seconds")
    write_storico(p, very_stale)
    monkeypatch.setattr(stato_pipeline, "FINESTRE_FISSE_PATH", str(p))
    flag = stato_pipeline._finestre_fisse_freshness_flag()
    assert flag["level"] == "error"


def test_a_corrupt_file_is_a_warning_not_a_crash(tmp_path, monkeypatch):
    p = tmp_path / "finestre.json"
    p.write_text('{"records": {}, "last_run_at":')  # troncato
    monkeypatch.setattr(stato_pipeline, "FINESTRE_FISSE_PATH", str(p))
    flag = stato_pipeline._finestre_fisse_freshness_flag()
    assert flag["level"] == "warn"  # last_run_at illeggibile == mai girato, stesso trattamento
