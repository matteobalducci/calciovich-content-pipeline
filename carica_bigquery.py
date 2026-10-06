#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
carica_bigquery.py — Fase 3 del layer analytics: ingestion BigQuery + modello
dimensionale sopra i dati gia' raccolti da Fase 1 (raccogli_metriche_video.py) e
Fase 2 (raccogli_finestre_fisse.py), in preparazione della Fase 4 (Looker Studio).

Progetto GCP dedicato calciovich-video-analytics (isolato da calciovich-analytics,
solo publishing — stessa decisione di Fase 2), dataset calciovich_content, service
account dedicato (calciovich-bigquery-loader) scoped al dataset (WRITER) + al
progetto solo per bigquery.jobUser (permesso minimo verificato creando davvero le
risorse, non assunto sulla carta).

SCOPE ONESTO: le metriche di engagement esistono solo per YouTube — vedi
dimensional_model.py per il perche' di tre fact table distinte invece di una sola
con colonna "platform".

DUE CANALI YOUTUBE (dal 06/10/2026): il canale originale (oggi "Gol Impossibili") e il canale del libro hanno
registri separati. fct_publish_event ha grain (content_key, platform, channel_key); le viste di copertura
passano da v_publish_event_canonico (una riga per contenuto e piattaforma, canale originale per primo) cosi'
restano identiche a prima per i contenuti esistenti. Le statistiche di engagement (fct_youtube_*) restano del
solo canale originale: raccogliere quelle del canale nuovo e' una fase successiva. Rollout fatto in 3 passi
additivi (colonna, viste, righe): vedi ENGINEERING-LOG.md.

