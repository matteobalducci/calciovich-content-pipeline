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
