#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dimensional_model.py — costruzione pure-Python del modello dimensionale (Fase 3
del layer analytics: BigQuery + star schema, in preparazione della Fase 4 Looker
Studio).

Nessuna dipendenza da google-cloud-bigquery, deliberatamente: separato da
carica_bigquery.py per lo stesso motivo per cui metriche_video.py/check_outliers.py
non dipendono da google-auth per la logica pura — la CI del repo pubblico gira senza
SDK Google, e la maggior parte del rischio reale (regola di dedup, filtro di scope,
mappa piattaforma->campo id) vive qui, testabile senza rete ne' credenziali.

SCOPE ONESTO (decisione piu' importante, verificata su dati reali in 6 giri di
revisione avversariale prima di scrivere questo file): le metriche di engagement
esistono SOLO per YouTube. fct_youtube_engagement_snapshot e fct_youtube_fixed_window
restano staging 1:1 dalle rispettive fonti locali (gia' scoped a upload YouTube
confermati da Fase 1/2, nessun filtro aggiuntivo qui). Solo fct_publish_event, che
legge da tre Registry grezzi, va filtrato esplicitamente ai contenuti presenti in
dim_content — foto/Stories Instagram fuori dal calendario video ne restano fuori,
con un conteggio esplicito di quante righe sono state escluse (mai un'esclusione
silenziosa).
"""
import os
from datetime import date, timedelta

from metriche_video import categoria as _categoria, _app_data_categoria_map

ID_FIELD_BY_PLATFORM = {"youtube": "videoId", "instagram": "mediaId", "tiktok": "publishId"}


def _content_key(file_path):
    """Stessa derivazione di _app_data_categoria_map(): basename senza l'ultima
    estensione. ATTENZIONE: molte key reali hanno un "falso" doppio suffisso
    (es. "...coast-to-coast-v2.tv.mp4" -> "...coast-to-coast-v2.tv", ".tv" e' parte
    del nome, non un'estensione) — splitext toglie solo l'ultima, esattamente il
    comportamento voluto perche' e' lo stesso usato ovunque nel resto della pipeline."""
    return os.path.splitext(os.path.basename(file_path))[0]


def build_dim_content(app_data):
    """Calendario video dichiarato (app/data.json), non un sottoprodotto di quali
    piattaforme hanno ricevuto l'upload — un content pubblicato su IG/TikTok con una
    key mai passata da YouTube produrrebbe un orfano silenzioso se dim_content fosse
    letto dal Registry invece che da qui (verificato con un caso reale in produzione,
    settimana2-day1, durante la revisione del piano).

    Filtro: solo stato == "Pronto" (equivalente esatto, verificato sui dati reali, a
    "file non nullo" — gli item "Da produrre" sono pianificati ma non ancora esistenti).

    Dedup: alcuni file fisici sono referenziati da piu' item con id diverso (un item
    di calendario vero + un backfill storico che punta allo stesso output). Vince
    l'item la cui fonte ha PREFISSO "output/ai-content-queue.json#" (match sul
    prefisso prima di "#", non una ricerca di sottostringa — un candidato perdente
    puo' contenere quella stringa nel proprio testo esplicativo, es.
    "...(nessun item corrispondente in ai-content-queue.json)", che una ricerca a
    sottostringa matcherebbe per errore esattamente sul caso che la regola deve
    risolvere).

    BUGFIX (dibattito di controllo, stesso giorno): la dedup deve chiavare su
    content_key (il grain vero di dim_content), non sul file path grezzo. Due file
    fisicamente diversi il cui basename-senza-ultima-estensione collide (es.
    ".../short01.mp4" e ".../altra-cartella/short01.mov") non venivano deduplicati
    affatto con una dedup per file: producevano due righe distinte con lo stesso
    content_key, dropped_dupes restava a 0 (nessuna segnalazione), e le LEFT JOIN
    del mart avrebbero fatto fan-out silenzioso. Zero collisioni nei dati reali di
    produzione oggi (verificato), ma non impossibile in futuro — non e' un
    controesempio ipotetico da ignorare solo perche' non si e' ancora manifestato.

    Ritorna (righe, conteggio_duplicati_risolti)."""
    app_map = _app_data_categoria_map(app_data)

    candidates = []
    for w in app_data.get("weeks", []):
        for it in w.get("items", []):
            if it.get("stato") != "Pronto":
                continue
            f = it.get("file")
            if not f:
                continue
            candidates.append((_content_key(f), f, it.get("fonte") or ""))

    by_key = {}
    dropped_dupes = 0
    for key, f, fonte in candidates:
        if key not in by_key:
            by_key[key] = (f, fonte)
            continue
        dropped_dupes += 1
        existing_f, existing_fonte = by_key[key]
        existing_wins = existing_fonte.split("#", 1)[0] == "output/ai-content-queue.json"
        new_wins = fonte.split("#", 1)[0] == "output/ai-content-queue.json"
        if new_wins and not existing_wins:
            by_key[key] = (f, fonte)
        elif not existing_wins and not new_wins:
            print(f"⚠️  dim_content: duplicato su content_key {key!r} "
                  f"(file: {existing_f!r} vs {f!r}) senza una fonte "
                  f"ai-content-queue.json chiara — tenuto il primo visto.")
        elif existing_wins and new_wins:
            # Segnalato dal secondo giro di controllo: due candidati ENTRAMBI
            # sourced da ai-content-queue.json che collidono sullo stesso
            # content_key (due voci di calendario diverse, non un item + un
            # backfill) — la regola non ha un criterio per scegliere fra loro,
            # tiene il primo visto. Raro quanto la collisione stessa, ma non va
            # taciuto: e' un'ambiguita' vera nel calendario, non solo un artefatto
            # di backfill storico.
            print(f"⚠️  dim_content: duplicato su content_key {key!r} "
                  f"(file: {existing_f!r} vs {f!r}), ENTRAMBI da ai-content-queue.json "
                  f"— tenuto il primo visto.")

    rows = []
    for key, (f, fonte) in by_key.items():
        rows.append({
            "content_key": key,
            "file": f,
            "fonte": fonte,
            "categoria": _categoria(key, app_map),
        })
    return rows, dropped_dupes


def build_dim_platform():
    return [{"platform": p} for p in ID_FIELD_BY_PLATFORM]


def build_dim_date(dates):
    """dates: iterable di date.date reali da coprire. Calendario standard da
    min(dates) a max(dates) inclusi. Lista vuota -> nessuna riga (nessun evento noto,
    nessun calendario da costruire)."""
    dates = list(dates)
    if not dates:
        return []
    start, end = min(dates), max(dates)
    rows = []
    d = start
    while d <= end:
        rows.append({
            "date": d.isoformat(),
            "year": d.year,
            "month": d.month,
            "day": d.day,
            "day_of_week": d.strftime("%A"),
            "iso_week": d.isocalendar()[1],
        })
        d += timedelta(days=1)
    return rows


def build_fct_youtube_engagement_snapshot(storico_records):
    """Staging 1:1 dal JSON sorgente (metriche-video-storico.json) — nessuna
    aggregazione a monte, nessun filtro di scope: e' gia' scoped a upload YouTube
    confermati da raccogli_metriche_video.py (Fase 1). Il grain (video_id,
    snapshot_at) resta quello del sorgente, deliberatamente: i cluster di snapshot
    ravvicinati osservati in sviluppo sono artefatti (riavvii del LaunchAgent), non
    un regime stazionario su cui decidere un'aggregazione — quella si fa a livello
    di vista SQL nel mart, non qui."""
    return [{
        "video_id": r.get("video_id"),
        "content_key": r.get("key"),
        "snapshot_at": r.get("snapshot_at"),
        "views": r.get("views"),
        "likes": r.get("likes"),
        "comments": r.get("comments"),
    } for r in storico_records]


def build_fct_youtube_fixed_window(finestre_records):
    """Staging 1:1 da metriche-finestre-fisse.json (Fase 2). finestre_records e' il
    dict {video_id: record} cosi' com'e' scritto da raccogli_finestre_fisse.py."""
    return [{
        "video_id": rec.get("video_id"),
        "content_key": rec.get("key"),
        "views_day1": rec.get("views_day1"),
        "views_day2": rec.get("views_day2"),
        "views_day7": rec.get("views_day7"),
    } for rec in finestre_records.values()]


