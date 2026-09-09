"""Fase 2 del layer analytics — raccolta delle finestre fisse.

WHY THIS EXISTS
----------------
Due rischi guidano questi test: (1) congelare una finestra troppo presto, prima che
i dati Analytics (spesso provvisori sui giorni più recenti) abbiano avuto modo di
convergere — da cui la politica "aggiorna finché età < N+1, poi congela"; (2) un
consenso OAuth scaduto/mai fatto che fa crashare un LaunchAgent non presidiato
invece di degradare pulito — lo stesso bug già trovato e corretto in Fase 1
(raccogli_metriche_video.py), qui esteso a un token dedicato che non si autoripara.
"""
import json
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import raccogli_finestre_fisse as rff  # noqa: E402
import metriche_video  # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(rff, "OUTPUT", str(output))
    monkeypatch.setattr(rff, "YT_UPLOADS", str(output / "youtube-uploads.json"))
    monkeypatch.setattr(rff, "FINESTRE", str(output / "metriche-finestre-fisse.json"))
    monkeypatch.setattr(rff, "CONSENT_STATUS", str(output / "analytics-consent-status.json"))
    return output


def write_uploads(repo, uploads):
    (repo / "youtube-uploads.json").write_text(json.dumps(uploads))


# --- build_plan(): scope e finestre di aggiornamento -----------------------


def test_build_plan_skips_a_video_not_yet_old_enough_for_any_window(repo):
    write_uploads(repo, {"k": {"videoId": "v1", "publishAt": "2026-07-05T15:00:00Z"}})
    updates = rff.build_plan(
        analytics_creds=None, today=date(2026, 7, 5),
        windows_override={"v1": {"2026-07-05": 10}},
    )
    assert updates == []


def test_build_plan_includes_day1_the_day_after_publish(repo):
    write_uploads(repo, {"k": {"videoId": "v1", "publishAt": "2026-07-05T15:00:00Z"}})
    updates = rff.build_plan(
        analytics_creds=None, today=date(2026, 7, 6),
        windows_override={"v1": {"2026-07-05": 10}},
    )
    assert updates == [("v1", "k", {"views_day1": 10})]


def test_build_plan_freezes_day1_once_age_reaches_2(repo):
    """Eta' 2: la finestra di aggiornamento di day1 (eta' < 2) e' chiusa — non deve
    piu' comparire fra i campi da scrivere, anche se il valore e' disponibile."""
    write_uploads(repo, {"k": {"videoId": "v1", "publishAt": "2026-07-05T15:00:00Z"}})
    updates = rff.build_plan(
        analytics_creds=None, today=date(2026, 7, 7),
        windows_override={"v1": {"2026-07-05": 10, "2026-07-06": 5}},
    )
    fields = updates[0][2] if updates else {}
    assert "views_day1" not in fields
    assert fields.get("views_day2") == 15


def test_build_plan_gives_day7_the_same_margin_as_day1_day2(repo):
    """Eta' 7: day7 diventa disponibile e deve ESSERE nella finestra di
    aggiornamento (eta' < 8) — non congelato al primo valore disponibile."""
    write_uploads(repo, {"k": {"videoId": "v1", "publishAt": "2026-07-05T15:00:00Z"}})
    daily = {f"2026-07-{5+i:02d}": 1 for i in range(7)}
    updates = rff.build_plan(
        analytics_creds=None, today=date(2026, 7, 12), windows_override={"v1": daily},
    )
    fields = updates[0][2]
    assert fields.get("views_day7") == 7


def test_build_plan_stops_processing_a_video_past_scope_days(repo):
    """Oltre 8 giorni di eta' il video esce completamente dallo scope di raccolta —
    non solo si congela day7, non viene proprio piu' processato."""
    write_uploads(repo, {"k": {"videoId": "v1", "publishAt": "2026-07-05T15:00:00Z"}})
    updates = rff.build_plan(
        analytics_creds=None, today=date(2026, 7, 14),  # eta' 9
        windows_override={"v1": {"2026-07-05": 1}},
    )
    assert updates == []


def test_build_plan_skips_uploads_with_no_publish_date(repo):
    write_uploads(repo, {"k": {"videoId": "v1"}})
    updates = rff.build_plan(analytics_creds=None, today=date(2026, 7, 5), windows_override={})
    assert updates == []


def test_a_malformed_publish_date_is_skipped_not_a_crash_for_the_whole_batch(repo):
    """Riprodotto dal dibattito di controllo: un solo publishAt rotto non deve
    affondare la raccolta per tutti gli altri video."""
    write_uploads(repo, {
        "broken": {"videoId": "v-broken", "publishAt": "N/D"},
        "ok": {"videoId": "v-ok", "publishAt": "2026-07-05T15:00:00Z"},
    })
    updates = rff.build_plan(
        analytics_creds=None, today=date(2026, 7, 6),
        windows_override={"v-ok": {"2026-07-05": 10}},
    )
    assert [u[0] for u in updates] == ["v-ok"]


def test_a_per_video_api_error_is_skipped_not_a_crash_for_the_whole_batch(repo, monkeypatch):
    """Riprodotto dal dibattito di controllo: un errore dell'API (rete, quota,
    video cancellato) su un video non deve affondare gli altri nello stesso run."""
    write_uploads(repo, {
        "boom": {"videoId": "v-boom", "publishAt": "2026-07-05T15:00:00Z"},
        "ok": {"videoId": "v-ok", "publishAt": "2026-07-05T15:00:00Z"},
    })

    real_fetch = rff.fetch_fixed_windows

    def flaky_fetch(vid, *a, **kw):
        if vid == "v-boom":
            raise RuntimeError("errore API simulato")
        return real_fetch(vid, *a, **kw)

    monkeypatch.setattr(rff, "fetch_fixed_windows", flaky_fetch)
    updates = rff.build_plan(
        analytics_creds=None, today=date(2026, 7, 6),
        windows_override={"v-boom": {"2026-07-05": 1}, "v-ok": {"2026-07-05": 10}},
    )
    assert [u[0] for u in updates] == ["v-ok"]


