"""The canonical-row rule of the BigQuery views, run for real on synthetic rows.

Needs the loader's service account key (bigquery_loader_key.json): skipped without it (CI of the public repo
has no credentials). The SELECT under test is extracted from sql/mart/views.sql, not retyped here, so the
test cannot drift from what is deployed. No table is created or written: the rows are an inline UNNEST.
"""
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY = os.path.join(HERE, "bigquery_loader_key.json")
VIEWS = os.path.join(HERE, "sql", "mart", "views.sql")

pytestmark = pytest.mark.skipif(not os.path.exists(KEY), reason="nessuna chiave BigQuery: test d'integrazione saltato")

TABLE = "`calciovich-video-analytics.calciovich_content.fct_publish_event`"


def canonical_select():
    sql = open(VIEWS, encoding="utf-8").read()
    m = re.search(r"CREATE OR REPLACE VIEW `[^`]*v_publish_event_canonico` AS\n(.*?);", sql, re.S)
    assert m, "v_publish_event_canonico non trovata in views.sql"
    return m.group(1)


@pytest.fixture(scope="module")
def client():
    from google.cloud import bigquery
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_file(KEY)
    return bigquery.Client(project="calciovich-video-analytics", credentials=creds)


def run(client, rows):
    """rows: [(content_key, platform, channel_key, external_id, confirmed_at|None, scheduled|None)]"""
    def lit(v, kind="STRING"):
        if v is None:
            return f"CAST(NULL AS {kind})"            # typed NULL: an untyped one makes the array's supertype fail
        return f"TIMESTAMP '{v}'" if kind == "TIMESTAMP" else f"'{v}'"
    values = ",\n".join(
        f"STRUCT('{k}' AS content_key, '{p}' AS platform, {lit(c)} AS channel_key, '{e}' AS external_id, "
        f"'private' AS privacy, {lit(t, 'TIMESTAMP')} AS confirmed_at, {lit(s, 'TIMESTAMP')} AS scheduled_publish_at)"
        for k, p, c, e, t, s in rows)
    synthetic = f"(SELECT * FROM UNNEST([{values}]))"
    sel = canonical_select().replace(TABLE, synthetic)
    return sorted((r.content_key, r.platform, r.external_id) for r in client.query(sel).result())


def test_the_original_channel_wins_even_when_the_original_has_no_timestamp_and_the_copy_has_one(client):
    got = run(client, [("a", "youtube", "gol-impossibili", "old", None, None),
                       ("a", "youtube", "calciovich", "new", "2026-10-07 10:30:00", "2026-10-07 10:30:00")])
    assert got == [("a", "youtube", "old")]


def test_the_original_wins_when_both_have_no_timestamp_and_the_copy_id_sorts_first(client):
    got = run(client, [("a", "youtube", "gol-impossibili", "zzz-old", None, None),
                       ("a", "youtube", "calciovich", "aaa-new", None, None)])
    assert got == [("a", "youtube", "zzz-old")]


def test_the_original_wins_even_when_the_copy_is_older_in_time(client):
    got = run(client, [("a", "youtube", "gol-impossibili", "old", "2026-10-07 10:00:00", None),
                       ("a", "youtube", "calciovich", "new", "2026-01-01 00:00:00", None)])
    assert got == [("a", "youtube", "old")]


def test_content_only_on_the_new_channel_keeps_its_single_row(client):
    assert run(client, [("n", "youtube", "calciovich", "new", None, None)]) == [("n", "youtube", "new")]


def test_other_platforms_are_untouched_and_each_platform_keeps_one_row(client):
    got = run(client, [("a", "youtube", "gol-impossibili", "o", None, None),
                       ("a", "youtube", "calciovich", "n", None, None),
                       ("a", "instagram", "calciovich", "ig", None, None),
                       ("a", "tiktok", "calciovich", "tt", None, None)])
    assert got == [("a", "instagram", "ig"), ("a", "tiktok", "tt"), ("a", "youtube", "o")]


def test_a_row_without_channel_key_loses_to_the_original_channel(client):
    got = run(client, [("a", "youtube", None, "legacy-null", None, None),
                       ("a", "youtube", "gol-impossibili", "old", None, None)])
    assert got == [("a", "youtube", "old")]


# ---------------------------------------------------------------- engagement views (two channels)

PREFIX = "`calciovich-video-analytics.calciovich_content."


def view_sql(name):
    sql = open(VIEWS, encoding="utf-8").read()
    m = re.search(r"CREATE OR REPLACE VIEW `[^`]*\." + re.escape(name) + r"` AS\n(.*?);\s*(?=\n--|\Z)", sql, re.S)
    assert m, f"{name} non trovata in views.sql"
    return m.group(1)


def struct_rows(rows):
    """rows: list of dicts with python scalars -> an inline typed UNNEST (typed NULLs: an untyped one breaks the supertype)."""
    def lit(k, v):
        if k in ("snapshot_at",):
            return f"TIMESTAMP '{v}'"
        if isinstance(v, int):
            return str(v)
        return f"'{v}'" if v is not None else "CAST(NULL AS STRING)"
    return "(SELECT * FROM UNNEST([" + ",\n".join(
        "STRUCT(" + ", ".join(f"{lit(k, v)} AS {k}" for k, v in r.items()) + ")" for r in rows) + "]))"


def render(name, tables):
    """The view's SELECT with every referenced table/view replaced by an inline synthetic or by the (rendered) view it refers to."""
    sql = view_sql(name)
    for t in re.findall(re.escape(PREFIX) + r"([A-Za-z_]+)`", sql):
        if t in tables:
            repl = struct_rows(tables[t])
        else:
            repl = "(" + render(t, tables) + ")"
        sql = sql.replace(f"{PREFIX}{t}`", repl)
    return sql


