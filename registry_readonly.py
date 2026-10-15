#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
registry_readonly.py — lettura dei registri di upload in SOLA LETTURA, per chi non pubblica.

PERCHE' ESISTE: `upload_registry.Registry(path)` non e' un lettore. All'apertura crea cartelle, database e schema,
imposta WAL e, se la piattaforma non ha righe, REIMPORTA i JSON legacy (backup superati). Un consumer di analytics
(loader BigQuery, raccolte di metriche, dashboard) che lo usa solo per leggere puo' quindi creare il database con
stato vecchio e trattarlo come vero (audit Codex del 14/10/2026). Qui l'apertura e' `mode=ro`: nessun DDL, nessun
PRAGMA, nessun database creato, nessuna migrazione. In modalita' WAL SQLite puo' comunque lasciare i file ausiliari
`-wal`/`-shm` anche a un lettore (meccanismo normale, non contengono stato nuovo).

CONTRATTO
  read_records(path) -> {key: record}, stessa forma di `Registry(path).data` (stessa piattaforma ricavata dal nome file).
  - DB assente, o presente senza tabella `uploads`  -> RegistryMissing  (fail-closed: non e' "nessun upload")
  - DB con `uploads` ma zero righe per la piattaforma -> {}              (stato legittimo, installazione nuova)
  - DB non leggibile / non SQLite / schema o `meta` malformati -> RegistryReadError
  - lock persistente dopo i tentativi -> RegistryReadBusy
  open_readonly(path) -> connessione `mode=ro` (stesse eccezioni di `read_records` se il DB manca/non si apre) e
  read_records_from_connection(conn, platform) -> {key: record}: per chi deve leggere PIU' cose con lo STESSO snapshot
  (una transazione: `conn.execute("BEGIN")` ... `conn.rollback()`); `read_records(path)` resta il wrapper a una sola lettura.
  Tutte sottoclassi di `upload_registry.RegistryCorrupt`: chi la gestisce gia' resta coperto.
  Una corsa "file creato ma tabella non ancora visibile" (primo avvio di un publisher) viene ritentata poche volte;
  se persiste e' RegistryMissing: il falso negativo temporaneo e' dal lato sicuro (il consumer si ferma).

Dipende da `upload_registry` solo per `RegistryCorrupt`, `_row_to_record`, `state_of`, `DB_NAME` (presenti in
entrambi i repo, privato e pubblico).
"""
import os
import sqlite3
import time

import upload_registry
from upload_registry import RegistryCorrupt

ATTEMPTS = 5
PAUSE = 0.2


class RegistryMissing(RegistryCorrupt):
    """Il database dei registri non esiste (o non ha la tabella `uploads`): non si inventa uno stato vuoto."""


class RegistryReadError(RegistryCorrupt):
    """Il database esiste ma non si legge (non SQLite, permessi, schema o `meta` malformati)."""


class RegistryReadBusy(RegistryCorrupt):
    """Il database resta bloccato da altri processi dopo tutti i tentativi."""


def _platform(path):
    return os.path.basename(path).replace("-uploads.json", "").replace(".json", "")


def db_path(path):
    return os.path.join(os.path.dirname(os.path.abspath(path)) or ".", upload_registry.DB_NAME)


def _uri(db):
    """URI SQLite `mode=ro` con il percorso ESCAPATO: un `?`, un `#` o un `%xx` nel nome di una cartella non devono
    cambiare il file aperto (audit Codex 15/10/2026)."""
    from urllib.parse import quote
    return f"file:{quote(os.path.abspath(db), safe='/')}?mode=ro"


def _read_once(db, platform):
    """Un tentativo: apertura -> verifica schema -> SELECT, con una connessione nuova chiusa in `finally`.
    Ritorna ('ok', records) | ('no_table', None). Solleva sqlite3.Error per l'esito da classificare fuori."""
    conn = sqlite3.connect(_uri(db), uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        has_table = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'uploads'").fetchone()
        if not has_table:
            return "no_table", None
        rows = conn.execute("SELECT * FROM uploads WHERE platform = ?", (platform,)).fetchall()
        return "ok", {row["key"]: upload_registry._row_to_record(row) for row in rows}
    finally:
        conn.close()


def open_readonly(path):
    """Connessione `mode=ro` al DB dei registri accanto a `path`, con `row_factory` pronta. Il chiamante la chiude.
    DB assente -> RegistryMissing, non apribile -> RegistryReadError/RegistryReadBusy (stesse regole di read_records)."""
    db = db_path(path)
    try:
        os.stat(db)
    except FileNotFoundError:
        raise RegistryMissing(f"{db}: database dei registri assente (nessuna lettura: non lo si crea)") from None
    except OSError as exc:
        raise RegistryReadError(f"{db}: database non raggiungibile: {exc}") from exc
    last = None
    for attempt in range(ATTEMPTS):
        try:
            conn = sqlite3.connect(_uri(db), uri=True, timeout=5)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            transient = "locked" in msg or "busy" in msg
            retryable = transient or "unable to open" in msg or "disk i/o" in msg
            last = exc
            if retryable and attempt < ATTEMPTS - 1:
                time.sleep(PAUSE)
                continue
            if transient:
                raise RegistryReadBusy(f"{db}: ancora bloccato dopo {attempt + 1} tentativi: {exc}") from exc
            raise RegistryReadError(f"{db}: non apribile: {exc}") from exc     # permessi, I/O permanente, percorso errato
        except sqlite3.DatabaseError as exc:
            raise RegistryReadError(f"{db}: stato dei registri non leggibile: {exc}") from exc
    raise RegistryReadError(f"{db}: apertura non riuscita: {last}")   # non raggiungibile in pratica


def read_records_from_connection(conn, platform):
    """Come read_records, ma su una connessione GIA' aperta (stesso snapshot per piu' letture).
    Senza tabella `uploads` -> RegistryMissing. Errori SQL/dati malformati -> RegistryReadError."""
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'uploads'").fetchone():
            raise RegistryMissing("database dei registri presente ma senza tabella `uploads`")
        rows = conn.execute("SELECT * FROM uploads WHERE platform = ?", (platform,)).fetchall()
        return {row["key"]: upload_registry._row_to_record(row) for row in rows}
    except RegistryMissing:
        raise
    except sqlite3.OperationalError as exc:
        msg = str(exc).lower()
        if "locked" in msg or "busy" in msg:
            raise RegistryReadBusy(f"registri bloccati: {exc}") from exc
        raise RegistryReadError(f"lettura impossibile: {exc}") from exc
    except (sqlite3.DatabaseError, ValueError, TypeError, KeyError, IndexError) as exc:
        raise RegistryReadError(f"stato dei registri non leggibile o malformato: {exc}") from exc


def read_records(path):
    db = db_path(path)
    try:
        os.stat(db)
    except FileNotFoundError:
        raise RegistryMissing(f"{db}: database dei registri assente (nessuna lettura: non lo si crea ne' si importano "
                              f"i JSON legacy)") from None
    except OSError as exc:                     # permessi/TCC sulla directory: non e' "assente", e' "non leggibile"
        raise RegistryReadError(f"{db}: database non raggiungibile: {exc}") from exc
    platform = _platform(path)
    last = None
    for attempt in range(ATTEMPTS):
        try:
            status, records = _read_once(db, platform)
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            transient = "locked" in msg or "busy" in msg
            retryable = transient or "unable to open" in msg or "disk i/o" in msg
            last = (transient, exc)
            if retryable and attempt < ATTEMPTS - 1:
                time.sleep(PAUSE)
                continue
            if transient:
                raise RegistryReadBusy(f"{db}: ancora bloccato dopo {ATTEMPTS} tentativi: {exc}") from exc
            raise RegistryReadError(f"{db}: lettura impossibile: {exc}") from exc
        except (sqlite3.DatabaseError, ValueError, TypeError, KeyError, IndexError) as exc:
            raise RegistryReadError(f"{db}: stato dei registri non leggibile o malformato: {exc}") from exc
        if status == "ok":
            return records
        if attempt < 2:                        # tabella non ancora visibile: primo avvio di un publisher?
            time.sleep(PAUSE)
            continue
        raise RegistryMissing(f"{db}: presente ma senza tabella `uploads` (non e' lo stato dei registri)")
    raise RegistryReadError(f"{db}: lettura non riuscita: {last[1] if last else '?'}")   # non raggiungibile in pratica
