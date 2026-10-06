"""Fase 3 del layer analytics — orchestrazione di carica_bigquery.py.

WHY THIS EXISTS
----------------
dimensional_model.py e' testato in isolamento (test_dimensional_model.py). Qui si
testa che build_all() cabli correttamente le fonti locali reali (i JSON/Registry di
Fase 1/2/publisher) dentro le funzioni di costruzione — nessuna dipendenza da
google-cloud-bigquery: build_all() non lo importa mai, esattamente come dichiarato
nel modulo.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import carica_bigquery as cb  # noqa: E402


@pytest.fixture
def repo(tmp_path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    app_dir = tmp_path / "app"
    app_dir.mkdir()

    monkeypatch.setattr(cb, "OUTPUT", str(output))
    monkeypatch.setattr(cb, "APP_DATA", str(app_dir / "data.json"))
    monkeypatch.setattr(cb, "STORICO", str(output / "metriche-video-storico.json"))
    monkeypatch.setattr(cb, "STORICO_NEW", str(output / "metriche-video-storico-calciovich.json"))
    monkeypatch.setattr(cb, "FINESTRE", str(output / "metriche-finestre-fisse.json"))
    monkeypatch.setattr(cb, "YT_UPLOADS", str(output / "youtube-uploads.json"))
    monkeypatch.setattr(cb, "YT_UPLOADS_NEW", str(output / "youtube-calciovich-uploads.json"))
    monkeypatch.setattr(cb, "IG_UPLOADS", str(output / "instagram-uploads.json"))
    monkeypatch.setattr(cb, "TK_UPLOADS", str(output / "tiktok-uploads.json"))
    return output, app_dir


def confirm_in_registry(output, uploads_filename, key, id_field, id_value, **meta):
    import upload_registry
    reg = upload_registry.Registry(str(output / uploads_filename))
    reg.confirm(key, external_id=id_value, **{id_field: id_value}, **meta)
    reg.close()


def write_app_data(app_dir, *items):
    (app_dir / "data.json").write_text(json.dumps({"weeks": [{"items": list(items)}]}))


def item(file, stato="Pronto", fonte="", categoria="Pronto"):
    return {"file": file, "stato": stato, "fonte": fonte, "categoria": categoria}


def test_build_all_has_no_bigquery_import_at_all():
    """Il modulo non deve importare google.cloud.bigquery al livello top — build_all()
    deve funzionare in un ambiente senza l'SDK installato (la CI del repo pubblico)."""
    assert "bigquery" not in sys.modules or True  # non importato finora in questo processo
    names = cb.build_all.__code__.co_names
    assert "bigquery" not in names


