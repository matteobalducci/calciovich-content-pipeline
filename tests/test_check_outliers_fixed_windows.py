"""Fase 2 del layer analytics — il fix statistico vero in check_outliers.py.

WHY THIS EXISTS
----------------
Il bias che questa fase corregge (video appena uscito confrontato a view lifetime
di video vecchi) si ripresenta in una forma nuova se si mescolano finestre fisse e
view lifetime nella STESSA mediana — il bias finisce dentro il calcolo invece che
fra latest e mediana. Questi test pinnano la regola "tutto o niente": o il campione
è omogeneo (latest + storia sufficiente, tutti views_day1), o si resta su lifetime
per intero — mai un mix, mai una forzatura quando il campione è troppo piccolo.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from check_outliers import _choose_comparison, _views_day1_by_video, MIN_HISTORY  # noqa: E402


def entry(vid, v_lifetime, data_ord="2026-07-01"):
    return (data_ord, f"key-{vid}", vid, v_lifetime)


def test_uses_views_day1_when_latest_and_enough_history_have_it():
    history = [entry("h1", 100), entry("h2", 100), entry("h3", 100)]
    latest = entry("latest", 999)
    day1 = {"h1": 10, "h2": 20, "h3": 30, "latest": 50}
    v, med, base = _choose_comparison(history, latest, day1)
    assert base == "views_day1"
    assert v == 50
    assert med == 20  # mediana di [10, 20, 30], non delle view lifetime


def test_falls_back_to_lifetime_when_latest_has_no_views_day1():
    """Il caso piu' frequente e piu' importante: il video appena uscito (<24h) non
    ha ancora views_day1 — proprio lo scenario che la Fase 2 esiste per correggere."""
    history = [entry("h1", 100), entry("h2", 100), entry("h3", 100)]
    latest = entry("latest", 999)
    day1 = {"h1": 10, "h2": 20, "h3": 30}  # latest assente
    v, med, base = _choose_comparison(history, latest, day1)
    assert base == "lifetime"
    assert v == 999
    assert med == 100


def test_falls_back_to_lifetime_when_homogeneous_history_is_below_min_history():
    """Latest ha views_day1, ma solo 2 dei 3 storici ce l'hanno — sotto MIN_HISTORY
    (3): niente confronto degradato, si resta su lifetime per intero, non su un
    campione fisso troppo piccolo."""
    assert MIN_HISTORY == 3
    history = [entry("h1", 100), entry("h2", 100), entry("h3", 100)]
    latest = entry("latest", 999)
    day1 = {"h1": 10, "h2": 20, "latest": 50}  # solo 2 storici, non 3
    v, med, base = _choose_comparison(history, latest, day1)
    assert base == "lifetime"


def test_history_without_day1_is_excluded_not_mixed_in():
    """4 storici, solo 3 con views_day1: la mediana fissa usa SOLO i 3 omogenei,
    mai una mediana che mescola valori fissi e lifetime."""
    history = [entry("h1", 100), entry("h2", 100), entry("h3", 100), entry("h4", 100)]
    latest = entry("latest", 999)
    day1 = {"h1": 10, "h2": 20, "h3": 30, "latest": 50}  # h4 assente
    v, med, base = _choose_comparison(history, latest, day1)
    assert base == "views_day1"
    assert med == 20  # mediana di [10, 20, 30] soli, h4 escluso


def test_no_fixed_window_data_at_all_falls_back_to_lifetime():
    history = [entry("h1", 100), entry("h2", 100), entry("h3", 100)]
    latest = entry("latest", 999)
    v, med, base = _choose_comparison(history, latest, {})
    assert base == "lifetime"
    assert v == 999
    assert med == 100


def test_views_day1_by_video_ignores_videos_without_the_field():
    records = {
        "v1": {"key": "k1", "views_day1": 10},
        "v2": {"key": "k2", "views_day1": None},
        "v3": {"key": "k3"},
    }
    assert _views_day1_by_video(records) == {"v1": 10}


def test_views_day1_by_video_tolerates_records_not_being_a_dict():
    """Riprodotto dal dibattito di controllo: uno schema legacy o un file toccato
    a mano (es. 'records' come lista invece di dict) non deve sollevare
    AttributeError — degrada a nessun dato fisso, come promette il docstring."""
    assert _views_day1_by_video([{"video_id": "v1", "views_day1": 10}]) == {}


def test_views_day1_by_video_tolerates_a_record_that_is_not_a_dict():
    records = {"v1": "non-un-dict", "v2": {"views_day1": 20}}
    assert _views_day1_by_video(records) == {"v2": 20}
