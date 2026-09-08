#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_outliers.py — trigger immediato per outlier evidenti (direttiva autore 04/08).

La revisione quindicinale resta lo strumento per decisioni strutturali (serve più
tempo/dati). Questo script e' il controllo LEGGERO che gira dentro la routine
giornaliera (STEP 0) per non aspettare 15 giorni quando un singolo video e'
palesemente fuori scala rispetto al suo formato — vedi vault/playbook/decisioni-coach.md
e output/ai-content-queue.json (rules, voce "COSTANZA YOUTUBE...").

Confronta le view YouTube (readonly, stesso token di carica_youtube.py) dell'ULTIMO
video pubblicato di ogni formato con la mediana degli altri video recenti dello
stesso formato:
  - views >= 5x la mediana  -> WIN outlier (ripeti la stessa formula appena possibile)
  - views <= 0.2x la mediana (e formato con >=3 uscite) -> FAIL outlier (pausa
    immediata di quella sotto-variante, stesso trattamento gia' dato a vecio_dixe/
    tomasito dalla revisione, ma senza aspettare 15 giorni)
Non tocca MAI la strategia di fondo (budget, rotazione, obiettivi) — solo azioni
reversibili e già praticate in questo progetto (pausa/preferenza di un format).

USO
  python3 check_outliers.py                # stampa il report e scrive outlier-flags.json
  python3 check_outliers.py --apply         # DEPRECATO: i FAIL non sospendono piu'
                                            # nulla da soli (vedi la nota nel codice).
                                            # Restava per: 
                                             # rotation-state.json / ai-content-queue.json
                                             # (stesso meccanismo gia' usato per vecio_dixe/tomasito)
"""
import os, sys, json, statistics

import upload_registry  # usato direttamente per upload_registry.save(OUT, ...) sotto
from metriche_video import load, categoria, _app_data_categoria_map, fetch_stats

HERE = os.path.dirname(os.path.abspath(__file__))
YT_UPLOADS = os.path.join(HERE, "output", "youtube-uploads.json")
ROTATION = os.path.join(HERE, "output", "rotation-state.json")
QUEUE = os.path.join(HERE, "output", "ai-content-queue.json")
OUT = os.path.join(HERE, "output", "outlier-flags.json")

WIN_MULT = 5.0
FAIL_MULT = 0.2
MIN_HISTORY = 3  # servono almeno 3 uscite precedenti nello stesso formato per fidarsi della mediana

def main():
    apply_changes = "--apply" in sys.argv

    uploads = load(YT_UPLOADS, {})
    app_map = _app_data_categoria_map()
    # ordina per data di pubblicazione effettiva (publishAt se presente e passato, altrimenti uploadedAt)
    entries = []
    for key, meta in uploads.items():
        vid = meta.get("videoId")
        if not vid:
            continue
        data_ord = meta.get("publishAt") or meta.get("uploadedAt") or ""
        entries.append((data_ord, key, vid, categoria(key, app_map)))
    entries.sort(key=lambda e: e[0])

    video_ids = [e[2] for e in entries]
    print(f"Recupero statistiche per {len(video_ids)} video…")
    stats = fetch_stats(video_ids)

    by_cat = {}
    for data_ord, key, vid, cat in entries:
        if cat == "altro":
            continue
        info = stats.get(vid, {})
        v = info.get("views")
        if v is None:
            continue
        if info.get("privacy") != "public":
            continue  # non ancora live (schedulato/privato): 0 view non e' un FAIL, e' "non ancora uscito"
        by_cat.setdefault(cat, []).append((data_ord, key, vid, v))

    flags = []
    print()
    for cat, items in by_cat.items():
        if len(items) < MIN_HISTORY + 1:
            continue
        *history, latest = items
        med = statistics.median(v for *_, v in history)
        _, key, vid, v = latest
        if med <= 0:
            continue
        ratio = v / med
        tag = None
        if ratio >= WIN_MULT:
            tag = "WIN"
        elif ratio <= FAIL_MULT:
            tag = "FAIL"
        marker = f" ⚠️ {tag} OUTLIER (x{ratio:.1f} vs mediana {med:.0f})" if tag else ""
        print(f"[{cat}] {key}: {v} views (mediana formato: {med:.0f}){marker}")
        if tag:
            flags.append({"categoria": cat, "key": key, "videoId": vid, "views": v,
                           "mediana": med, "ratio": round(ratio, 2), "tipo": tag})

    if not flags:
        print("\nNessun outlier evidente oggi.")
    else:
        print(f"\n{len(flags)} outlier trovati:")
        for f in flags:
            print(f"  {f['tipo']} — {f['key']} ({f['categoria']}): {f['views']} views, "
                  f"x{f['ratio']} vs mediana {f['mediana']:.0f}")

    upload_registry.save(OUT, {"generatedAt": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
                               "flags": flags})

    if apply_changes:
        # BUGFIX 02/09 — LIMITE STATISTICO NOTO: il confronto e' fra le view
        # LIFETIME di video vecchi e quelle di un video appena uscito, che ha
        # avuto poche ore per accumularle. Un video nuovo e' quindi
        # strutturalmente spinto verso FAIL, e con --apply questo bastava a
        # SOSPENDERE automaticamente un formato. Un falso positivo che spegne un
        # formato costa molto piu' di un vero positivo scoperto un giorno dopo.
        #
        # Finche' il confronto non passa a finestre fisse dalla pubblicazione
        # (view a 24/48/168 ore, che richiedono la Analytics API — vedi la fase
        # BigQuery), i FAIL non modificano piu' nulla da soli: si segnalano e
        # basta. I WIN non sono simmetrici, perche' un falso WIN non spegne
        # niente.
        fails = [f for f in flags if f["tipo"] == "FAIL"]
        if fails:
            print("\n⚠️  FAIL rilevati, NON applicati automaticamente:")
            for f in fails:
                print(f"   • {f['key']} ({f['categoria']}): {f['views']} views, "
                      f"x{f['ratio']} vs mediana {f['mediana']:.0f}")
            print("   Il confronto con le view lifetime penalizza i video appena usciti.")
            print("   Verifica l'eta' del video prima di decidere; per sospendere davvero "
                  "un formato, modifica rotation-state.json a mano.")
        wins = [f for f in flags if f["tipo"] == "WIN"]
        if wins:
            print("  → WIN outlier: nessuna modifica automatica di stato, va solo preferito nel prossimo slot eleggibile dello stesso formato (decisione della sessione che pubblica).")

if __name__ == "__main__":
    main()
