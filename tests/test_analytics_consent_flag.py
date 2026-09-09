"""Guardia di consenso per raccogli_finestre_fisse.py (Fase 2 del layer analytics).

WHY THIS EXISTS
----------------
Il token Analytics è dedicato: se scade o non è mai stato concesso, nessun altro
script della pipeline lo rinnova da solo. Questi test pinnano i tre stati dichiarati
esplicitamente nel piano — file assente, consent_needed false, consent_needed true —
perché "serve un consenso umano" è un'azione diversa da "il dato è vecchio", e i due
segnali non devono mai confondersi in dashboard.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stato_pipeline  # noqa: E402


def write_status(path, payload):
    path.write_text(json.dumps(payload))


def test_no_status_file_is_silent_not_an_error(tmp_path, monkeypatch):
    """La Fase 2 non ha ancora girato una volta: non è
    un errore da segnalare, la guardia di freschezza generica già copre 'nessun
    dato mai' senza bisogno di duplicarlo qui."""
    monkeypatch.setattr(stato_pipeline, "ANALYTICS_CONSENT_STATUS_PATH",
                         str(tmp_path / "missing.json"))
    assert stato_pipeline._analytics_consent_flag() is None


def test_consent_needed_false_is_silent(tmp_path, monkeypatch):
    p = tmp_path / "status.json"
    write_status(p, {"consent_needed": False})
    monkeypatch.setattr(stato_pipeline, "ANALYTICS_CONSENT_STATUS_PATH", str(p))
    assert stato_pipeline._analytics_consent_flag() is None


def test_consent_needed_true_raises_an_error_flag(tmp_path, monkeypatch):
    p = tmp_path / "status.json"
    write_status(p, {"consent_needed": True, "ultimo_errore": "RefreshError"})
    monkeypatch.setattr(stato_pipeline, "ANALYTICS_CONSENT_STATUS_PATH", str(p))
    flag = stato_pipeline._analytics_consent_flag()
    assert flag["level"] == "error"
    assert "consenso" in flag["text"].lower()


def test_the_message_is_generic_not_specific_to_expired(tmp_path, monkeypatch):
    """Non deve dire 'scaduto': copre anche 'mai consentito' e 'file corrotto',
    dove 'scaduto' sarebbe testualmente sbagliato."""
    p = tmp_path / "status.json"
    write_status(p, {"consent_needed": True, "ultimo_errore": "FileNotFoundError"})
    monkeypatch.setattr(stato_pipeline, "ANALYTICS_CONSENT_STATUS_PATH", str(p))
    flag = stato_pipeline._analytics_consent_flag()
    assert "scadut" not in flag["text"].lower()


def test_a_corrupt_status_file_is_silent_not_a_crash(tmp_path, monkeypatch):
    p = tmp_path / "status.json"
    p.write_text('{"consent_needed": true')  # troncato
    monkeypatch.setattr(stato_pipeline, "ANALYTICS_CONSENT_STATUS_PATH", str(p))
    assert stato_pipeline._analytics_consent_flag() is None


def test_compute_status_surfaces_the_consent_flag_in_the_dashboard(tmp_path, monkeypatch):
    p = tmp_path / "status.json"
    write_status(p, {"consent_needed": True})
    monkeypatch.setattr(stato_pipeline, "ANALYTICS_CONSENT_STATUS_PATH", str(p))
    monkeypatch.setattr(stato_pipeline, "METRICHE_STORICO_PATH", str(tmp_path / "no-metriche.json"))
    monkeypatch.setattr(stato_pipeline, "FINESTRE_FISSE_PATH", str(tmp_path / "no-finestre.json"))
    monkeypatch.setattr(stato_pipeline, "QUEUE_PATH", str(tmp_path / "no-queue.json"))
    monkeypatch.setattr(stato_pipeline, "IG_UPLOADS_PATH", str(tmp_path / "no-ig.json"))
    monkeypatch.setattr(stato_pipeline, "KNOWN_ISSUES_PATH", str(tmp_path / "no-issues.json"))
    monkeypatch.setattr(stato_pipeline, "LAUNCHAGENTS", str(tmp_path / "no-agents"))

    status = stato_pipeline.compute_status()
    texts = [f["text"] for f in status["flags"]]
    assert any("consenso youtube analytics" in t.lower() for t in texts)
