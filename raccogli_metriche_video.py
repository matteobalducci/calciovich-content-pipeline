#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
raccogli_metriche_video.py — logger storico delle metriche per-video (Fase 1 del
layer analytics).

Gira sullo stesso LaunchAgent di aggiorna_youtube_stats.py (com.calciovich.youtubestats,
ogni 6h) — vedi aggiorna_youtube_stats.sh. Una riga per video per ESECUZIONE dello
script (non una al giorno): la Fase 2 (finestre fisse 24h/48h/168h via YouTube
Analytics API) ha bisogno di rilevazioni infragiornaliere per essere utile.

Nota deliberata, non un bug: questo script e check_outliers.py girano su trigger
indipendenti e leggono le view in momenti diversi. I due numeri per lo stesso video
nello stesso giorno divergono legittimamente — nessuno dei due e' "piu' autorevole".

USO
  python3 raccogli_metriche_video.py            # raccoglie e scrive lo storico
  python3 raccogli_metriche_video.py --dry-run   # mostra il piano — non scrive lo
                                                  # storico, ma la PRIMA apertura di
                                                  # Registry su un checkout pulito
                                                  # (publish-state.db non ancora
                                                  # creato, youtube-uploads.json si')
                                                  # importa quel JSON come righe vere
                                                  # nel DB (comportamento di Registry,
                                                  # non introdotto da --dry-run) —
                                                  # non e' "zero scritture" in quel caso
"""
import contextlib
import fcntl
from collections import namedtuple
import os
import sys
from datetime import datetime

import upload_registry
from metriche_video import (ChannelMismatch, categoria, _app_data_categoria_map, fetch_stats, load,
                             load_confirmed_youtube_uploads)

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "output")
YT_UPLOADS = os.path.join(OUTPUT, "youtube-uploads.json")                    # canale ORIGINALE
YT_UPLOADS_NEW = os.path.join(OUTPUT, "youtube-calciovich-uploads.json")     # canale del libro (dal 06/10/2026)
STORICO = os.path.join(OUTPUT, "metriche-video-storico.json")                  # canale originale (nome storico invariato)
STORICO_NEW = os.path.join(OUTPUT, "metriche-video-storico-calciovich.json")   # canale del libro


Profile = namedtuple("Profile", "key token channel_id")   # in produzione e' il profilo di canale del publisher


def sources():
    """UNA raccolta per canale: profilo, registro, file di storico. Valutata a ogni chiamata (non a import) perche'
    i test e gli ambienti redirigono i percorsi dei moduli. Il file di storico di un canale contiene SOLO righe di
    quel canale (channel_key obbligatorio nelle righe nuove): mai un unico file con due canali."""
    return [
        {"profile": Profile("gol-impossibili", os.path.join(HERE, "youtube_token.json"), "UCLPBYAv19aizEYX4MmXV7rA"),
         "uploads": YT_UPLOADS, "storico": STORICO},
        {"profile": Profile("calciovich", os.path.join(HERE, "youtube_libro_token.json"), "UCy1V7Lwaeb8_6iaSEzSOtPA"),
         "uploads": YT_UPLOADS_NEW, "storico": STORICO_NEW},
    ]


@contextlib.contextmanager
def collection_lock():
    """Stesso pattern di carica_youtube.py::publish_lock(): senza lock, un'esecuzione che dura piu' dell'intervallo fra due trigger
    (es. rete lenta sulle chiamate YouTube Data API) puo' sovrapporsi alla successiva ed
    interlacciare le scritture su metriche-video-storico.json — lo stesso pattern che ha
    gia' corrotto una serie storica in aggiorna_youtube_stats.py (vedi il suo commento)."""
    os.makedirs(OUTPUT, exist_ok=True)
    lock_path = os.path.join(OUTPUT, ".raccogli_metriche.lock")
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        sys.exit("Raccolta metriche gia' in corso (lock occupato) — salto questa "
                  "esecuzione, riprova al prossimo trigger.")
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def build_plan(stats_override=None, source=None):
    """Ritorna (snapshot_at, righe_da_scrivere) PER UN CANALE (`source`, default il canale originale) — nessun
    effetto collaterale, cosi' --dry-run puo' condividere la stessa logica del run reale.

    stats_override e' inoltrato a fetch_stats() com'e': parametro esplicito, non monkeypatching — stesso
    meccanismo di test_metriche_video.py. Con la rete: il token e' quello del canale e viene verificato
    (check_owner) prima di leggere: un token sbagliato non produce righe."""
    source = source or sources()[0]
    profile = source["profile"]
    uploads = load_confirmed_youtube_uploads(source["uploads"])
    app_map = _app_data_categoria_map()

    entries = []
    for key, meta in uploads.items():
        vid = meta.get("videoId")
        if vid:
            entries.append((key, vid))

    video_ids = [vid for _, vid in entries]
    stats = fetch_stats(video_ids, stats_override=stats_override, token_path=profile.token,
                        expect_channel_id=profile.channel_id) if video_ids else {}

    snapshot_at = datetime.now().isoformat(timespec="seconds")
    rows = []
    for key, vid in entries:
        info = stats.get(vid, {})
        if info.get("privacy") != "public":
            continue  # non ancora live (o reso privato): niente da registrare in questo run
        rows.append({
            "video_id": vid,
            "key": key,
            "categoria": categoria(key, app_map),
            "snapshot_at": snapshot_at,
            "views": info.get("views"),
            "likes": info.get("likes"),
            "comments": info.get("comments"),
            "channel_key": profile.key,
        })
    return snapshot_at, rows


