"""Fase 2 del layer analytics — finestre fisse (24h/48h/168h) via YouTube Analytics
API.

WHY THIS EXISTS
----------------
"None se il video non ha ancora raggiunto quell'età" non è un dettaglio: un finto
zero al posto di None trasformerebbe un video appena uscito in un FAIL artificiale
in check_outliers.py — esattamente il bias che questa fase esiste per correggere.
Questi test pinnano il confine di età per ciascuna finestra, non solo il calcolo.
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metriche_video import fetch_fixed_windows  # noqa: E402


def test_all_windows_none_on_publish_day():
    windows = fetch_fixed_windows(
        "v1", "2026-07-05", window_override={"2026-07-05": 339},
        today=date(2026, 7, 5),
    )
    assert windows == {"views_day1": None, "views_day2": None, "views_day7": None}


def test_day1_available_the_day_after_publish():
    windows = fetch_fixed_windows(
        "v1", "2026-07-05",
        window_override={"2026-07-05": 339, "2026-07-06": 1},
        today=date(2026, 7, 6),
    )
    assert windows["views_day1"] == 339
    assert windows["views_day2"] is None
    assert windows["views_day7"] is None


def test_day2_is_cumulative_not_the_single_days_value():
    windows = fetch_fixed_windows(
        "v1", "2026-07-05",
        window_override={"2026-07-05": 339, "2026-07-06": 1},
        today=date(2026, 7, 7),
    )
    assert windows["views_day2"] == 340  # 339 + 1, non 1


def test_day7_cumulative_matches_a_real_probe():
    """Dati reali osservati su un video noto: short03-el-vecio-dixe.vert, iuQAVoyHo3Q."""
    daily = {
        "2026-07-05": 339, "2026-07-06": 1, "2026-07-07": 2, "2026-07-08": 0,
        "2026-07-09": 3, "2026-07-10": 17, "2026-07-11": 7,
    }
    windows = fetch_fixed_windows(
        "iuQAVoyHo3Q", "2026-07-05", window_override=daily, today=date(2026, 7, 12),
    )
    assert windows["views_day1"] == 339
    assert windows["views_day2"] == 340
    assert windows["views_day7"] == 369  # somma dei 7 giorni sopra


def test_a_day_with_zero_views_is_not_confused_with_a_missing_day():
    """2026-07-08 ha 0 view reali (non mancante) — deve contare come 0, non essere
    scambiato per un giorno senza dati."""
    windows = fetch_fixed_windows(
        "v1", "2026-07-05",
        window_override={"2026-07-05": 10, "2026-07-06": 5, "2026-07-07": 0},
        today=date(2026, 7, 8),
    )
    assert windows["views_day2"] == 15
    assert windows["views_day1"] == 10


def test_publish_date_accepts_a_string_or_a_date_object():
    from datetime import date as _date
    a = fetch_fixed_windows("v1", "2026-07-05", window_override={"2026-07-05": 10},
                             today=date(2026, 7, 5))
    b = fetch_fixed_windows("v1", _date(2026, 7, 5), window_override={"2026-07-05": 10},
                             today=date(2026, 7, 5))
    assert a == b
