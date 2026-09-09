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

SCRITTURA STAGING: WRITE_TRUNCATE per tabella, non MERGE. Ogni fonte locale (i JSON
di Fase 1/2, upload_registry.Registry per le tre piattaforme) e' uno stato pieno e
autoconsistente ad ogni lettura, non un log incrementale — verificato leggendo
merge_records()/merge_windows() (accumulano localmente, riscrivono l'intero file) e
Registry.data (sempre SELECT * sullo stato corrente). Un'istintiva scelta
WRITE_APPEND per una "tabella di staging" romperebbe questa proprieta'.

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
STORICO = os.path.join(OUTPUT, "metriche-video-storico.json")
FINESTRE = os.path.join(OUTPUT, "metriche-finestre-fisse.json")
YT_UPLOADS = os.path.join(OUTPUT, "youtube-uploads.json")
IG_UPLOADS = os.path.join(OUTPUT, "instagram-uploads.json")
TK_UPLOADS = os.path.join(OUTPUT, "tiktok-uploads.json")

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
    engagement_rows = dm.build_fct_youtube_engagement_snapshot(storico.get("records", []))

    finestre = load(FINESTRE, {"records": {}})
    fixed_window_rows = dm.build_fct_youtube_fixed_window(finestre.get("records", {}))

    registry_data = {
        "youtube": load_confirmed_youtube_uploads(YT_UPLOADS),
        "instagram": load_confirmed_uploads(IG_UPLOADS, "mediaId"),
        "tiktok": load_confirmed_uploads(TK_UPLOADS, "publishId"),
    }
    publish_rows, excluded_publish = dm.build_fct_publish_event(registry_data, valid_keys)

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
        ],
        "fct_youtube_engagement_snapshot": [
            bigquery.SchemaField("video_id", "STRING"),
            bigquery.SchemaField("content_key", "STRING"),
            bigquery.SchemaField("snapshot_at", "TIMESTAMP", mode="REQUIRED"),
            bigquery.SchemaField("views", "INTEGER"),
            bigquery.SchemaField("likes", "INTEGER"),
            bigquery.SchemaField("comments", "INTEGER"),
        ],
        "fct_youtube_fixed_window": [
            bigquery.SchemaField("video_id", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("content_key", "STRING"),
            bigquery.SchemaField("views_day1", "INTEGER"),
            bigquery.SchemaField("views_day2", "INTEGER"),
            bigquery.SchemaField("views_day7", "INTEGER"),
        ],
        "fct_publish_event": [
            bigquery.SchemaField("content_key", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("platform", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("external_id", "STRING"),
            bigquery.SchemaField("privacy", "STRING"),
            bigquery.SchemaField("confirmed_at", "TIMESTAMP"),
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

    staging_tables = ["dim_platform", "dim_date", "dim_content",
                       "fct_youtube_engagement_snapshot", "fct_youtube_fixed_window",
                       "fct_publish_event"]
    written = []
    for name in staging_tables:
        _write_table(client, bigquery, name, tables[name], schemas[name],
                     write_disposition="WRITE_TRUNCATE")
        written.append(name)
        print(f"✓ {name} scritta ({len(tables[name])} righe)")

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
