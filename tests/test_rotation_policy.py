"""An editorial pause has to bind where publishing happens.

WHY THIS EXISTS
--------------
The AI-clip format was paused on 2026-09-02. The decision was written into
rotation-state.json and the coach's decision log — both read by the planning
session, neither read by the publishers. A clip went out the next day anyway.

These tests assert the pause is honoured by the thing that can actually publish.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rotation_policy import RotationPolicy  # noqa: E402


@pytest.fixture
def output(tmp_path):
    return str(tmp_path)


def write_state(output, state):
    with open(os.path.join(output, "rotation-state.json"), "w") as fh:
        json.dump(state, fh)


def test_a_paused_category_is_blocked(output):
    write_state(output, {"gol_ai_paused": "STOP deciso dall'autore il 2026-09-02"})
    policy = RotationPolicy(output)
    assert policy.is_paused("Gol-AI")
    assert "2026-09-02" in policy.pause_reason("Gol-AI")


def test_other_categories_are_untouched(output):
    write_state(output, {"gol_ai_paused": "stop"})
    policy = RotationPolicy(output)
    for category in ["Personaggio: Esordio", "Re-cut da long-form", "Audiolibro", "Pronto"]:
        assert not policy.is_paused(category), f"{category} non deve essere bloccata"


def test_the_path_catches_a_mislabelled_item(output):
    """Un item la cui categoria e' stata modificata a mano non deve sfuggire:
    conta anche dove vive il file."""
    write_state(output, {"gol_ai_paused": "stop"})
    policy = RotationPolicy(output)
    assert policy.is_paused("Qualcos'altro", "/output/ai-clips/settimana3-day1.mp4")
    assert not policy.is_paused("Qualcos'altro", "/output/short30.vert.mp4")


def test_no_pause_means_everything_publishes(output):
    write_state(output, {"gol_ai_last_number": 15})
    policy = RotationPolicy(output)
    assert not policy.is_paused("Gol-AI")
    assert policy.paused_keys() == []


def test_a_falsy_value_is_not_a_pause(output):
    """Solo un motivo scritto mette in pausa: una chiave vuota o False no."""
    for value in [None, "", False, 0]:
        write_state(output, {"gol_ai_paused": value})
        assert not RotationPolicy(output).is_paused("Gol-AI"), f"valore {value!r}"


# --- fail open, and deliberately so --------------------------------------


def test_a_missing_state_file_does_not_block_publishing(output):
    """Al contrario del registro degli upload, qui si fallisce APERTI: perdere
    una pausa fa uscire un video in ritardo, non due volte."""
    policy = RotationPolicy(output)
    assert not policy.is_paused("Gol-AI")


def test_a_corrupt_state_file_does_not_block_publishing(output):
    with open(os.path.join(output, "rotation-state.json"), "w") as fh:
        fh.write('{"gol_ai_paused": "stop"')   # troncato
    assert not RotationPolicy(output).is_paused("Gol-AI")


def test_a_non_object_state_file_does_not_block_publishing(output):
    with open(os.path.join(output, "rotation-state.json"), "w") as fh:
        json.dump(["non", "un", "oggetto"], fh)
    assert not RotationPolicy(output).is_paused("Gol-AI")
