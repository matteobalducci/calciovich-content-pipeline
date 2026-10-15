"""registry_readonly: lettura dei registri di upload SENZA creare ne' migrare nulla (audit Codex del 14/10/2026).

Il difetto che previene: un consumer di analytics apriva `Registry(path)` solo per leggere; con `publish-state.db`
assente quella apertura lo creava e reimportava i JSON legacy, e il consumer scriveva poi quello stato vecchio su
BigQuery come se fosse vero."""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import registry_readonly as rr  # noqa: E402
import upload_registry  # noqa: E402


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(rr, "PAUSE", 0)


def _files(directory):
    return sorted(p.name for p in directory.iterdir())


def _populate(directory):
    """DB popolato da Registry, come fanno i publisher: due piattaforme con la STESSA chiave."""
    yt = upload_registry.Registry(str(directory / "youtube-uploads.json"))
    yt.confirm("short01.vert", external_id="vidYT", videoId="vidYT")
    yt.close()
    ig = upload_registry.Registry(str(directory / "instagram-uploads.json"))
    ig.confirm("short01.vert", external_id="medIG", mediaId="medIG")
    ig.close()


def test_a_missing_database_raises_and_nothing_is_created_even_with_legacy_json_present(tmp_path):
    (tmp_path / "youtube-uploads.json").write_text(json.dumps({"short01.vert": {"videoId": "vidOLD"}}))
    with pytest.raises(rr.RegistryMissing):
        rr.read_records(str(tmp_path / "youtube-uploads.json"))
    assert _files(tmp_path) == ["youtube-uploads.json"]             # niente DB, niente -wal/-shm, niente import


