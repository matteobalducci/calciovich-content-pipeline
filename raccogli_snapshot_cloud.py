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
con lo split di canale, un token per canale, mai condiviso.

DUE CANALI (dal 2026-10-09): il job raccoglie entrambi, ognuno col SUO token a sola lettura
(CHANNEL_TOKENS): Gol Impossibili (secret YOUTUBE_READONLY_TOKEN_JSON, obbligatorio) e Calciovich (secret
YOUTUBE_READONLY_LIBRO_TOKEN_JSON, facoltativo SOLO nel senso che se il file token manca il canale viene
saltato con un'annotazione ::warning::; se il token c'e', zero video noti o zero righe sono un errore). Ogni lettura verifica che il token appartenga
davvero al canale atteso (channels.list mine=True): le statistiche pubbliche di un video si leggono con
qualunque token, quindi un token sbagliato non darebbe errore e scriverebbe righe con l'etichetta di un altro
canale. Un problema su un canale NON impedisce di scrivere le righe dell'altro, ma fa finire il job in errore:
un run verde deve significare "tutti i canali attesi hanno scritto".

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
READONLY_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
# canale -> (file del token nel checkout CI, obbligatorio?)
CHANNEL_TOKENS = {
    dm.LEGACY_CHANNEL: ("youtube_readonly_token.json", True),
    "calciovich": ("youtube_readonly_libro_token.json", False),
}
READONLY_TOKEN = os.path.join(HERE, CHANNEL_TOKENS[dm.LEGACY_CHANNEL][0])     # compatibilita'


def _snapshot_at_now():
    """Ora di Roma, senza fuso: la STESSA convenzione di raccogli_metriche_video.py
    (datetime.now() sul Mac, che e' in Italia). Il runner GitHub e' in UTC: con
    datetime.now() nudo scriverebbe orari 1-2 ore indietro nella stessa colonna: gli snapshot dei due scrittori
    non sarebbero confrontabili e la serie temporale di un video sarebbe incoerente (scoperto il 2026-10-03,
    quando il vecchio filtro per limite scarto' un giro cloud: un run "success" con 0 righe scritte). Il filtro
    ora e' per chiave esatta (canale, video, snapshot_at), ma la convenzione oraria resta una sola nella colonna."""
    return datetime.now(ZoneInfo("Europe/Rome")).replace(tzinfo=None).isoformat(timespec="seconds")


def _known_video_content_keys(client, bigquery, channel_key=dm.LEGACY_CHANNEL):
    """{video_id: content_key} dei video di UN canale gia' su BigQuery — niente app/data.json (non esiste in un
    checkout CI), niente registry locali. Le righe storiche con channel_key NULL sono del canale originale. Il
    filtro per canale serve perche' la Data API legge le statistiche pubbliche di qualunque video: senza di esso
    un token scriverebbe righe di un canale con l'etichetta dell'altro."""
    if channel_key not in CHANNEL_TOKENS:
        raise ValueError(f"canale sconosciuto: {channel_key}")
    table_id = f"{cb.PROJECT}.{cb.DATASET}.fct_youtube_engagement_snapshot"
    cond = (f"COALESCE(channel_key, '{dm.LEGACY_CHANNEL}') = '{channel_key}'" if channel_key == dm.LEGACY_CHANNEL
            else f"channel_key = '{channel_key}'")
    rows = client.query(
        f"SELECT DISTINCT video_id, content_key FROM `{table_id}` WHERE video_id IS NOT NULL AND {cond}"
    ).result()
    return {r["video_id"]: r["content_key"] for r in rows}


def _collect_channel(client, bigquery, channel_key, token_path, snapshot_at):
    """Righe di un canale il cui token ESISTE. Ritorna (rows, n_non_pubblici, problema|None). Non solleva mai:
    un canale rotto (BigQuery, token, API) non deve impedire di scrivere l'altro; il problema viene riportato e
    fa finire il job in errore. Facoltativo e' solo l'avere il token: se c'e', si aspettano dati."""
    try:
        video_content_key = _known_video_content_keys(client, bigquery, channel_key)
        video_ids = sorted(video_content_key)
        if not video_ids:
            return [], 0, (f"{channel_key}: nessun video noto su BigQuery (tabella vuota) — il Mac deve "
                           f"girare almeno una volta per primo.")
        stats = fetch_stats(video_ids, token_path=token_path, scopes=READONLY_SCOPES,
                            expect_channel_id=cb.CHANNEL_IDS[channel_key])
    except (Exception, SystemExit) as exc:            # SystemExit: fetch_stats esce se il token e' illeggibile
        return [], 0, f"{channel_key}: raccolta fallita ({type(exc).__name__}: {exc})"
    rows, skipped_private = [], 0
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
            "channel_key": channel_key,
        })
    if not rows:
        # Video noti ma nessuno pubblico/leggibile (token sbagliato, canale cambiato, API che non risponde):
        # zero righe e' il "verde che non ha scritto" di 03/10 per un'altra strada.
        return [], skipped_private, (f"{channel_key}: nessun video pubblico fra i {len(video_ids)} noti "
                                     f"({skipped_private} non pubblici o non restituiti dall'API) — controlla "
                                     f"token e canale.")
    return rows, skipped_private, None


