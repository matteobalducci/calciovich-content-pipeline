"""youtube_readonly_auth.py — un token per canale, salvato solo se appartiene davvero al canale scelto."""
import os
import stat
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metriche_video  # noqa: E402
import youtube_readonly_auth as ra  # noqa: E402


class FakeCreds:
    def to_json(self):
        return '{"fake": "token"}'


def _run(monkeypatch, tmp_path, channel, owner_ok):
    tokens = {k: str(tmp_path / os.path.basename(v)) for k, v in ra.TOKENS.items()}
    monkeypatch.setattr(ra, "TOKENS", tokens)
    flow_mod = types.SimpleNamespace(InstalledAppFlow=types.SimpleNamespace(
        from_client_secrets_file=lambda *a, **k: types.SimpleNamespace(run_local_server=lambda port=0: FakeCreds())))
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib", types.SimpleNamespace(flow=flow_mod))
    monkeypatch.setitem(sys.modules, "google_auth_oauthlib.flow", flow_mod)

    def verify(creds, key):
        if not owner_ok:
            raise metriche_video.ChannelMismatch("il token appartiene a ['UCaltro']")
    monkeypatch.setattr(ra, "verify_owner", verify)
    return tokens


def test_each_channel_has_its_own_token_file():
    assert ra.TOKENS["gol-impossibili"] != ra.TOKENS["calciovich"]
    assert os.path.basename(ra.TOKENS["calciovich"]) == "youtube_readonly_libro_token.json"
    assert ra.TOKEN_PATH == ra.TOKENS["gol-impossibili"]                      # compatibilita' con chi la importa


def test_a_token_for_the_chosen_channel_is_saved_private(monkeypatch, tmp_path):
    tokens = _run(monkeypatch, tmp_path, "calciovich", owner_ok=True)
    ra.main(["--channel", "calciovich"])
    path = tokens["calciovich"]
    assert open(path).read() == '{"fake": "token"}'
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not os.path.exists(tokens["gol-impossibili"])                      # l'altro canale non e' toccato


def test_a_token_of_the_wrong_channel_is_not_saved(monkeypatch, tmp_path):
    tokens = _run(monkeypatch, tmp_path, "calciovich", owner_ok=False)
    with pytest.raises(SystemExit) as exc:
        ra.main(["--channel", "calciovich"])
    assert "NON SALVATO" in str(exc.value)
    assert not os.path.exists(tokens["calciovich"])


def test_verify_owner_checks_the_token_against_the_id_of_the_chosen_channel(monkeypatch):
    """Il collegamento vero: verify_owner costruisce il client YouTube e chiama check_owner con l'ID del canale
    scelto (non uno stub che solleva a prescindere)."""
    seen = {}
    fake_discovery = types.SimpleNamespace(build=lambda *a, **k: ("yt", a, k))
    monkeypatch.setitem(sys.modules, "googleapiclient", types.SimpleNamespace(discovery=fake_discovery))
    monkeypatch.setitem(sys.modules, "googleapiclient.discovery", fake_discovery)
    monkeypatch.setattr(metriche_video, "check_owner", lambda yt, expected: seen.update(yt=yt, expected=expected))
    ra.verify_owner(FakeCreds(), "calciovich")
    assert seen["expected"] == ra.CHANNEL_IDS["calciovich"] == "UCy1V7Lwaeb8_6iaSEzSOtPA"
    assert seen["yt"][0] == "yt"
    ra.verify_owner(FakeCreds(), "gol-impossibili")
    assert seen["expected"] == ra.CHANNEL_IDS["gol-impossibili"] == "UCLPBYAv19aizEYX4MmXV7rA"