SCRITTURA STAGING: WRITE_TRUNCATE per tabella, non MERGE. Ogni fonte locale (i JSON
di Fase 1/2, upload_registry.Registry per le tre piattaforme) e' uno stato pieno e
autoconsistente ad ogni lettura, non un log incrementale — verificato leggendo
merge_records()/merge_windows() (accumulano localmente, riscrivono l'intero file) e
Registry.data (sempre SELECT * sullo stato corrente). Un'istintiva scelta
WRITE_APPEND per una "tabella di staging" romperebbe questa proprieta'.

ECCEZIONE (2026-10-01): fct_youtube_engagement_snapshot e' WRITE_APPEND delle sole
righe piu' recenti di MAX(snapshot_at) gia' su BigQuery, non WRITE_TRUNCATE come le
altre. Da quando la raccolta gira anche da GitHub Actions (backstop per i buchi del
LaunchAgent locale durante sleep prolungati — vedi examples/youtube-stats-cloud.yml), questa e'
l'unica tabella scritta da due processi indipendenti con stati locali diversi: il
Mac continua a vedere solo il proprio storico locale (output/metriche-video-storico.json,
mai sincronizzato con la cloud per scelta — vedi .gitignore), GitHub Actions scrive
una sola riga fresca a ogni run senza vedere lo storico del Mac. Un WRITE_TRUNCATE
da uno dei due cancellerebbe quello che l'altro ha scritto nel frattempo. Le altre
tabelle restano WRITE_TRUNCATE: solo il Mac le scrive (GitHub Actions non ha
app/data.json, le upload-registry, ne' le finestre fisse — vedi il workflow), quindi
non c'e' nessun secondo scrittore con cui andare in conflitto.

_run_metadata e' la SOLA eccezione: WRITE_APPEND deliberato, una riga per run
RIUSCITO, scritta in un unico INSERT alla fine (mai una riga anticipata con
completed_at NULL — un run che crasha a meta' non scrive nessuna riga, punto). I
consumatori (Fase 4) leggono l'ultima riga per completed_at, senza dover filtrare
per NULL. WRITE_TRUNCATE per singola tabella e' atomico (o sostituisce tutto o non
tocca nulla), ma non garantisce le tre tabelle di staging sincronizzate fra loro se
questo script crasha a meta' del proprio lavoro — _run_metadata da' ai consumatori
un modo di sapere qual e' l'ultimo punto nel tempo in cui erano coerenti, senza
transazioni multi-statement (non necessarie per un mart di portfolio).

Nessun controllo di exit-code fra questo step e Fase 1/2 in aggiorna_youtube_stats.sh
(proprieta' esistente e voluta: ogni step si autoprotegge) — questo script tollera
un run in cui Fase 1/2 sono fallite silenziosamente nello stesso ciclo, semplicemente
ricaricando lo stato corrente (eventualmente stantio) delle proprie fonti locali.

USO
  python3 carica_bigquery.py             # costruisce le righe e le scrive su BigQuery
  python3 carica_bigquery.py --dry-run   # costruisce le righe, stampa i conteggi,
                                          # non scrive nulla su BigQuery
"""
import os
import sys
import uuid
from datetime import date, datetime, timezone

import dimensional_model as dm
from metriche_video import load, load_confirmed_uploads, load_confirmed_youtube_uploads

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "output")
APP_DATA = os.path.join(HERE, "app", "data.json")
STORICO = os.path.join(OUTPUT, "metriche-video-storico.json")                       # canale ORIGINALE
STORICO_NEW = os.path.join(OUTPUT, "metriche-video-storico-calciovich.json")        # canale del libro
FINESTRE = os.path.join(OUTPUT, "metriche-finestre-fisse.json")                     # solo canale originale (Analytics)
CHANNEL_IDS = {"gol-impossibili": "UCLPBYAv19aizEYX4MmXV7rA", "calciovich": "UCy1V7Lwaeb8_6iaSEzSOtPA"}
YT_UPLOADS = os.path.join(OUTPUT, "youtube-uploads.json")                   # canale ORIGINALE (oggi Gol Impossibili)
YT_UPLOADS_NEW = os.path.join(OUTPUT, "youtube-calciovich-uploads.json")    # canale del libro (dal 06/10/2026)
IG_UPLOADS = os.path.join(OUTPUT, "instagram-uploads.json")
TK_UPLOADS = os.path.join(OUTPUT, "tiktok-uploads.json")

# Da questo istante (ora locale, stesso formato di snapshot_at) OGNI riga nuova di engagement deve avere channel_key:
# le righe storiche NULL sono del canale originale, ma una riga nuova senza canale significa uno scrittore vecchio
# o rotto (un checkout non aggiornato) e va segnalata.
CHANNEL_KEY_REQUIRED_FROM = "2026-10-07T00:00:00"

PROJECT = "calciovich-video-analytics"
DATASET = "calciovich_content"
KEY_PATH = os.path.join(HERE, "bigquery_loader_key.json")


def build_all():
    """Nessun effetto collaterale: costruisce tutte le righe da scrivere. Condivisa
    fra --dry-run e il run reale, stesso principio di build_plan() nel resto della
    pipeline. Ritorna (tabelle, statistiche) dove tabelle e' {nome: [righe]}."""
    app_data = load(APP_DATA, {})
    dim_content_rows, dropped_dupes = dm.build_dim_content(app_data)
    valid_keys = {r["content_key"] for r in dim_content_rows}

    storico = load(STORICO, {"records": []})
    storico_new = load(STORICO_NEW, {"records": []})
    for r in storico.get("records", []):
        if r.get("channel_key") not in (None, dm.LEGACY_CHANNEL):   # file originale: righe storiche senza canale o del suo canale
            raise ValueError(f"{STORICO}: riga con channel_key={r.get('channel_key')!r} per il video {r.get('video_id')}")
    for r in storico_new.get("records", []):
        if r.get("channel_key") != "calciovich":    # il file del canale nuovo non puo' contenere altro
            raise ValueError(f"{STORICO_NEW}: riga con channel_key={r.get('channel_key')!r} per il video {r.get('video_id')}")
    engagement_rows = dm.build_fct_youtube_engagement_snapshot(
        storico.get("records", []) + storico_new.get("records", []))

    finestre = load(FINESTRE, {"records": {}})
    fixed_window_rows = dm.build_fct_youtube_fixed_window(finestre.get("records", {}))

    yt_original = load_confirmed_youtube_uploads(YT_UPLOADS)
    yt_new = load_confirmed_youtube_uploads(YT_UPLOADS_NEW)
    registry_data = {
        "youtube": yt_original,
        "youtube-calciovich": yt_new,
        "instagram": load_confirmed_uploads(IG_UPLOADS, "mediaId"),
        "tiktok": load_confirmed_uploads(TK_UPLOADS, "publishId"),
    }
    publish_rows, excluded_publish = dm.build_fct_publish_event(registry_data, valid_keys)
    lineage_rows = dm.build_dim_content_lineage(yt_new, yt_original, valid_keys)

    known_dates = {date.today()}
    for r in engagement_rows:
        ts = r.get("snapshot_at")
        if ts:
            known_dates.add(date.fromisoformat(ts[:10]))
    for r in publish_rows:
        ts = r.get("confirmed_at")
        if ts:
            try:
                known_dates.add(date.fromisoformat(ts[:10]))
            except ValueError:
                pass
    dim_date_rows = dm.build_dim_date(known_dates)

    tables = {
        "dim_platform": dm.build_dim_platform(),
        "dim_date": dim_date_rows,
        "dim_content": dim_content_rows,
        "fct_youtube_engagement_snapshot": engagement_rows,
        "fct_youtube_fixed_window": fixed_window_rows,
        "fct_publish_event": publish_rows,
        "dim_channel": dm.build_dim_channel(CHANNEL_IDS),
        "dim_content_lineage": lineage_rows,
    }
    stats = {"dropped_content_dupes": dropped_dupes, "excluded_publish_events": excluded_publish}
    return tables, stats


def _schemas(bigquery):
    return {
        "dim_platform": [bigquery.SchemaField("platform", "STRING", mode="REQUIRED")],
        "dim_date": [
            bigquery.SchemaField("date", "DATE", mode="REQUIRED"),
            bigquery.SchemaField("year", "INTEGER"),
            bigquery.SchemaField("month", "INTEGER"),
            bigquery.SchemaField("day", "INTEGER"),
            bigquery.SchemaField("day_of_week", "STRING"),
            bigquery.SchemaField("iso_week", "INTEGER"),
        ],
        "dim_content": [
            bigquery.SchemaField("content_key", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("file", "STRING"),
            bigquery.SchemaField("fonte", "STRING"),
            bigquery.SchemaField("categoria", "STRING"),
            bigquery.SchemaField("titolo", "STRING"),
        ],
        "fct_youtube_engagement_snapshot": [
            bigquery.SchemaField("video_id", "STRING"),
            bigquery.SchemaField("content_key", "STRING"),
            bigquery.SchemaField("snapshot_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("views", "INTEGER"),
            bigquery.SchemaField("likes", "INTEGER"),
            bigquery.SchemaField("comments", "INTEGER"),
            bigquery.SchemaField("channel_key", "STRING"),
        ],
        "fct_youtube_fixed_window": [
            bigquery.SchemaField("video_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("content_key", "STRING"),
            bigquery.SchemaField("views_day1", "INTEGER"),
            bigquery.SchemaField("views_day2", "INTEGER"),
            bigquery.SchemaField("views_day7", "INTEGER"),
            bigquery.SchemaField("channel_key", "STRING"),
        ],
        "fct_publish_event": [
            bigquery.SchemaField("content_key", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("platform", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("external_id", "STRING"),
            bigquery.SchemaField("privacy", "STRING"),
            bigquery.SchemaField("confirmed_at", "TIMESTAMP"),
            bigquery.SchemaField("channel_key", "STRING"),
            bigquery.SchemaField("scheduled_publish_at", "TIMESTAMP"),
        ],
        "dim_channel": [
            bigquery.SchemaField("channel_key", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("channel_name", "STRING"),
            bigquery.SchemaField("youtube_channel_id", "STRING"),
            bigquery.SchemaField("role", "STRING"),
        ],
        "dim_content_lineage": [
            bigquery.SchemaField("content_key", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("original_video_id", "STRING"),
            bigquery.SchemaField("copy_video_id", "STRING"),
            bigquery.SchemaField("scheduled_publish_at", "TIMESTAMP"),
            bigquery.SchemaField("copy_confirmed_at", "TIMESTAMP"),
            bigquery.SchemaField("reason", "STRING"),
        ],
        "_run_metadata": [
            bigquery.SchemaField("run_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("completed_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("tables_written", "STRING", mode="REPEATED"),
        ],
    }


def _bigquery_client(bigquery, service_account):
    creds = service_account.Credentials.from_service_account_file(KEY_PATH)
    return bigquery.Client(project=PROJECT, credentials=creds)


def _ensure_table(client, bigquery, table_name, schema):
    """Crea la tabella se manca (schema esplicito). Va fatto PRIMA di leggerla: cosi' un errore di lettura non puo'
    mai essere scambiato per 'tabella vuota'."""
    client.create_table(bigquery.Table(f"{PROJECT}.{DATASET}.{table_name}", schema=schema), exists_ok=True)


def _existing_engagement_keys(client):
    """Insieme delle chiavi (canale, video, snapshot_at) gia' su BigQuery, nello stesso formato delle stringhe
    locali ("%Y-%m-%dT%H:%M:%S", senza offset) per un confronto diretto. Deliberatamente FORMAT_TIMESTAMP, non
    CAST(...AS STRING): CAST produce "2026-09-29 18:08:53+00" (spazio, offset) che non combacia con le stringhe locali.
    Le righe storiche con channel_key NULL contano come canale originale.

    FAIL-CLOSED: qualunque errore (permessi, rete, schema) si propaga e lo script si ferma. Un tentativo precedente
    trattava ogni errore come 'tabella vuota' e avrebbe riappeso l'intero storico locale."""
    table_id = f"{PROJECT}.{DATASET}.fct_youtube_engagement_snapshot"
    rows = client.query(
        f"SELECT DISTINCT COALESCE(channel_key, '{dm.LEGACY_CHANNEL}') AS ch, video_id, "
        f"FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%S', snapshot_at) AS ts FROM `{table_id}`"
    ).result()
    return {(r["ch"], r["video_id"], r["ts"]) for r in rows}


def _null_channel_rows_since(client, boundary=CHANNEL_KEY_REQUIRED_FROM):
    """Quante righe di engagement SCRITTE dopo `boundary` non hanno channel_key. Dopo lo split ogni scrittore valorizza
    il canale: una riga nuova senza canale = scrittore vecchio o rotto (es. un checkout non aggiornato) e il
    COALESCE delle viste la farebbe passare per 'canale originale' in silenzio."""
    table_id = f"{PROJECT}.{DATASET}.fct_youtube_engagement_snapshot"
    r = list(client.query(
        f"SELECT COUNT(*) AS n FROM `{table_id}` WHERE channel_key IS NULL "
        f"AND snapshot_at >= TIMESTAMP('{boundary}')"
    ).result())
    return r[0]["n"]


def _duplicate_engagement_keys(client):
    """Quante chiavi (canale, video, snapshot_at) compaiono piu' di una volta nella fact. Non e' un errore: la
    vista v_engagement_canonico le elimina in lettura (il progetto e' senza DML, quindi non si possono impedire
    ne' cancellare in scrittura). Ma si conta e si dice, cosi' una deriva non resta invisibile."""
    table_id = f"{PROJECT}.{DATASET}.fct_youtube_engagement_snapshot"
    r = list(client.query(
        f"SELECT COUNT(*) AS n FROM (SELECT 1 FROM `{table_id}` "
        f"GROUP BY COALESCE(channel_key, '{dm.LEGACY_CHANNEL}'), video_id, snapshot_at HAVING COUNT(*) > 1)"
    ).result())
    return r[0]["n"]


def _write_table(client, bigquery, table_name, rows, schema, write_disposition):
    """Crea la tabella se non esiste (schema esplicito, non inferito — un load con
    zero righe non deve lasciare la tabella senza schema) e carica le righe con la
    write_disposition indicata. Nessuna scrittura se rows e' vuota E la tabella
    esiste gia': un load_table_from_json con lista vuota funziona comunque (BigQuery
    lo accetta), utile per svuotare una tabella diventata vuota nella fonte."""
    table_id = f"{PROJECT}.{DATASET}.{table_name}"
    table = bigquery.Table(table_id, schema=schema)
    client.create_table(table, exists_ok=True)
    job_config = bigquery.LoadJobConfig(write_disposition=write_disposition, schema=schema)
    job = client.load_table_from_json(rows, table_id, job_config=job_config)
    job.result()


def main():
    dry_run = "--dry-run" in sys.argv
    tables, stats = build_all()

    print(f"dim_content: {len(tables['dim_content'])} righe "
          f"({stats['dropped_content_dupes']} duplicati risolti)")
    print(f"dim_date: {len(tables['dim_date'])} righe")
    print(f"dim_platform: {len(tables['dim_platform'])} righe")
    print(f"fct_youtube_engagement_snapshot: {len(tables['fct_youtube_engagement_snapshot'])} righe")
    print(f"fct_youtube_fixed_window: {len(tables['fct_youtube_fixed_window'])} righe")
    print(f"dim_channel: {len(tables['dim_channel'])} righe")
    print(f"dim_content_lineage: {len(tables['dim_content_lineage'])} righe")
    print(f"fct_publish_event: {len(tables['fct_publish_event'])} righe "
          f"({stats['excluded_publish_events']} escluse, fuori scope dim_content)")

    if dry_run:
        print("DRY RUN: nessuna scrittura su BigQuery.")
        return

    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        sys.exit("google-cloud-bigquery non installato su questo interprete "
                  "(/usr/bin/python3 -m pip install --user google-cloud-bigquery) — "
                  "nessuna scrittura, riprova al prossimo trigger.")

    if not os.path.exists(KEY_PATH):
        sys.exit(f"Chiave del service account assente ({KEY_PATH}) — nessuna "
                  f"scrittura, riprova dopo averla generata.")

    client = _bigquery_client(bigquery, service_account)
    schemas = _schemas(bigquery)

    truncate_tables = ["dim_platform", "dim_date", "dim_content", "dim_channel", "dim_content_lineage",
                        "fct_youtube_fixed_window", "fct_publish_event"]
    written = []
    for name in truncate_tables:
        _write_table(client, bigquery, name, tables[name], schemas[name],
                     write_disposition="WRITE_TRUNCATE")
        written.append(name)
        print(f"✓ {name} scritta ({len(tables[name])} righe)")

    _ensure_table(client, bigquery, "fct_youtube_engagement_snapshot", schemas["fct_youtube_engagement_snapshot"])
    remote_keys = _existing_engagement_keys(client)
    new_engagement_rows = dm.filter_new_engagement_rows(
        tables["fct_youtube_engagement_snapshot"], remote_keys)
    _write_table(client, bigquery, "fct_youtube_engagement_snapshot", new_engagement_rows,
                 schemas["fct_youtube_engagement_snapshot"], write_disposition="WRITE_APPEND")
    written.append("fct_youtube_engagement_snapshot")
    print(f"✓ fct_youtube_engagement_snapshot scritta ({len(new_engagement_rows)} righe "
          f"nuove su {len(tables['fct_youtube_engagement_snapshot'])} nello storico locale, "
          f"{len(remote_keys)} chiavi gia' remote)")
    dups = _duplicate_engagement_keys(client)
    if dups:
        print(f"ℹ️  {dups} chiavi (canale, video, snapshot_at) duplicate nella fact: innocue, le elimina "
              f"v_engagement_canonico in lettura (due scrittori partiti insieme).")
    null_rows = _null_channel_rows_since(client)
    if null_rows:
        print(f"⛔ {null_rows} righe di engagement scritte dopo {CHANNEL_KEY_REQUIRED_FROM} senza channel_key: "
              f"uno scrittore vecchio o rotto (checkout non aggiornato?). Le viste le leggono come canale originale. "
              f"Il run NON viene registrato come completato.")
        sys.exit(1)

    run_row = {
        "run_id": uuid.uuid4().hex,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tables_written": written,
    }
    _write_table(client, bigquery, "_run_metadata", [run_row], schemas["_run_metadata"],
                 write_disposition="WRITE_APPEND")
    print(f"✓ _run_metadata: run {run_row['run_id']} completato ({len(written)} tabelle)")



if __name__ == "__main__":
    main()