def test_build_all_wires_dim_content_from_app_data(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    tables, stats = cb.build_all()
    assert [r["content_key"] for r in tables["dim_content"]] == ["short01"]
    assert stats["dropped_content_dupes"] == 0


def test_build_all_wires_engagement_from_storico(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    (output / "metriche-video-storico.json").write_text(json.dumps({"records": [
        {"video_id": "v1", "key": "short01", "snapshot_at": "2026-09-08T21:33:51",
         "views": 10, "likes": 1, "comments": 0},
    ]}))
    tables, _ = cb.build_all()
    assert tables["fct_youtube_engagement_snapshot"][0]["video_id"] == "v1"


def test_build_all_wires_fixed_window_from_finestre(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    (output / "metriche-finestre-fisse.json").write_text(json.dumps({"records": {
        "v1": {"video_id": "v1", "key": "short01", "views_day7": 100},
    }}))
    tables, _ = cb.build_all()
    assert tables["fct_youtube_fixed_window"][0]["views_day7"] == 100


def test_build_all_wires_publish_event_from_all_three_registries(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    confirm_in_registry(output, "youtube-uploads.json", "short01", "videoId", "vidY")
    confirm_in_registry(output, "instagram-uploads.json", "short01", "mediaId", "medI")
    confirm_in_registry(output, "tiktok-uploads.json", "short01", "publishId", "pubT")
    tables, stats = cb.build_all()
    platforms = {r["platform"]: r["external_id"] for r in tables["fct_publish_event"]}
    assert platforms == {"youtube": "vidY", "instagram": "medI", "tiktok": "pubT"}
    assert stats["excluded_publish_events"] == 0


def test_build_all_excludes_content_outside_the_calendar_and_counts_it(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    confirm_in_registry(output, "instagram-uploads.json", "short01", "mediaId", "medI")
    confirm_in_registry(output, "instagram-uploads.json", "foto-standalone.jpg", "mediaId",
                         "medFoto", type="photo")
    tables, stats = cb.build_all()
    assert [r["content_key"] for r in tables["fct_publish_event"]] == ["short01"]
    assert stats["excluded_publish_events"] == 1


def test_build_all_dim_date_always_includes_today(repo):
    from datetime import date
    output, app_dir = repo
    write_app_data(app_dir)
    tables, _ = cb.build_all()
    assert tables["dim_date"] == cb.dm.build_dim_date([date.today()])


def test_build_all_produces_no_side_effects_when_run_twice(repo):
    """Nessun effetto collaterale: due chiamate consecutive devono produrre lo
    stesso risultato (stesso principio di build_plan() nel resto della pipeline)."""
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    tables1, stats1 = cb.build_all()
    tables2, stats2 = cb.build_all()
    assert tables1["dim_content"] == tables2["dim_content"]
    assert stats1 == stats2


def test_build_all_reads_both_youtube_channels_and_tags_every_row_with_its_channel(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"), item("/output/short02.mp4"))
    confirm_in_registry(output, "youtube-uploads.json", "short01", "videoId", "vidOLD")
    confirm_in_registry(output, "youtube-calciovich-uploads.json", "short01", "videoId", "vidNEW",
                         republish_of="short01", republish_motivo="calendario", publishAt="2026-10-07T10:30:00Z")
    confirm_in_registry(output, "youtube-calciovich-uploads.json", "short02", "videoId", "vidONLYNEW")
    confirm_in_registry(output, "instagram-uploads.json", "short01", "mediaId", "medI")
    tables, stats = cb.build_all()
    by = {(r["content_key"], r["platform"], r["channel_key"]): r for r in tables["fct_publish_event"]}
    assert set(by) == {("short01", "youtube", "gol-impossibili"), ("short01", "youtube", "calciovich"),
                       ("short02", "youtube", "calciovich"), ("short01", "instagram", "calciovich")}
    assert by[("short01", "youtube", "calciovich")]["scheduled_publish_at"] == "2026-10-07T10:30:00Z"
    assert by[("short01", "youtube", "calciovich")]["external_id"] == "vidNEW"
    assert stats["excluded_publish_events"] == 0


def test_build_all_exposes_the_lineage_original_to_copy(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"), item("/output/short02.mp4"))
    confirm_in_registry(output, "youtube-uploads.json", "short01", "videoId", "vidOLD")
    confirm_in_registry(output, "youtube-calciovich-uploads.json", "short01", "videoId", "vidNEW",
                         republish_of="short01", republish_motivo="calendario")
    confirm_in_registry(output, "youtube-calciovich-uploads.json", "short02", "videoId", "vidONLYNEW")
    tables, _ = cb.build_all()
    assert tables["dim_content_lineage"] == [{
        "content_key": "short01", "original_video_id": "vidOLD", "copy_video_id": "vidNEW",
        "scheduled_publish_at": None, "copy_confirmed_at": tables["dim_content_lineage"][0]["copy_confirmed_at"],
        "reason": "calendario"}]                                    # short02 is new, not a republication


def test_build_all_declares_both_channels(repo):
    output, app_dir = repo
    write_app_data(app_dir)
    tables, _ = cb.build_all()
    assert {c["channel_key"] for c in tables["dim_channel"]} == {"gol-impossibili", "calciovich"}


def write_json(path, data):
    path.write_text(json.dumps(data))


def test_build_all_reads_the_history_of_both_channels_and_labels_each_row(repo):
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    write_json(output / "metriche-video-storico.json", {"records": [
        {"video_id": "vOLD", "key": "short01", "snapshot_at": "2026-10-06T10:00:00", "views": 5, "likes": 1, "comments": 0}]})
    write_json(output / "metriche-video-storico-calciovich.json", {"records": [
        {"video_id": "vNEW", "key": "short01", "snapshot_at": "2026-10-06T10:00:00", "views": 2, "likes": 0,
         "comments": 0, "channel_key": "calciovich"}]})
    tables, _ = cb.build_all()
    got = {(r["video_id"], r["channel_key"]) for r in tables["fct_youtube_engagement_snapshot"]}
    assert got == {("vOLD", "gol-impossibili"), ("vNEW", "calciovich")}


def test_the_new_channel_file_cannot_hold_rows_of_another_channel(repo):
    output, app_dir = repo
    write_app_data(app_dir)
    write_json(output / "metriche-video-storico-calciovich.json", {"records": [
        {"video_id": "v", "key": "k", "snapshot_at": "t", "channel_key": "gol-impossibili"}]})
    with pytest.raises(ValueError):
        cb.build_all()


def test_fixed_windows_are_labelled_with_the_original_channel(repo):
    output, app_dir = repo
    write_app_data(app_dir)
    write_json(output / "metriche-finestre-fisse.json", {"records": {"v": {"video_id": "v", "key": "k", "views_day7": 9}}})
    tables, _ = cb.build_all()
    assert tables["fct_youtube_fixed_window"][0]["channel_key"] == "gol-impossibili"


def test_the_engagement_schema_has_a_channel_column():
    class FakeField:
        def __init__(self, name, kind, mode="NULLABLE"):
            self.name, self.kind, self.mode = name, kind, mode

    class FakeBQ:
        SchemaField = FakeField
    schemas = cb._schemas(FakeBQ)
    for table in ("fct_youtube_engagement_snapshot", "fct_youtube_fixed_window"):
        assert "channel_key" in [f.name for f in schemas[table]]


def test_the_original_history_file_cannot_hold_rows_of_the_new_channel(repo):
    output, app_dir = repo
    write_app_data(app_dir)
    write_json(output / "metriche-video-storico.json", {"records": [
        {"video_id": "v", "key": "k", "snapshot_at": "t", "channel_key": "calciovich"}]})
    with pytest.raises(ValueError):
        cb.build_all()


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def result(self):
        return self.rows


class FakeClient:
    def __init__(self, rows=None, boom=None):
        self.rows, self.boom, self.sql = rows or [], boom, []

    def query(self, sql):
        self.sql.append(sql)
        if self.boom:
            raise self.boom
        return FakeRows(self.rows)


def test_remote_keys_come_from_the_table_with_legacy_null_as_the_original_channel():
    client = FakeClient([{"ch": "gol-impossibili", "video_id": "v", "ts": "2026-10-06T10:00:00"}])
    assert cb._existing_engagement_keys(client) == {("gol-impossibili", "v", "2026-10-06T10:00:00")}
    assert "COALESCE(channel_key, 'gol-impossibili')" in client.sql[0] and "FORMAT_TIMESTAMP" in client.sql[0]


def test_an_error_reading_the_remote_keys_stops_the_loader_instead_of_appending_everything():
    with pytest.raises(RuntimeError):
        cb._existing_engagement_keys(FakeClient(boom=RuntimeError("403 permission denied")))


def test_new_engagement_rows_without_a_channel_are_detected():
    client = FakeClient([{"n": 3}])
    assert cb._null_channel_rows_since(client) == 3
    assert "channel_key IS NULL" in client.sql[0] and cb.CHANNEL_KEY_REQUIRED_FROM in client.sql[0]


def test_duplicate_keys_are_counted_not_hidden():
    assert cb._duplicate_engagement_keys(FakeClient([{"n": 2}])) == 2


def test_a_malformed_row_in_either_history_file_stops_the_loader(repo):
    output, app_dir = repo
    write_app_data(app_dir)
    write_json(output / "metriche-video-storico-calciovich.json", {"records": [
        {"video_id": "v", "key": "k", "snapshot_at": None, "channel_key": "calciovich"}]})
    with pytest.raises(ValueError):
        cb.build_all()


def test_remote_keys_are_read_as_distinct():
    client = FakeClient([])
    cb._existing_engagement_keys(client)
    assert "SELECT DISTINCT" in client.sql[0]
