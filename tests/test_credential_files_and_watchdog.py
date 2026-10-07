"""Correzioni dell'audit Codex del 2026-10-07 sui commit pubblicati del 03-04/10:
permessi dei token gia' esistenti, chiave dei flag del watchdog, notifiche fallite."""
import datetime
import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stato_pipeline  # noqa: E402
import youtube_analytics_auth as yaa  # noqa: E402
import app_server  # noqa: E402


# ---------------------------------------------------------------- token files

def test_write_private_tightens_a_token_file_that_already_exists_with_wide_permissions(tmp_path):
    f = tmp_path / "token.json"
    f.write_text("old")
    os.chmod(f, 0o644)                                    # os.open(..., 0o600) NON la restringerebbe
    yaa.write_private(str(f), "new")
    assert f.read_text() == "new"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


def test_write_private_falls_back_to_chmod_where_fchmod_does_not_exist(tmp_path, monkeypatch):
    f = tmp_path / "token.json"
    f.write_text("old")
    os.chmod(f, 0o644)
    monkeypatch.delattr(os, "fchmod")                     # come su Windows: hasattr(os, "fchmod") e' False
    yaa.write_private(str(f), "new")
    assert f.read_text() == "new"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


def test_write_private_creates_new_files_as_0600(tmp_path):
    f = tmp_path / "fresh.json"
    yaa.write_private(str(f), "x")
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600


# ---------------------------------------------------------------- flag_key

def test_volatile_numbers_do_not_change_the_flag_key():
    a = stato_pipeline.flag_key("warn", "Nessuna raccolta da 31 ore (ultima 2026-10-03)")
    b = stato_pipeline.flag_key("warn", "Nessuna raccolta da 32 ore (ultima 2026-10-04)")
    assert a == b                                          # stesso problema, numeri che scorrono


def test_distinct_identifiers_are_distinct_problems():
    a = stato_pipeline.flag_key("warn", "Titolo mancante per short42")
    b = stato_pipeline.flag_key("warn", "Titolo mancante per short43")
    assert a != b                                          # il vecchio [0-9]+ -> '#' li fondeva


def test_a_number_glued_to_a_unit_is_still_noise_but_an_id_with_a_prefix_is_not():
    k = stato_pipeline.flag_key
    assert k("warn", "soglia 36h") == k("warn", "soglia 72h")              # "36h" come "36 ore": rumore
    assert k("warn", "manca abc42") != k("warn", "manca abc43")            # prefisso alfabetico: identita'
    assert k("warn", "manca ep3") != k("warn", "manca ep4")
    assert k("warn", "dal 2026-10-03") == k("warn", "dal 2026-10-04")      # le date scorrono


def test_level_is_part_of_the_key():
    assert stato_pipeline.flag_key("warn", "x 1") != stato_pipeline.flag_key("error", "x 1")


# ---------------------------------------------------------------- watchdog step

NOW = datetime.datetime(2026, 10, 7, 12, 0, 0)
FLAG = {"text": "Qualcosa non va", "level": "error"}


def _step(active, last, notify):
    return app_server._watchdog_step(active, last, NOW, notify)


def test_a_new_flag_is_notified_and_recorded():
    sent = []
    state = _step({"k": FLAG}, {}, lambda t: sent.append(t) or True)
    assert len(sent) == 1 and sent[0].startswith("🛑")
    assert state == {"k": NOW.isoformat(timespec="seconds")}


def test_a_failed_notification_is_not_recorded_so_it_is_retried_next_cycle():
    state = _step({"k": FLAG}, {}, lambda t: False)
    assert state == {}                                     # non e' partita: niente "gia' notificato"
    sent = []
    _step({"k": FLAG}, state, lambda t: sent.append(t) or True)
    assert len(sent) == 1                                  # al giro dopo ci riprova


def test_inside_the_quiet_window_it_stays_silent_and_keeps_the_old_time():
    prev = (NOW - datetime.timedelta(hours=3)).isoformat(timespec="seconds")
    sent = []
    state = _step({"k": FLAG}, {"k": prev}, lambda t: sent.append(t) or True)
    assert sent == [] and state == {"k": prev}


def test_after_the_renotify_window_it_notifies_again():
    prev = (NOW - datetime.timedelta(hours=13)).isoformat(timespec="seconds")
    sent = []
    state = _step({"k": FLAG}, {"k": prev}, lambda t: sent.append(t) or True)
    assert len(sent) == 1 and state["k"] == NOW.isoformat(timespec="seconds")


def test_a_flag_that_is_no_longer_active_leaves_the_state():
    prev = NOW.isoformat(timespec="seconds")
    assert _step({}, {"gone": prev}, lambda t: True) == {}
