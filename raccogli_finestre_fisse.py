#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
raccogli_finestre_fisse.py — raccolta delle finestre fisse (24h/48h/168h via
YouTube Analytics API) per il fix statistico di check_outliers.py (Fase 2 del
layer analytics).

Semantica diversa da raccogli_metriche_video.py (Fase 1): un record per video,
aggiornato finche' l'eta' del video e' dentro la finestra di ciascun campo, non una
riga append-only per esecuzione. Gira sullo stesso LaunchAgent
(com.calciovich.youtubestats, ogni 6h) — vedi aggiorna_youtube_stats.sh.

Politica di scrittura: aggiorna views_dayN finche' l'eta' del video e' < N+1 giorni
(margine di ri-lettura per dati Analytics ancora provvisori), poi congela — l'ultimo
valore scritto resta, senza bisogno di un flag esplicito. Scope di raccolta: video
pubblicati negli ultimi 8 giorni, non 7 — un giorno oltre l'ultima finestra tracciata
(day7), cosi' anche day7 ha lo stesso margine di day1/day2 prima di uscire di scope.

Il token Analytics e' dedicato (youtube_analytics_token.json, isolato da
carica_youtube.py): se il consenso scade, questo script non tenta MAI di riaprire
un browser (gira non presidiato) — scrive un marcatore esplicito
(analytics-consent-status.json) che stato_pipeline.py legge per la dashboard, e
salta pulito. Il consenso successivo e' un passo umano (youtube_analytics_auth.py).

USO
  python3 raccogli_finestre_fisse.py            # raccoglie e aggiorna lo storico
  python3 raccogli_finestre_fisse.py --dry-run   # mostra il piano, non scrive nulla
"""
import contextlib
import fcntl
import os
import sys
from datetime import date, datetime

import upload_registry
import youtube_analytics_auth
from metriche_video import fetch_fixed_windows, load

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "output")
YT_UPLOADS = os.path.join(OUTPUT, "youtube-uploads.json")
FINESTRE = os.path.join(OUTPUT, "metriche-finestre-fisse.json")
CONSENT_STATUS = os.path.join(OUTPUT, "analytics-consent-status.json")

SCOPE_DAYS = 8
WINDOW_FIELDS = {"views_day1": 1, "views_day2": 2, "views_day7": 7}


@contextlib.contextmanager
def collection_lock():
    """Stesso pattern di raccogli_metriche_video.py::collection_lock() — file di
    lock diverso perche' sono script indipendenti che possono girare in momenti
    diversi nello stesso LaunchAgent."""
    os.makedirs(OUTPUT, exist_ok=True)
    lock_path = os.path.join(OUTPUT, ".raccogli_finestre.lock")
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        sys.exit("Raccolta finestre fisse gia' in corso (lock occupato) — salto "
                  "questa esecuzione, riprova al prossimo trigger.")
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def _write_consent_status(consent_needed, error_class=None):
    payload = {
        "consent_needed": consent_needed,
        "since": datetime.now().isoformat(timespec="seconds"),
    }
    if error_class:
        payload["ultimo_errore"] = error_class
    upload_registry.save(CONSENT_STATUS, payload)


def build_plan(analytics_creds, today=None, windows_override=None):
    """Ritorna [(video_id, key, {campo: valore})] — solo i campi ancora dentro la
    propria finestra di aggiornamento (eta' < N+1). windows_override e' un dict
    {video_id: {data_iso: views}} iniettabile per i test di caratterizzazione,
    inoltrato a fetch_fixed_windows() com'e'."""
    if today is None:
        today = date.today()

    uploads = load(YT_UPLOADS, {})
    updates = []
    for key, meta in uploads.items():
        vid = meta.get("videoId")
        publish_at = meta.get("publishAt") or meta.get("uploadedAt")
        if not vid or not publish_at:
            continue
        publish_date = date.fromisoformat(publish_at[:10])
        age = (today - publish_date).days
        if age < 0 or age > SCOPE_DAYS:
            continue  # non ancora pubblicato, o uscito dallo scope di raccolta

        override = windows_override.get(vid) if windows_override is not None else None
        windows = fetch_fixed_windows(vid, publish_date, analytics_creds=analytics_creds,
                                       window_override=override, today=today)

        fields = {
            field: windows[field]
            for field, n in WINDOW_FIELDS.items()
            if age < n + 1 and windows.get(field) is not None
        }
        if fields:
            updates.append((vid, key, fields))
    return updates


def merge_windows(existing_records, updates):
    """Applica gli aggiornamenti ai record esistenti (dict per video_id) — un campo
    congelato (fuori dalla propria finestra di aggiornamento) non compare più in
    `updates` per quel video, quindi resta intatto qui. Ritorna (record_uniti,
    quanti valori sono davvero cambiati)."""
    merged = {vid: dict(rec) for vid, rec in existing_records.items()}
    changed = 0
    for vid, key, fields in updates:
        record = merged.setdefault(vid, {"video_id": vid, "key": key})
        record["key"] = key
        for field, value in fields.items():
            if record.get(field) != value:
                changed += 1
            record[field] = value
    return merged, changed


def main():
    dry_run = "--dry-run" in sys.argv

    try:
        from google.auth.exceptions import RefreshError
        refresh_errors = (RefreshError,)
    except ImportError:
        refresh_errors = ()  # google-auth non installato: nessuna RefreshError da intercettare
    consent_errors = (FileNotFoundError, ValueError) + refresh_errors

    if dry_run:
        try:
            creds = youtube_analytics_auth.get_analytics_credentials_unattended()
        except consent_errors as e:
            print(f"Consenso Analytics non disponibile ({type(e).__name__}) — "
                  f"--dry-run non può mostrare un piano reale finché non lo rifai "
                  f"(youtube_analytics_auth.py).")
            return
        updates = build_plan(analytics_creds=creds)
        print(f"{len(updates)} video con aggiornamenti da scrivere (nessuna scrittura, "
              f"--dry-run):")
        for vid, key, fields in updates:
            print(f"  {key} ({vid}): {fields}")
        return

    with collection_lock():
        try:
            creds = youtube_analytics_auth.get_analytics_credentials_unattended()
        except consent_errors as e:
            error_class = type(e).__name__
            print(f"⚠️  consenso Analytics non disponibile ({error_class}) — salto "
                  f"questa raccolta (rilancia youtube_analytics_auth.py da un "
                  f"terminale con browser per rinnovarlo).")
            _write_consent_status(consent_needed=True, error_class=error_class)
            return

        _write_consent_status(consent_needed=False)
        updates = build_plan(analytics_creds=creds)

        storico = load(FINESTRE, {"records": {}})
        merged, changed = merge_windows(storico.get("records", {}), updates)
        storico["records"] = merged
        upload_registry.save(FINESTRE, storico)
        print(f"Raccolta finestre fisse completata: {changed} valori aggiornati su "
              f"{len(updates)} video processati.")


if __name__ == "__main__":
    main()