def merge_records(existing_records, new_rows):
    """Dedup su (video_id, snapshot_at) — una riga per video per esecuzione, mai due
    per lo stesso timestamp di raccolta. Ritorna (record_uniti, quante_nuove)."""
    merged = list(existing_records)
    existing_keys = {(r["video_id"], r["snapshot_at"]) for r in existing_records}

    added = 0
    for r in new_rows:
        dedup_key = (r["video_id"], r["snapshot_at"])
        if dedup_key in existing_keys:
            continue
        merged.append(r)
        existing_keys.add(dedup_key)
        added += 1
    return merged, added


def _build_plan_or_none(source=None):
    """build_plan() puo' propagare RefreshError se il token del canale e' scaduto o revocato — capita ogni ~7
    giorni circa su un progetto GCP non verificato (vedi carica_youtube.py::get_service()). Questo script gira
    non presidiato su un LaunchAgent ogni 6h: non puo' completare un consenso interattivo, quindi degrada
    pulito (nessuna riga scritta in questo run per QUEL canale) invece di lasciar propagare un traceback. Il
    token si aggiorna al prossimo giro di carica_youtube.py (presidiato).

    ChannelMismatch (token che non appartiene al canale atteso) e' un errore vero: si dice e non si scrive nulla
    per quel canale, ma l'altro canale non si ferma. L'import e' avvolto in un ImportError perche' google-auth
    non e' un requisito per il percorso senza errori (es. --dry-run con stats_override nei test)."""
    try:
        from google.auth.exceptions import RefreshError
    except ImportError:
        RefreshError = ()
    name = (source or sources()[0])["profile"].key
    try:
        return build_plan(source=source)
    except RefreshError:
        print(f"⚠️  [{name}] token scaduto/revocato — salto questa raccolta "
              "(si autorisolve al prossimo consenso di carica_youtube.py).")
        return None
    except ChannelMismatch as e:
        print(f"⛔ [{name}] {e} — nessuna riga scritta per questo canale.")
        return None


def main():
    dry_run = "--dry-run" in sys.argv
    failures = 0

    if dry_run:
        for source in sources():
            result = _build_plan_or_none(source)
            name = source["profile"].key
            if result is None:
                failures += 1
                continue
            snapshot_at, rows = result
            print(f"[{name}] snapshot_at: {snapshot_at}")
            print(f"[{name}] {len(rows)} righe verrebbero scritte (nessuna scrittura, --dry-run):")
            for r in rows:
                print(f"  [{r['categoria']}] {r['key']}: {r['views']} views, "
                      f"{r['likes']} like, {r['comments']} commenti")
        return 1 if failures else 0

    with collection_lock():
        for source in sources():
            name = source["profile"].key
            result = _build_plan_or_none(source)
            if result is None:
                failures += 1
                continue
            snapshot_at, rows = result
            if not rows and not os.path.exists(source["storico"]):
                print(f"[{name}] nessun video pubblico da registrare: nessun file creato.")
                continue

            storico = load(source["storico"], {"records": []})
            merged, added = merge_records(storico["records"], rows)
            storico["records"] = merged

            upload_registry.save(source["storico"], storico)
            print(f"[{name}] Raccolta metriche completata: {added} nuove righe "
                  f"({len(rows) - added} gia' presenti), storico totale: {len(merged)} righe.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
