#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
raccogli_snapshot_cloud.py — raccolta YouTube snapshot da GitHub Actions, backstop
indipendente al LaunchAgent locale com.calciovich.youtubestats durante i periodi in
cui il Mac resta addormentato per giorni (vedi l'incidente del 23-27/09/2026 —
~4 giorni senza nessun trigger, confermato da backgroundtaskmanagementd e dal gap
in BigQuery).

A differenza di aggiorna_youtube_stats.py + raccogli_metriche_video.py (che
accumulano uno storico locale completo in output/, mai sincronizzato con la cloud
per scelta — vedi .gitignore e il commento in cima a carica_bigquery.py), questo
script non ha e non vuole uno storico locale proprio: prende UNA sola fotografia
fresca per run e la scrive su BigQuery con WRITE_APPEND, usando lo stesso
meccanismo anti-duplicazione del path Mac (dm.filter_new_engagement_rows contro
le chiavi (canale, video, snapshot_at) gia' remote, via cb._existing_engagement_keys) — qui e' quasi
sempre un no-op perche' ogni riga porta un timestamp "adesso" per forza piu'
recente di qualunque storico, ma e' lo stesso codice del path Mac: un solo punto
dove questa logica puo' avere un bug, non due copie che possono divergere.

Content_key risolto leggendo DISTINCT (video_id, content_key) gia' visti in
fct_youtube_engagement_snapshot su BigQuery, non da app/data.json: quel file e'
rigenerato in locale da genera_app.py a ogni avvio e non esiste in un checkout
GitHub Actions. Un video pubblicato per la prima volta proprio durante un gap di
piu' giorni del Mac non avrebbe ancora un content_key noto: la riga viene scritta
comunque con content_key=None invece di essere scartata (mart_daily_engagement
somma comunque sul giorno per il grafico "Andamento nel tempo"; solo il titolo in
mart_video_performance resterebbe vuoto finche' il Mac non torna attivo e un suo
WRITE_TRUNCATE non ricostruisce dim_content con il content_key corretto) — scelta
deliberata: un dato parziale ma presente batte un buco nel grafico.

Credenziali: bigquery_loader_key.json (gia' usata dal path Mac) e
youtube_readonly_token.json — un token dedicato a SOLA LETTURA (scope
youtube.readonly, client OAuth separato, vedi youtube_readonly_auth.py), NON il
youtube_token.json condiviso con carica_youtube.py che ha anche lo scope upload.
Se questo secret uscisse, chi lo prende potrebbe leggere statistiche, non toccare
il canale. Stesso principio di youtube_analytics_auth.py (token isolato dopo un
incidente reale di scope troppo ampio che si risolveva sul canale sbagliato) e,
con lo split di canale in arrivo, un token per canale, mai condiviso.

USO (pensato per girare da GitHub Actions (vedi examples/youtube-stats-cloud.yml), eseguibile anche a mano per un test)
  python3 raccogli_snapshot_cloud.py
"""
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import carica_bigquery as cb
import dimensional_model as dm
from metriche_video import fetch_stats

HERE = os.path.dirname(os.path.abspath(__file__))
READONLY_TOKEN = os.path.join(HERE, "youtube_readonly_token.json")
READONLY_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


def _snapshot_at_now():
    """Ora di Roma, senza fuso: la STESSA convenzione di raccogli_metriche_video.py
    (datetime.now() sul Mac, che e' in Italia). Il runner GitHub e' in UTC: con
    datetime.now() nudo scriverebbe orari 1-2 ore indietro nella stessa colonna: gli snapshot dei due scrittori
    non sarebbero confrontabili e la serie temporale di un video sarebbe incoerente (scoperto il 2026-10-03,
    quando il vecchio filtro per limite scarto' un giro cloud: un run "success" con 0 righe scritte). Il filtro
    ora e' per chiave esatta (canale, video, snapshot_at), ma la convenzione oraria resta una sola nella colonna."""
    return datetime.now(ZoneInfo("Europe/Rome")).replace(tzinfo=None).isoformat(timespec="seconds")


def _known_video_content_keys(client, bigquery):
    """{video_id: content_key} dei video del canale ORIGINALE gia' su BigQuery — niente app/data.json (non esiste
    in un checkout CI), niente registry locali. Solo il canale originale: questo job ha il token (a sola lettura)
    di quel canale e scrive righe etichettate con quel canale. Le righe storiche con channel_key NULL sono
    del canale originale. I video del canale del libro NON si raccolgono da qui: servirebbe un job e un secret
    separati per quel canale (la Data API legge le statistiche pubbliche di qualunque video, quindi senza
    questo filtro il job scriverebbe righe del canale nuovo con l'etichetta sbagliata)."""
    table_id = f"{cb.PROJECT}.{cb.DATASET}.fct_youtube_engagement_snapshot"
    rows = client.query(
        f"SELECT DISTINCT video_id, content_key FROM `{table_id}` "
        f"WHERE video_id IS NOT NULL AND COALESCE(channel_key, '{dm.LEGACY_CHANNEL}') = '{dm.LEGACY_CHANNEL}'"
    ).result()
    return {r["video_id"]: r["content_key"] for r in rows}


def main():
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        sys.exit("google-cloud-bigquery non installato.")

    if not os.path.exists(cb.KEY_PATH):
        sys.exit(f"Chiave del service account assente ({cb.KEY_PATH}).")

    client = cb._bigquery_client(bigquery, service_account)

    video_content_key = _known_video_content_keys(client, bigquery)
    video_ids = sorted(video_content_key)
    if not video_ids:
        sys.exit("Nessun video noto su BigQuery (tabella vuota) — niente da "
                  "aggiornare, il Mac deve girare almeno una volta per primo.")

    stats = fetch_stats(video_ids, token_path=READONLY_TOKEN, scopes=READONLY_SCOPES,
                        expect_channel_id=cb.CHANNEL_IDS[dm.LEGACY_CHANNEL])
    snapshot_at = _snapshot_at_now()

    rows = []
    skipped_private = 0
    for vid in video_ids:
        info = stats.get(vid)
        if not info or info.get("privacy") != "public":
            skipped_private += 1
            continue
        rows.append({
            "video_id": vid,
            "content_key": video_content_key.get(vid),
            "snapshot_at": snapshot_at,
            "views": info.get("views"),
            "likes": info.get("likes"),
            "comments": info.get("comments"),
            "channel_key": dm.LEGACY_CHANNEL,
        })

    if not rows:
        # Video noti ma nessuno pubblico/leggibile (token sbagliato, canale cambiato, API che
        # non risponde): zero righe e' esattamente il "verde che non ha scritto" di 03/10, per
        # un'altra strada. Un run verde deve significare righe scritte.
        sys.exit(f"Nessun video pubblico fra i {len(video_ids)} noti ({skipped_private} non "
                  f"pubblici o non restituiti dall'API) — niente da scrivere: controlla token e canale.")

    schema = cb._schemas(bigquery)["fct_youtube_engagement_snapshot"]
    cb._ensure_table(client, bigquery, "fct_youtube_engagement_snapshot", schema)
    remote_keys = cb._existing_engagement_keys(client)
    new_rows = dm.filter_new_engagement_rows(rows, remote_keys)      # per chiave esatta, non per limite

    if rows and not new_rows:
        # Non e' piu' un caso legittimo: con la stessa convenzione oraria del Mac, ogni
        # riga "adesso" e' piu' recente di qualunque scrittura precedente. Zero righe
        # scritte su video che ci sono = qualcosa non va, e un run verde non deve
        # nasconderlo (e' esattamente cosi' che il bug dei fusi e' passato inosservato).
        sys.exit(f"Nessuna riga scritta su {len(rows)} video raccolti (snapshot_at="
                  f"{snapshot_at}, {len(remote_keys)} chiavi gia' remote) — controlla la convenzione "
                  f"oraria di snapshot_at fra Mac e cloud.")

    cb._write_table(client, bigquery, "fct_youtube_engagement_snapshot", new_rows,
                     schema, write_disposition="WRITE_APPEND")
    print(f"✓ fct_youtube_engagement_snapshot (cloud): {len(new_rows)} righe nuove "
          f"su {len(video_ids)} video noti ({skipped_private} non pubblici, saltati)")


if __name__ == "__main__":
    main()