def run_view(client, name, tables):
    return [dict(r) for r in client.query(render(name, tables)).result()]


def snap(video, ts, views, channel, likes=0, comments=0, content="c1"):
    return {"video_id": video, "content_key": content, "snapshot_at": ts, "views": views, "likes": likes,
            "comments": comments, "channel_key": channel}


def test_canonical_engagement_collapses_the_duplicate_written_by_two_writers(client):
    rows = [snap("v", "2026-10-06 10:00:00", 5, "gol-impossibili"), snap("v", "2026-10-06 10:00:00", 5, "gol-impossibili")]
    got = run_view(client, "v_engagement_canonico", {"fct_youtube_engagement_snapshot": rows})
    assert len(got) == 1


def test_canonical_engagement_reads_a_legacy_null_channel_as_the_original_channel(client):
    rows = [snap("v", "2026-10-06 10:00:00", 5, None)]
    got = run_view(client, "v_engagement_canonico", {"fct_youtube_engagement_snapshot": rows})
    assert got[0]["channel_key"] == "gol-impossibili"


def test_the_same_video_id_and_timestamp_on_two_channels_are_two_rows(client):
    rows = [snap("v", "2026-10-06 10:00:00", 5, "gol-impossibili"), snap("v", "2026-10-06 10:00:00", 2, "calciovich")]
    got = run_view(client, "v_engagement_canonico", {"fct_youtube_engagement_snapshot": rows})
    assert sorted(r["channel_key"] for r in got) == ["calciovich", "gol-impossibili"]


def test_legacy_video_performance_does_not_double_a_republished_content(client):
    snaps = [snap("vOLD", "2026-10-06 10:00:00", 50, "gol-impossibili"),
             snap("vNEW", "2026-10-06 10:00:00", 7, "calciovich")]                 # same content_key c1
    tables = {"fct_youtube_engagement_snapshot": snaps,
              "dim_content": [{"content_key": "c1", "titolo": "T", "categoria": "canonical", "file": "f", "fonte": "x"}],
              "fct_youtube_fixed_window": [{"video_id": "vOLD", "content_key": "c1", "views_day1": 1, "views_day2": 2,
                                            "views_day7": 3, "channel_key": "gol-impossibili"}]}
    got = run_view(client, "mart_video_performance", tables)
    assert [(r["video_id"], r["youtube_views_ultimo_snapshot"], r["views_day7"]) for r in got] == [("vOLD", 50, 3)]


def test_legacy_video_performance_keeps_its_columns(client):
    snaps = [snap("vOLD", "2026-10-06 10:00:00", 50, None)]
    tables = {"fct_youtube_engagement_snapshot": snaps,
              "dim_content": [{"content_key": "c1", "titolo": "T", "categoria": "canonical", "file": "f", "fonte": "x"}],
              "fct_youtube_fixed_window": [{"video_id": "x", "content_key": "zz", "views_day1": 1, "views_day2": 1,
                                            "views_day7": 1, "channel_key": None}]}
    got = run_view(client, "mart_video_performance", tables)
    assert list(got[0].keys()) == ["content_key", "titolo", "categoria", "video_id", "youtube_views_ultimo_snapshot",
                                   "youtube_likes_ultimo_snapshot", "youtube_comments_ultimo_snapshot",
                                   "youtube_ultimo_snapshot_at", "views_day1", "views_day2", "views_day7"]


def test_legacy_daily_engagement_is_the_original_channel_only(client):
    snaps = [snap("vOLD", "2026-10-06 10:00:00", 50, "gol-impossibili"), snap("vNEW", "2026-10-06 10:00:00", 7, "calciovich")]
    tables = {"fct_youtube_engagement_snapshot": snaps,
              "dim_content": [{"content_key": "c1", "titolo": "T", "categoria": "canonical", "file": "f", "fonte": "x"}]}
    got = run_view(client, "mart_daily_engagement", tables)
    assert [r["video_id"] for r in got] == ["vOLD"]


def test_by_channel_views_keep_both_videos_apart_and_never_sum_them(client):
    snaps = [snap("vOLD", "2026-10-06 10:00:00", 50, "gol-impossibili"), snap("vNEW", "2026-10-06 10:00:00", 7, "calciovich")]
    tables = {"fct_youtube_engagement_snapshot": snaps,
              "dim_content": [{"content_key": "c1", "titolo": "T", "categoria": "canonical", "file": "f", "fonte": "x"}],
              "dim_channel": [{"channel_key": "gol-impossibili", "channel_name": "Gol Impossibili"},
                              {"channel_key": "calciovich", "channel_name": "La Vera Storia"}],
              "fct_youtube_fixed_window": [{"video_id": "vOLD", "content_key": "c1", "views_day1": 1, "views_day2": 2,
                                            "views_day7": 3, "channel_key": "gol-impossibili"}]}
    perf = {(r["channel_key"], r["video_id"]): r for r in run_view(client, "mart_video_performance_by_channel", tables)}
    assert set(perf) == {("gol-impossibili", "vOLD"), ("calciovich", "vNEW")}
    assert perf[("gol-impossibili", "vOLD")]["views_day7"] == 3 and perf[("calciovich", "vNEW")]["views_day7"] is None
    daily = run_view(client, "mart_daily_engagement_by_channel", tables)
    assert sorted((r["channel_key"], r["views"]) for r in daily) == [("calciovich", 7), ("gol-impossibili", 50)]
