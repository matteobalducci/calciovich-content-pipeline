"""raccogli_snapshot_cloud.py — convenzione oraria di snapshot_at.

WHY THIS EXISTS: il Mac scrive snapshot_at come ora locale italiana senza fuso; un
runner GitHub e' in UTC. Con datetime.now() nudo le due scritture avevano convenzioni
diverse nella stessa colonna e il filtro per stringa scartava un giro cloud riuscito
(run "success" con 0 righe scritte, 2026-10-03).
"""
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import raccogli_snapshot_cloud as rsc  # noqa: E402


def test_snapshot_at_has_the_same_naive_iso_format_as_the_mac_side():
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", rsc._snapshot_at_now())


def test_snapshot_at_is_rome_wall_clock_not_utc():
    got = datetime.fromisoformat(rsc._snapshot_at_now())
    rome = datetime.now(ZoneInfo("Europe/Rome")).replace(tzinfo=None)
    assert abs((rome - got).total_seconds()) < 5