def main():
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        sys.exit("google-cloud-bigquery non installato.")

    if not os.path.exists(cb.KEY_PATH):
        sys.exit(f"Chiave del service account assente ({cb.KEY_PATH}).")

    client = cb._bigquery_client(bigquery, service_account)
    snapshot_at = _snapshot_at_now()

    rows, problems, summaries = [], [], []
    for channel_key, (token_file, required) in CHANNEL_TOKENS.items():
        token_path = os.path.join(HERE, token_file)
        if not os.path.exists(token_path):
            msg = f"{channel_key}: token {token_file} assente"
            if required:
                problems.append(msg)
            else:
                print(f"::warning::{msg} — canale saltato (imposta il secret per attivarlo)")
            continue
        ch_rows, skipped, problem = _collect_channel(client, bigquery, channel_key, token_path, snapshot_at)
        rows += ch_rows
        if problem:
            problems.append(problem)
        elif ch_rows:
            summaries.append(f"{channel_key}: {len(ch_rows)} video ({skipped} non pubblici)")

    new_rows = []
    if rows:
        schema = cb._schemas(bigquery)["fct_youtube_engagement_snapshot"]
        cb._ensure_table(client, bigquery, "fct_youtube_engagement_snapshot", schema)
        remote_keys = cb._existing_engagement_keys(client)
        new_rows = dm.filter_new_engagement_rows(rows, remote_keys)      # per chiave esatta, non per limite
        # Il controllo e' PER CANALE: con la stessa convenzione oraria del Mac ogni riga "adesso" e' nuova, quindi un
        # canale con video raccolti ma zero righe nuove ha qualcosa che non va (bug dei fusi, 03/10), anche se l'altro
        # canale ha scritto: un run verde deve significare "ogni canale atteso ha scritto".
        for channel_key in sorted({r["channel_key"] for r in rows} - {r["channel_key"] for r in new_rows}):
            n = sum(1 for r in rows if r["channel_key"] == channel_key)
            problems.append(f"{channel_key}: nessuna riga scritta su {n} video raccolti (snapshot_at={snapshot_at}, "
                            f"{len(remote_keys)} chiavi gia' remote) — controlla la convenzione oraria.")
        if new_rows:
            cb._write_table(client, bigquery, "fct_youtube_engagement_snapshot", new_rows,
                             schema, write_disposition="WRITE_APPEND")
            print(f"✓ fct_youtube_engagement_snapshot (cloud): {len(new_rows)} righe nuove — "
                  + "; ".join(summaries))

    if problems:
        # Le righe dei canali sani sono gia' scritte; il job va comunque in errore, ben visibile.
        sys.exit("PROBLEMI: " + " | ".join(problems))


if __name__ == "__main__":
    main()
