"""Fase 1 del layer analytics estrae categoria()/_app_data_categoria_map()/fetch_stats()
da check_outliers.py in un modulo condiviso.

WHY THIS EXISTS
----------------
Un refactor di logica di classificazione che gira ogni giorno non deve cambiare
comportamento silenziosamente: un video vicino a un confine di categoria che cambia
classificazione per un bug di estrazione, non per una decisione, sposta la mediana
usata da check_outliers.py e può generare falsi outlier. Questi test pinnano ogni
ramo — inclusi quelli non ovvi (precedenza fra rami, case mismatch, chiavi duplicate,
il ramo silenzioso di _app_data_categoria_map) — non solo un caso per ramo.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metriche_video import categoria, _app_data_categoria_map, fetch_stats  # noqa: E402


# --- categoria(): un caso per ramo -----------------------------------------


def test_a_key_already_in_the_app_map_wins_regardless_of_its_shape():
    app_map = {"short26": "personaggio"}
    assert categoria("short26", app_map) == "personaggio"


def test_character_keys_substring_match():
    app_map = {}
    for needle in ["vecio-dixe", "vecio_dixe", "non-sa-fare", "non_sa_fare",
                   "subentro-decisivo", "subentro_decisivo", "esordio", "tomasito"]:
        key = f"prefix-{needle}-suffix"
        assert categoria(key, app_map) == "personaggio", key


def test_short_number_prefix_is_canonical():
    assert categoria("short12-qualcosa.vert", {}) == "canonical"


def test_ep_number_prefix_is_longform():
    assert categoria("ep3-titolo", {}) == "long-form"


def test_libro_prefix_is_longform():
    assert categoria("libro-p1-capitolo1", {}) == "long-form"
    assert categoria("libroaltro", {}) == "long-form"


def test_settimana_and_day_together_is_gol_ai():
    assert categoria("settimana2-day4-rovesciata-final.tv", {}) == "gol-ai"


def test_nothing_matches_falls_to_altro():
    assert categoria("qualcosa-di-mai-visto", {}) == "altro"


# --- categoria(): casi non ovvi, l'obiezione fondata del round 1 -----------


def test_character_keys_take_precedence_over_the_short_regex():
    """Una key non in app_map che soddisfa sia CHARACTER_KEYS sia ^short\\d+ deve
    vincere sul ramo 'personaggio' (ramo 2), perche' e' controllato prima del
    regex 'canonical' al ramo 3."""
    key = "short07-tomasito-esordio"
    assert categoria(key, {}) == "personaggio"


def test_case_mismatch_falls_back_predictably_to_the_regex_path():
    """Il lookup in app_map e' case-sensitive sulla key originale; i regex
    successivi lavorano su key.lower(). Una key con maiuscole diverse da come
    app_map e' stata costruita non deve sparire silenziosamente: deve ricadere
    sul path regex (qui: short, case-insensitive)."""
    app_map = {"short05-qualcosa": "personaggio"}  # costruita in minuscolo
    assert categoria("SHORT05-qualcosa", app_map) == "canonical"


def test_duplicate_base_keys_in_app_data_the_last_one_wins():
    app_data = {
        "weeks": [{
            "items": [
                {"file": "short09-x.vert", "categoria": "Pronto"},
                {"file": "short09-x.vert", "categoria": "Personaggio: Esordio"},
            ]
        }]
    }
    app_map = _app_data_categoria_map(app_data)
    assert app_map["short09-x"] == "personaggio"


def test_an_unrecognised_categoria_value_is_silently_excluded_from_the_map():
    """_app_data_categoria_map() ha un ramo silenzioso: un valore di categoria
    che non e' ne' 'Personaggio*' ne' una delle tre stringhe esatte va escluso
    dalla mappa — testato, non assunto."""
    app_data = {
        "weeks": [{
            "items": [
                {"file": "short11-boh.vert", "categoria": "Categoria mai vista"},
                {"file": "short12-noto.vert", "categoria": "Pronto"},
            ]
        }]
    }
    app_map = _app_data_categoria_map(app_data)
    assert "short11-boh" not in app_map
    assert app_map["short12-noto"] == "canonical"


def test_app_data_map_reads_the_real_file_by_default(tmp_path, monkeypatch):
    """Il default (nessun app_data passato) deve continuare a leggere APP_DATA dal
    disco — il parametro iniettabile e' solo per i test, non cambia il comportamento
    in produzione."""
    import metriche_video
    fake_path = tmp_path / "data.json"
    fake_path.write_text(
        '{"weeks": [{"items": [{"file": "short99-x.vert", "categoria": "Pronto"}]}]}'
    )
    monkeypatch.setattr(metriche_video, "APP_DATA", str(fake_path))
    assert _app_data_categoria_map()["short99-x"] == "canonical"


# --- fetch_stats(): l'override esplicito, non monkeypatching ---------------


def test_stats_override_is_returned_verbatim_without_touching_the_network():
    frozen = {"abc123": {"views": 100, "likes": 5, "comments": 1, "privacy": "public"}}
    assert fetch_stats(["abc123"], stats_override=frozen) is frozen


def test_stats_override_bypasses_any_credential_requirement(monkeypatch):
    """Se stats_override e' passato, fetch_stats non deve toccare TOKEN_PATH ne'
    la rete — deve funzionare anche senza credenziali sul disco."""
    import metriche_video
    monkeypatch.setattr(metriche_video, "TOKEN_PATH", "/percorso/che/non/esiste.json")
    frozen = {"xyz": {"views": 0, "likes": 0, "comments": 0, "privacy": "private"}}
    assert fetch_stats(["xyz"], stats_override=frozen) == frozen
