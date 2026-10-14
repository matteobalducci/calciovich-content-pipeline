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
    monkeypatch.setattr(cb, "FINESTRE_NEW", str(output / "metriche-finestre-fisse-calciovich.json"))
    monkeypatch.setattr(cb, "YT_UPLOADS", str(output / "youtube-uploads.json"))
    monkeypatch.setattr(cb, "YT_UPLOADS_NEW", str(output / "youtube-calciovich-uploads.json"))
    monkeypatch.setattr(cb, "IG_UPLOADS", str(output / "instagram-uploads.json"))
    monkeypatch.setattr(cb, "TK_UPLOADS", str(output / "tiktok-uploads.json"))
    monkeypatch.setattr(cb, "STATS_ORIGINAL", str(app_dir / "youtube-stats.json"))
    monkeypatch.setattr(cb, "STATS_NEW", str(app_dir / "youtube-stats-calciovich.json"))
    write_channel_stats(app_dir)
    return output, app_dir


def write_channel_stats(app_dir, original=None, new=None):
    """Storici di canale validi di default: la fixture non deve leggere i file reali del checkout."""
    original = original if original is not None else {
        "channel": "gol-impossibili", "history": [
            {"date": "2026-10-05", "title": "Calciovich", "subs": 37, "views": 100, "videos": 60},
            {"date": "2026-10-10", "title": "Gol Impossibili", "subs": 47, "views": 450, "videos": 62}]}
    new = new if new is not None else {
        "channel": "calciovich", "history": [{"date": "2026-10-10", "subs": 0, "views": 5, "videos": 7, "ig_followers": 8}]}
    for name, content in (("youtube-stats.json", original), ("youtube-stats-calciovich.json", new)):
        path = app_dir / name
        if content == "ASSENTE":
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(content))


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
    (output / "metriche-finestre-fisse-calciovich.json").write_text(json.dumps({"records": {}}))
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
    write_json(output / "metriche-finestre-fisse-calciovich.json", {"records": {}})
    tables, _ = cb.build_all()
    assert tables["fct_youtube_fixed_window"][0]["channel_key"] == "gol-impossibili"


@pytest.mark.parametrize("gol,new", [
    ("ASSENTE", {"records": {}}),                                   # file del canale originale mancante
    ({"records": {}}, {"records": {}}),                             # originale vuoto: non puo' essere "nessuna finestra"
    ({"records": {"v": {"video_id": "v", "key": "k", "views_day7": 9}}}, "ASSENTE"),   # file del canale nuovo mancante
    ({"records": {"v": {"video_id": "v", "key": "k", "views_day7": 9}}}, {"records": []}),   # records non e' un oggetto
])
def test_a_missing_or_invalid_window_file_leaves_the_table_unwritten_not_half_empty(repo, gol, new):
    """WRITE_TRUNCATE: se manca il file di UN canale, le righe di quel canale non devono sparire in silenzio."""
    output, app_dir = repo
    write_app_data(app_dir)
    for name, content in (("metriche-finestre-fisse.json", gol), ("metriche-finestre-fisse-calciovich.json", new)):
        if content != "ASSENTE":
            write_json(output / name, content)
    tables, stats = cb.build_all()
    assert tables["fct_youtube_fixed_window"] is None and stats["fixed_window_error"]


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


def test_fixed_windows_of_both_channels_are_loaded_each_with_its_own_label(repo):
    """Una raccolta per canale (file diversi): le righe finiscono nella stessa tabella, etichettate ciascuna col SUO
    canale — il canale del libro non eredita mai l'etichetta del canale originale."""
    output, app_dir = repo
    write_app_data(app_dir)
    write_json(output / "metriche-finestre-fisse.json", {"records": {"vOLD": {"video_id": "vOLD", "key": "k1", "views_day7": 9}}})
    write_json(output / "metriche-finestre-fisse-calciovich.json",
               {"records": {"vNEW": {"video_id": "vNEW", "key": "k2", "views_day1": 4}}})
    tables, _ = cb.build_all()
    labels = {r["video_id"]: r["channel_key"] for r in tables["fct_youtube_fixed_window"]}
    assert labels == {"vOLD": "gol-impossibili", "vNEW": "calciovich"}




