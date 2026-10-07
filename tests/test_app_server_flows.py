"""Il pulsante "ritenta tutto" di TikTok pubblica UN solo post per richiesta (ENG-5: le bozze nella
Inbox si smaltiscono a mano, piu' post insieme fanno scattare spam_risk_too_many_pending_share)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app_server  # noqa: E402


def test_tiktok_without_an_item_is_limited_to_one_post():
    cmd = app_server._flow_tiktok(None)
    assert cmd[1:] == ["carica_tiktok.py", "--all", "--limit", "1"]


def test_tiktok_with_an_item_publishes_only_that_item():
    assert app_server._flow_tiktok("short42")[1:] == ["carica_tiktok.py", "--only", "short42"]


def test_the_retry_all_flow_goes_through_the_limited_command():
    assert app_server.FLOWS["tiktok-retry-all"](None) == app_server._flow_tiktok(None)
    assert "--limit" in app_server.FLOWS["tiktok-retry-all"](None)