def test_a_database_with_only_the_spend_table_is_not_the_registry_state(tmp_path):
    conn = sqlite3.connect(tmp_path / upload_registry.DB_NAME)
    conn.execute("CREATE TABLE spend (x TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(rr.RegistryMissing):
        rr.read_records(str(tmp_path / "youtube-uploads.json"))


def test_a_database_with_an_empty_uploads_table_is_a_legitimate_empty_state(tmp_path):
    upload_registry.Registry(str(tmp_path / "youtube-uploads.json")).close()      # schema creato, nessun record
    assert rr.read_records(str(tmp_path / "youtube-uploads.json")) == {}


def test_records_match_registry_data_and_platforms_do_not_leak(tmp_path):
    _populate(tmp_path)
    for name in ("youtube-uploads.json", "instagram-uploads.json"):
        reg = upload_registry.Registry(str(tmp_path / name))
        expected = reg.data
        reg.close()
        assert rr.read_records(str(tmp_path / name)) == expected
    assert rr.read_records(str(tmp_path / "youtube-uploads.json"))["short01.vert"]["videoId"] == "vidYT"
    assert rr.read_records(str(tmp_path / "instagram-uploads.json"))["short01.vert"]["mediaId"] == "medIG"
    assert rr.read_records(str(tmp_path / "tiktok-uploads.json")) == {}                # piattaforma senza righe


def test_reading_writes_nothing_and_leaves_no_connection_open(tmp_path):
    _populate(tmp_path)
    db = tmp_path / upload_registry.DB_NAME
    before_files, before_bytes = _files(tmp_path), db.read_bytes()
    for _ in range(3):
        rr.read_records(str(tmp_path / "youtube-uploads.json"))
    assert db.read_bytes() == before_bytes                                 # il file del database non cambia
    # in modalita' WAL anche un lettore puo' lasciare i file ausiliari -wal/-shm (meccanismo normale di SQLite):
    # nient'altro deve comparire
    assert set(_files(tmp_path)) - set(before_files) <= {upload_registry.DB_NAME + "-wal", upload_registry.DB_NAME + "-shm"}
    # connessione rilasciata: un writer puo' aprire, scrivere e chiudere subito dopo
    reg = upload_registry.Registry(str(tmp_path / "youtube-uploads.json"))
    reg.confirm("short02.vert", external_id="vid2", videoId="vid2")
    reg.close()
    assert "short02.vert" in rr.read_records(str(tmp_path / "youtube-uploads.json"))


def test_a_non_sqlite_file_is_a_read_error_not_a_raw_exception(tmp_path):
    (tmp_path / upload_registry.DB_NAME).write_bytes(b"questo non e' un database sqlite" * 20)
    with pytest.raises(rr.RegistryReadError):
        rr.read_records(str(tmp_path / "youtube-uploads.json"))


def test_a_table_with_missing_columns_is_a_read_error(tmp_path):
    conn = sqlite3.connect(tmp_path / upload_registry.DB_NAME)
    conn.execute("CREATE TABLE uploads (platform TEXT, key TEXT)")
    conn.execute("INSERT INTO uploads VALUES ('youtube', 'k')")
    conn.commit()
    conn.close()
    with pytest.raises(rr.RegistryReadError):
        rr.read_records(str(tmp_path / "youtube-uploads.json"))


def test_malformed_meta_is_a_read_error(tmp_path):
    upload_registry.Registry(str(tmp_path / "youtube-uploads.json")).close()
    conn = sqlite3.connect(tmp_path / upload_registry.DB_NAME)
    conn.execute("INSERT INTO uploads (platform, key, state, meta) VALUES ('youtube', 'k', 'confirmed', '{non json')")
    conn.commit()
    conn.close()
    with pytest.raises(rr.RegistryReadError):
        rr.read_records(str(tmp_path / "youtube-uploads.json"))


def test_the_table_appearing_late_is_retried_not_declared_missing(tmp_path, monkeypatch):
    """Primo avvio di un publisher: file creato, tabella non ancora visibile. Un secondo tentativo la vede."""
    upload_registry.Registry(str(tmp_path / "youtube-uploads.json")).close()
    real, calls = rr._read_once, []

    def flaky(db, platform):
        calls.append(1)
        return ("no_table", None) if len(calls) == 1 else real(db, platform)
    monkeypatch.setattr(rr, "_read_once", flaky)
    assert rr.read_records(str(tmp_path / "youtube-uploads.json")) == {} and len(calls) == 2


def test_a_persistent_lock_is_busy_after_all_attempts(tmp_path, monkeypatch):
    upload_registry.Registry(str(tmp_path / "youtube-uploads.json")).close()
    calls = []

    def locked(db, platform):
        calls.append(1)
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(rr, "_read_once", locked)
    with pytest.raises(rr.RegistryReadBusy):
        rr.read_records(str(tmp_path / "youtube-uploads.json"))
    assert len(calls) == rr.ATTEMPTS


def test_a_permission_style_failure_is_a_read_error_not_busy(tmp_path, monkeypatch):
    upload_registry.Registry(str(tmp_path / "youtube-uploads.json")).close()

    def denied(db, platform):
        raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(rr, "_read_once", denied)
    with pytest.raises(rr.RegistryReadError) as exc:
        rr.read_records(str(tmp_path / "youtube-uploads.json"))
    assert not isinstance(exc.value, rr.RegistryReadBusy)


def test_the_errors_are_registry_corrupt_so_existing_handlers_still_apply():
    for cls in (rr.RegistryMissing, rr.RegistryReadError, rr.RegistryReadBusy):
        assert issubclass(cls, upload_registry.RegistryCorrupt)


def test_a_permission_error_on_the_directory_is_a_read_error_not_a_missing_database(tmp_path, monkeypatch):
    real_stat = os.stat

    def denied(target, *a, **k):
        if str(target).endswith(upload_registry.DB_NAME):
            raise PermissionError(13, "Operation not permitted")
        return real_stat(target, *a, **k)
    monkeypatch.setattr(os, "stat", denied)
    with pytest.raises(rr.RegistryReadError) as exc:
        rr.read_records(str(tmp_path / "youtube-uploads.json"))
    assert not isinstance(exc.value, rr.RegistryMissing)


def test_a_shared_connection_reads_every_platform_with_the_same_shape_as_read_records(tmp_path):
    _populate(tmp_path)
    path = str(tmp_path / "youtube-uploads.json")
    conn = rr.open_readonly(path)
    try:
        assert rr.read_records_from_connection(conn, "youtube") == rr.read_records(path)
        assert rr.read_records_from_connection(conn, "instagram")["short01.vert"]["external_id"] == "medIG"
        assert rr.read_records_from_connection(conn, "tiktok") == {}        # piattaforma senza righe: stato legittimo
    finally:
        conn.close()


def test_open_readonly_never_creates_the_database(tmp_path):
    with pytest.raises(rr.RegistryMissing):
        rr.open_readonly(str(tmp_path / "youtube-uploads.json"))
    assert _files(tmp_path) == []


def test_one_transaction_does_not_see_a_concurrent_commit_between_two_reads(tmp_path):
    _populate(tmp_path)
    path = str(tmp_path / "youtube-uploads.json")
    writer = upload_registry.Registry(path)
    conn = rr.open_readonly(path)
    try:
        conn.execute("BEGIN")
        before = rr.read_records_from_connection(conn, "youtube")
        writer.confirm("short02.vert", external_id="vidNEW", videoId="vidNEW")       # commit dopo la prima lettura
        after = rr.read_records_from_connection(conn, "youtube")
        conn.rollback()
    finally:
        conn.close()
        writer.close()
    assert set(before) == set(after) == {"short01.vert"}
    assert set(rr.read_records(path)) == {"short01.vert", "short02.vert"}            # una lettura nuova lo vede


def test_a_shared_connection_on_a_database_without_the_uploads_table_is_missing(tmp_path):
    conn = sqlite3.connect(tmp_path / upload_registry.DB_NAME)
    conn.execute("CREATE TABLE spend (x TEXT)")
    conn.commit()
    conn.close()
    ro = rr.open_readonly(str(tmp_path / "youtube-uploads.json"))
    try:
        with pytest.raises(rr.RegistryMissing):
            rr.read_records_from_connection(ro, "youtube")
    finally:
        ro.close()


def test_open_readonly_permanent_open_errors_are_read_errors_not_busy(tmp_path, monkeypatch):
    """Permessi / I/O permanente: dopo i tentativi e' RegistryReadError (il contratto), non 'busy'."""
    upload_registry.Registry(str(tmp_path / "youtube-uploads.json")).close()
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(rr.sqlite3, "connect", boom)
    with pytest.raises(rr.RegistryReadError) as exc:
        rr.open_readonly(str(tmp_path / "youtube-uploads.json"))
    assert not isinstance(exc.value, rr.RegistryReadBusy) and len(calls) == rr.ATTEMPTS


def test_open_readonly_an_unrecognised_operational_error_is_not_busy(tmp_path, monkeypatch):
    upload_registry.Registry(str(tmp_path / "youtube-uploads.json")).close()

    def boom(*a, **k):
        raise sqlite3.OperationalError("qualcosa di inatteso")
    monkeypatch.setattr(rr.sqlite3, "connect", boom)
    with pytest.raises(rr.RegistryReadError) as exc:
        rr.open_readonly(str(tmp_path / "youtube-uploads.json"))
    assert not isinstance(exc.value, rr.RegistryReadBusy)


def test_open_readonly_a_persistent_lock_is_busy(tmp_path, monkeypatch):
    upload_registry.Registry(str(tmp_path / "youtube-uploads.json")).close()

    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(rr.sqlite3, "connect", boom)
    with pytest.raises(rr.RegistryReadBusy):
        rr.open_readonly(str(tmp_path / "youtube-uploads.json"))


@pytest.mark.parametrize("folder", ["con?punto", "con#cancelletto", "con%20percento", "con spazio"])
def test_paths_with_uri_reserved_characters_open_the_right_database(tmp_path, folder):
    d = tmp_path / folder
    d.mkdir()
    reg = upload_registry.Registry(str(d / "youtube-uploads.json"))
    reg.confirm("k", external_id="v", videoId="v")
    reg.close()
    assert "k" in rr.read_records(str(d / "youtube-uploads.json"))
    conn = rr.open_readonly(str(d / "youtube-uploads.json"))
    try:
        assert "k" in rr.read_records_from_connection(conn, "youtube")
    finally:
        conn.close()
