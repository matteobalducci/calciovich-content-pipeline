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