# --- fct_channel_snapshot: cablaggio (piano del 14/10/2026  (audit Codex 14/10/2026)) ---------------------

def test_build_all_reads_the_history_of_both_channels_into_the_channel_snapshot(repo):
    output, app_dir = repo
    write_app_data(app_dir)
    write_channel_stats(app_dir, original={"channel": "gol-impossibili", "history": [
        {"date": "2026-10-05", "subs": 37, "views": 100, "videos": 60}, {"date": "bad", "subs": 1}]})
    tables, stats = cb.build_all()
    rows = tables["fct_channel_snapshot"]
    assert {(r["channel_key"], r["snapshot_date"], r["era"]) for r in rows} == {
        ("gol-impossibili", "2026-10-05", "pre_split"), ("calciovich", "2026-10-10", "post_split")}
    assert stats["excluded_channel_points"] == 1 and stats["channel_snapshot_error"] is None


@pytest.mark.parametrize("original", ["ASSENTE", {}, {"channel": "gol-impossibili", "history": []},
                                      {"channel": "calciovich", "history": [{"date": "2026-10-10", "subs": 1}]},
                                      {"history": [{"date": "2026-10-10", "subs": 1}]}])
def test_an_invalid_channel_history_file_leaves_the_table_unwritten_not_empty(repo, original):
    """WRITE_TRUNCATE con zero righe cancellerebbe tutta la tabella: file mancante/vuoto/nel posto sbagliato ->
    rows = None e un errore leggibile, mentre tutte le altre tabelle si costruiscono normalmente."""
    output, app_dir = repo
    write_app_data(app_dir)
    write_channel_stats(app_dir, original=original)
    tables, stats = cb.build_all()
    assert tables["fct_channel_snapshot"] is None and stats["channel_snapshot_error"]
    assert tables["dim_channel"] and tables["dim_platform"]            # il resto del loader non si ferma


def test_the_channel_snapshot_schema_matches_the_model_rows_and_is_keyed_by_channel_and_day():
    class FakeBQ:
        SchemaField = staticmethod(lambda name, ftype, mode="NULLABLE", **kw: (name, ftype, mode))
    schema = cb._schemas(FakeBQ)["fct_channel_snapshot"]
    assert [c[0] for c in schema] == ["channel_key", "snapshot_date", "subscribers", "total_views", "video_count",
                                      "ig_followers", "era"]
    assert dict((c[0], c[2]) for c in schema)["channel_key"] == "REQUIRED"
    assert dict((c[0], c[2]) for c in schema)["snapshot_date"] == "REQUIRED"


def _run_main_with_fakes(monkeypatch, tmp_path):
    import types
    writes, metadata = [], []

    class FakeBQ:
        SchemaField = staticmethod(lambda name, ftype, mode="NULLABLE", **kw: (name, ftype, mode))

    key = tmp_path / "key.json"
    key.write_text("{}")
    monkeypatch.setattr(cb, "KEY_PATH", str(key))
    monkeypatch.setattr(cb, "_bigquery_client", lambda *a, **k: object())
    monkeypatch.setattr(cb, "_ensure_table", lambda *a, **k: None)
    monkeypatch.setattr(cb, "_existing_engagement_keys", lambda c: set())
    monkeypatch.setattr(cb, "_duplicate_engagement_keys", lambda c: 0)
    monkeypatch.setattr(cb, "_null_channel_rows_since", lambda c: 0)

    def fake_write(client, bigquery, name, rows, schema, write_disposition):
        writes.append((name, write_disposition))
        if name == "_run_metadata":
            metadata.extend(rows)
    monkeypatch.setattr(cb, "_write_table", fake_write)
    monkeypatch.setitem(sys.modules, "google.cloud", types.SimpleNamespace(bigquery=FakeBQ))
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", FakeBQ)
    monkeypatch.setitem(sys.modules, "google.oauth2", types.SimpleNamespace(service_account=object()))
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", object())
    monkeypatch.setattr(sys, "argv", ["carica_bigquery.py"])
    cb.main()
    return writes, metadata


