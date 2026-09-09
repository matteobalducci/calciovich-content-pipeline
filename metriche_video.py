#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metriche_video.py — classificazione per-formato e recupero statistiche YouTube,
condivisi fra check_outliers.py e raccogli_metriche_video.py (Fase 1 del layer
analytics).

Estratto da check_outliers.py senza cambiarne il comportamento osservabile —
check_outliers.py importa da qui invece di ridefinire la stessa logica.
"""
import os, sys, re
from datetime import date, timedelta

import upload_registry  # scrittura/lettura atomica

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(HERE, "youtube_token.json")
APP_DATA = os.path.join(HERE, "app", "data.json")

CHARACTER_KEYS = ["vecio-dixe", "vecio_dixe", "non-sa-fare", "non_sa_fare",
                  "subentro-decisivo", "subentro_decisivo", "esordio", "tomasito"]


def load(path, default):
    """BUGFIX 02/09 (audit Codex): ingoiava qualsiasi errore e restituiva il
    default, quindi un registro corrotto diventava "nessun video da analizzare"
    e il report veniva riscritto con un falso stato pulito — l'opposto della
    politica fail-closed adottata nei publisher. Ora un file assente e' normale,
    un file illeggibile e' un errore."""
    try:
        return upload_registry.load(path) or default
    except upload_registry.RegistryCorrupt:
        raise


def load_confirmed_uploads(path, id_field):
    """BUGFIX (dibattito di controllo Fase 3, 09/09): dal commit 827ba65 (02/09) lo
    stato di pubblicazione vive in SQLite (upload_registry.Registry) — i JSON legacy
    (youtube/instagram/tiktok-uploads.json) restano solo backup importati una tantum,
    mai piu' risincronizzati. Leggerli direttamente con load() (come facevano ancora
    raccogli_metriche_video.py, raccogli_finestre_fisse.py e check_outliers.py per
    YouTube, scritti l'8-9/09 ma contro la fonte gia' superata) significa perdere in
    silenzio ogni upload confermato dopo quella data. stato_pipeline.py legge gia'
    correttamente da Registry per Instagram: questa funzione generalizza lo stesso
    pattern a qualunque piattaforma (id_field e' il nome campo specifico della
    piattaforma nel meta: "videoId"/"mediaId"/"publishId").

    Solo CONFIRMED: un record pending e' un tentativo di upload non ancora risolto
    (vedi upload_registry.py), non un item da trattare come pubblicato.

    SECONDO BUGFIX (dibattito di controllo sul primo, stesso giorno): Registry.reconcile()
    conferma un record recuperato dopo un crash/timeout con
    self.confirm(key, external_id, recoveredAt=...) — external_id finisce nella colonna
    riservata, MAI in meta col nome campo piattaforma-specifico (Registry e'
    volutamente agnostico rispetto alla piattaforma). reconcile_pending() gira ad OGNI
    esecuzione dei publisher, non e' un caso raro — verificato per YouTube e Instagram
    (entrambi perdono il campo id piattaforma-specifico dopo un recovery; TikTok no,
    "publishId" e' scritto prima di qualunque crash possibile). external_id e id_field
    sono la stessa cosa nel percorso felice (vedi carica_youtube.py:
    confirm(key, external_id=vid, videoId=vid, ...)) — quando manca il primo, il
    secondo e' sempre presente per un record davvero CONFIRMED (confirm() lo richiede
    come parametro posizionale)."""
    registry = upload_registry.Registry(path)
    out = {}
    for key, record in registry.data.items():
        if upload_registry.state_of(record) != upload_registry.CONFIRMED:
            continue
        if not record.get(id_field) and record.get("external_id"):
            record = {**record, id_field: record["external_id"]}
        out[key] = record
    return out


def load_confirmed_youtube_uploads(path):
    """Caso specifico YouTube di load_confirmed_uploads() — mantenuta come funzione
    a se' perche' e' l'unica chiamata da codice gia' in produzione (Fase 1/2)."""
    return load_confirmed_uploads(path, "videoId")


def _app_data_categoria_map(app_data=None):
    """app/data.json (weeks[].items[].categoria) e' la fonte autorevole per distinguere
    'personaggio' dai canonici veri: entrambi usano lo stesso schema shortNN-*.vert, il
    nome file da solo non basta (es. short26 e' 'Personaggio: Esordio', non canonical).

    app_data e' iniettabile per i test di caratterizzazione; di default legge APP_DATA."""
    if app_data is None:
        app_data = load(APP_DATA, {})
    out = {}
    for w in app_data.get("weeks", []):
        for it in w.get("items", []):
            f = it.get("file") or ""
            base = os.path.splitext(os.path.basename(f))[0]
            cat = (it.get("categoria") or "")
            if cat.startswith("Personaggio"):
                out[base] = "personaggio"
            elif cat in ("Pronto", "Micro-short", "Re-cut da long-form"):
                out[base] = "canonical"
            elif cat == "Audiolibro (costo zero)":
                out[base] = "long-form"
    return out