# --- merge_windows(): un campo congelato non deve mai regredire -------------


def test_merge_windows_adds_a_new_video():
    merged, changed = rff.merge_windows({}, [("v1", "k", {"views_day1": 10})])
    assert merged["v1"] == {"video_id": "v1", "key": "k", "views_day1": 10}
    assert changed == 1


def test_merge_windows_leaves_a_frozen_field_untouched_when_absent_from_updates():
    """Se day1 non compare piu' negli updates (congelato), il valore gia' scritto
    deve restare intatto — merge_windows non lo deve mai azzerare o toccare."""
    existing = {"v1": {"video_id": "v1", "key": "k", "views_day1": 10}}
    merged, changed = rff.merge_windows(existing, [("v1", "k", {"views_day2": 15})])
    assert merged["v1"]["views_day1"] == 10
    assert merged["v1"]["views_day2"] == 15
    assert changed == 1


def test_merge_windows_counts_only_real_changes():
    existing = {"v1": {"video_id": "v1", "key": "k", "views_day1": 10}}
    merged, changed = rff.merge_windows(existing, [("v1", "k", {"views_day1": 10})])
    assert changed == 0  # stesso valore di prima, nessun vero cambiamento


# --- consenso: RefreshError, FileNotFoundError, ValueError sono lo stesso esito --


@pytest.mark.parametrize("exc", [FileNotFoundError("mai esistito"), ValueError("JSON corrotto")])
def test_a_missing_or_corrupt_token_writes_consent_needed(repo, monkeypatch, exc):
    monkeypatch.setattr(rff.youtube_analytics_auth, "get_analytics_credentials_unattended",
                         lambda: (_ for _ in ()).throw(exc))
    rff.main()
    status = json.load(open(rff.CONSENT_STATUS))
    assert status["consent_needed"] is True
    assert status["ultimo_errore"] == type(exc).__name__


def test_an_expired_analytics_token_writes_consent_needed_too(repo, monkeypatch):
    google_auth_exceptions = pytest.importorskip("google.auth.exceptions")
    RefreshError = google_auth_exceptions.RefreshError
    monkeypatch.setattr(rff.youtube_analytics_auth, "get_analytics_credentials_unattended",
                         lambda: (_ for _ in ()).throw(RefreshError("scaduto")))
    rff.main()
    status = json.load(open(rff.CONSENT_STATUS))
    assert status["consent_needed"] is True
    assert status["ultimo_errore"] == "RefreshError"


def test_a_working_consent_writes_consent_needed_false(repo, monkeypatch):
    write_uploads(repo, {})
    monkeypatch.setattr(rff.youtube_analytics_auth, "get_analytics_credentials_unattended",
                         lambda: "fake-creds")
    rff.main()
    status = json.load(open(rff.CONSENT_STATUS))
    assert status["consent_needed"] is False


def test_a_successful_run_writes_last_run_at(repo, monkeypatch):
    write_uploads(repo, {})
    monkeypatch.setattr(rff.youtube_analytics_auth, "get_analytics_credentials_unattended",
                         lambda: "fake-creds")
    rff.main()
    storico = json.load(open(rff.FINESTRE))
    assert "last_run_at" in storico


def test_a_network_error_is_not_treated_as_a_consent_problem(repo, monkeypatch):
    """Riprodotto dal dibattito di controllo: un errore di rete durante il refresh
    (gia' successo in produzione allo script gemello, vedi youtubestats.err.log)
    non deve scrivere consent_needed — non e' un problema di consenso, e non deve
    propagare come crash non gestito."""
    google_auth_exceptions = pytest.importorskip("google.auth.exceptions")
    TransportError = google_auth_exceptions.TransportError
    monkeypatch.setattr(rff.youtube_analytics_auth, "get_analytics_credentials_unattended",
                         lambda: (_ for _ in ()).throw(TransportError("DNS irraggiungibile")))
    rff.main()  # non deve sollevare
    assert not os.path.exists(rff.CONSENT_STATUS)  # nessuna scrittura, non e' quel tipo di errore


def test_get_analytics_credentials_unattended_never_reaches_an_interactive_browser():
    """Verifica statica (Componente 1, obiezione 1 del round 4): nessun nome
    referenziato nel BYTECODE della funzione non presidiata è InstalledAppFlow o
    run_local_server — non un grep sul testo (che troverebbe anche il docstring
    che spiega perché non li usa), i nomi che il codice compilato usa davvero."""
    func = youtube_analytics_auth_module().get_analytics_credentials_unattended
    names = func.__code__.co_names
    assert "InstalledAppFlow" not in names
    assert "run_local_server" not in names


def youtube_analytics_auth_module():
    import youtube_analytics_auth
    return youtube_analytics_auth


# --- lock: stesso rischio di scritture concorrenti gia' visto in Fase 1 ----


def test_collection_lock_refuses_a_second_concurrent_acquisition(repo):
    with rff.collection_lock():
        with pytest.raises(SystemExit):
            with rff.collection_lock():
                pass