def build_fct_publish_event(registry_data_by_platform, valid_content_keys):
    """registry_data_by_platform: {"youtube": {key: record}, "instagram": {...},
    "tiktok": {...}} — gia' filtrato a CONFIRMED dal chiamante (vedi
    metriche_video.load_confirmed_uploads()/load_confirmed_youtube_uploads()).

    Un record la cui key non e' in valid_content_keys (dim_content) e' escluso
    esplicitamente — oggi questo esclude le foto standalone Instagram (fuori dal
    calendario video) e le eventuali Stories, mai in modo silenzioso: il conteggio
    delle righe escluse va sempre loggato dal chiamante.

    CONFIRMED non significa "visibile pubblicamente ora", significa "la piattaforma
    ha accettato l'upload" — privacy resta grezzo (mai un booleano is_public
    derivato), riflette lo stato al momento del confirm(), non lo stato live.

    Ritorna (righe, conteggio_escluse)."""
    rows = []
    excluded = 0
    for platform, records in registry_data_by_platform.items():
        id_field = ID_FIELD_BY_PLATFORM[platform]
        for key, record in records.items():
            if key not in valid_content_keys:
                excluded += 1
                continue
            rows.append({
                "content_key": key,
                "platform": platform,
                "external_id": record.get(id_field) or record.get("external_id"),
                "privacy": record.get("privacy"),
                "confirmed_at": record.get("confirmedAt"),
            })
    return rows, excluded