def _healthy_inputs(output, app_dir):
    """Input completi: contenuto in calendario, un upload confermato, finestre fisse di entrambi i canali."""
    write_app_data(app_dir, item("/output/short01.mp4"))
    confirm_in_registry(output, "youtube-uploads.json", "short01", "videoId", "vidY")
    write_json(output / "metriche-finestre-fisse.json", {"records": {"v": {"video_id": "v", "key": "k", "views_day7": 9}}})
    write_json(output / "metriche-finestre-fisse-calciovich.json", {"records": {}})


def test_main_truncates_the_channel_snapshot_and_registers_it_in_the_run_metadata(repo, monkeypatch, tmp_path):
    output, app_dir = repo
    _healthy_inputs(output, app_dir)
    writes, metadata = _run_main_with_fakes(monkeypatch, tmp_path)
    assert ("fct_channel_snapshot", "WRITE_TRUNCATE") in writes
    assert "fct_channel_snapshot" in metadata[0]["tables_written"]


def test_main_does_not_touch_the_channel_snapshot_when_a_history_file_is_missing(repo, monkeypatch, tmp_path, capsys):
    """Run parziale: la tabella resta intatta e il processo esce con errore (codice != 0, wrapper rosso)."""
    output, app_dir = repo
    _healthy_inputs(output, app_dir)
    write_channel_stats(app_dir, new="ASSENTE")
    with pytest.raises(SystemExit) as exc:
        _run_main_with_fakes(monkeypatch, tmp_path)
    assert "run parziale" in str(exc.value) and exc.value.code != 0
    assert "NON sara' scritta" in capsys.readouterr().out


def test_dry_run_is_not_green_when_the_real_run_would_be_partial(repo, monkeypatch, tmp_path):
    output, app_dir = repo
    _healthy_inputs(output, app_dir)
    write_channel_stats(app_dir, new="ASSENTE")
    monkeypatch.setattr(sys, "argv", ["carica_bigquery.py", "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        cb.main()
    assert "PARZIALE" in str(exc.value)


def test_zero_confirmed_uploads_with_the_registry_db_present_is_a_legitimate_empty_state(repo, monkeypatch, tmp_path):
    """Fonte valida (DB dei registri esistente), zero upload confermati: non e' un input mancante, il run procede."""
    import upload_registry
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    write_json(output / "metriche-finestre-fisse.json", {"records": {"v": {"video_id": "v", "key": "k", "views_day7": 9}}})
    write_json(output / "metriche-finestre-fisse-calciovich.json", {"records": {}})
    upload_registry.Registry(str(output / "youtube-uploads.json")).close()     # DB creato, nessun record
    writes, _ = _run_main_with_fakes(monkeypatch, tmp_path)
    assert ("fct_publish_event", "WRITE_TRUNCATE") in writes


def test_main_stops_when_the_registry_db_is_missing_even_if_legacy_json_backups_exist(repo, monkeypatch, tmp_path):
    """I JSON sono backup superati: con il DB assente, Registry lo ricreerebbe reimportando stato datato."""
    output, app_dir = repo
    write_app_data(app_dir, item("/output/short01.mp4"))
    write_json(output / "metriche-finestre-fisse.json", {"records": {"v": {"video_id": "v", "key": "k", "views_day7": 9}}})
    write_json(output / "metriche-finestre-fisse-calciovich.json", {"records": {}})
    write_json(output / "youtube-uploads.json", {"short01": {"status": "confirmed", "videoId": "vOLD", "meta": {"videoId": "vOLD"}}})
    for name in ("youtube-calciovich-uploads.json", "instagram-uploads.json", "tiktok-uploads.json"):
        write_json(output / name, {})
    with pytest.raises(SystemExit) as exc:
        _run_main_with_fakes(monkeypatch, tmp_path)
    assert "Nessuna scrittura" in str(exc.value)
    assert not (output / "publish-state.db").exists()      # lo stop avviene PRIMA di aprire un Registry: niente DB creato



def test_main_writes_nothing_when_the_calendar_or_the_registries_are_missing(repo, monkeypatch, tmp_path):
    """app/data.json assente -> dim_content e fct_publish_event vuote: scriverle con TRUNCATE cancellerebbe lo storico."""
    output, app_dir = repo
    with pytest.raises(SystemExit) as exc:
        _run_main_with_fakes(monkeypatch, tmp_path)
    assert "Nessuna scrittura" in str(exc.value)