def categoria(key, app_map):
    if key in app_map:
        return app_map[key]
    k = key.lower()
    if any(c in k for c in CHARACTER_KEYS):
        return "personaggio"
    if re.match(r"^short\d+", k):
        return "canonical"
    if k.startswith("ep") and re.match(r"^ep\d+-", k):
        return "long-form"
    if "libro-" in k or k.startswith("libro"):
        return "long-form"
    if "settimana" in k and "day" in k:
        return "gol-ai"
    return "altro"


def fetch_stats(video_ids, stats_override=None):
    """Ritorna {video_id: {"views", "likes", "comments", "privacy"}}.

    stats_override e' iniettabile per i test di caratterizzazione: se passato, viene
    restituito direttamente senza chiamare l'API YouTube (nessuna credenziale/rete
    necessaria) — isola l'effetto di un refactor dal drift dei dati live.

    check_outliers.py legge solo "views"/"privacy" (comportamento invariato); i campi
    "likes"/"comments" servono a raccogli_metriche_video.py, gia' presenti nella stessa
    risposta API (part="statistics,status") ma finora non estratti."""
    if stats_override is not None:
        return stats_override

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    import googleapiclient.discovery

    if not os.path.exists(TOKEN_PATH):
        sys.exit(f"Manca {TOKEN_PATH} — non posso leggere le statistiche.")
    creds = Credentials.from_authorized_user_file(TOKEN_PATH,
        ["https://www.googleapis.com/auth/youtube.upload",
         "https://www.googleapis.com/auth/youtube.readonly"])
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        open(TOKEN_PATH, "w").write(creds.to_json())
    yt = googleapiclient.discovery.build("youtube", "v3", credentials=creds)

    out = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i + 50]
        resp = yt.videos().list(part="statistics,status", id=",".join(chunk)).execute()
        for item in resp.get("items", []):
            stats = item.get("statistics", {})
            vc = stats.get("viewCount")
            lc = stats.get("likeCount")
            cc = stats.get("commentCount")
            privacy = item.get("status", {}).get("privacyStatus")
            out[item["id"]] = {
                "views": int(vc) if vc is not None else None,
                "likes": int(lc) if lc is not None else None,
                "comments": int(cc) if cc is not None else None,
                "privacy": privacy,
            }
    return out


def fetch_fixed_windows(video_id, publish_date, analytics_creds=None,
                        window_override=None, today=None):
    """Ritorna {"views_day1", "views_day2", "views_day7"} — cumulato dai bucket
    giornalieri di reports().query() dal giorno di pubblicazione. Ogni finestra e'
    None finche' il video non ha raggiunto quell'eta' (non un finto zero).

    Per-video, non batched: verificato con una chiamata reale che
    reports().query() risponde 400 Bad Request senza filters=video==ID — la YouTube
    Analytics API non offre un equivalente batched per questo report, a differenza
    della Data API (fetch_stats, chunk da 50).

    window_override e' iniettabile per i test di caratterizzazione: se passato, e'
    il dict {data_iso: views} che l'API avrebbe restituito per questo video — la
    logica di eta'/cumulato gira invariata sopra, isolando l'effetto della rete dal
    resto. today e' iniettabile allo stesso modo (default date.today())."""
    if isinstance(publish_date, str):
        publish_date = date.fromisoformat(publish_date)
    if today is None:
        today = date.today()
    age = (today - publish_date).days

    if window_override is not None:
        daily_views = window_override
    else:
        import googleapiclient.discovery
        yt_analytics = googleapiclient.discovery.build(
            "youtubeAnalytics", "v2", credentials=analytics_creds
        )
        end_date = min(today, publish_date + timedelta(days=7))
        resp = yt_analytics.reports().query(
            ids="channel==MINE",
            startDate=publish_date.isoformat(),
            endDate=end_date.isoformat(),
            metrics="views",
            dimensions="day",
            filters=f"video=={video_id}",
            sort="day",
        ).execute()
        daily_views = {row[0]: row[1] for row in resp.get("rows", [])}

    def cumulative_through(n_days):
        total = 0
        found_any = False
        for i in range(n_days):
            d = (publish_date + timedelta(days=i)).isoformat()
            if d in daily_views:
                total += daily_views[d]
                found_any = True
        return total if found_any else None

    return {
        "views_day1": cumulative_through(1) if age >= 1 else None,
        "views_day2": cumulative_through(2) if age >= 2 else None,
        "views_day7": cumulative_through(7) if age >= 7 else None,
    }
