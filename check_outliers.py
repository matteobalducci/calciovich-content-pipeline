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
FINESTRE_FISSE = os.path.join(HERE, "output", "metriche-finestre-fisse.json")

WIN_MULT = 5.0
FAIL_MULT = 0.2
MIN_HISTORY = 3  # servono almeno 3 uscite precedenti nello stesso formato per fidarsi della mediana


def _views_day1_by_video(finestre_records):
    """{video_id: views_day1} per i video che ce l'hanno (Fase 2, YouTube Analytics
    API — vedi metriche_video.fetch_fixed_windows()). Il file puo' non esistere
    ancora (Fase 2 mai girata) o essere illeggibile: e' un dato opzionale che
    migliora il confronto quando c'e', non un requisito — degrada a nessun dato
    fisso, mai a un crash di check_outliers.py. Tollera anche una struttura
    inattesa (non un dict, o un record che non e' un dict) invece di sollevare
    AttributeError — un file toccato a mano o uno schema legacy non deve far
    crashare il controllo LEGGERO di STEP 0."""
    out = {}
    if not isinstance(finestre_records, dict):
        return out
    for vid, rec in finestre_records.items():
        if not isinstance(rec, dict):
            continue
        d1 = rec.get("views_day1")
        if d1 is not None:
            out[vid] = d1
    return out


def _choose_comparison(history, latest, views_day1_by_video):
    """Ritorna (v_latest, mediana, base) — base e' 'views_day1' o 'lifetime'.

    BUGFIX statistico (Fase 2): la
    finestra fissa si usa SOLO se sia il video piu' recente SIA un campione omogeneo
    di storia (>= MIN_HISTORY video, tutti con views_day1) ce l'hanno — mai un mix
    fisso/lifetime nella stessa mediana, che reintrodurrebbe lo stesso bias dentro
    il calcolo invece che fra latest e mediana. Se manca anche solo uno dei due lati,
    resta il confronto lifetime-vs-lifetime di sempre — nessuna forzatura."""
    _, _, latest_vid, latest_lifetime = latest
    latest_day1 = views_day1_by_video.get(latest_vid)

    if latest_day1 is not None:
        history_day1 = [views_day1_by_video[vid] for *_, vid, _ in history
                         if vid in views_day1_by_video]
        if len(history_day1) >= MIN_HISTORY:
            return latest_day1, statistics.median(history_day1), "views_day1"

    return latest_lifetime, statistics.median(v for *_, v in history), "lifetime"


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

    try:
        finestre_records = json.load(open(FINESTRE_FISSE, encoding="utf-8")).get("records", {})
    except FileNotFoundError:
        finestre_records = {}  # Fase 2 mai girata — normale, non un errore
    except Exception as e:
        # File presente ma illeggibile/corrotto: degrada a lifetime (mai un crash
        # qui), ma NON in silenzio — altrimenti il fix statistico della Fase 2
        # smette di funzionare senza che nessuno se ne accorga.
        print(f"⚠️  {FINESTRE_FISSE} illeggibile ({type(e).__name__}) — confronto solo "
              f"lifetime per questo run.")
        finestre_records = {}
    views_day1_by_video = _views_day1_by_video(finestre_records)

    flags = []
    print()
    for cat, items in by_cat.items():
        if len(items) < MIN_HISTORY + 1:
            continue
        *history, latest = items
        v, med, base = _choose_comparison(history, latest, views_day1_by_video)
        _, key, vid, _ = latest
        if med <= 0:
            continue
        ratio = v / med
        tag = None
        if ratio >= WIN_MULT:
            tag = "WIN"
        elif ratio <= FAIL_MULT:
            tag = "FAIL"
        base_note = " (finestra 24h)" if base == "views_day1" else ""
        marker = f" ⚠️ {tag} OUTLIER (x{ratio:.1f} vs mediana {med:.0f})" if tag else ""
        print(f"[{cat}] {key}: {v} views{base_note} (mediana formato: {med:.0f}){marker}")
        if tag:
            flags.append({"categoria": cat, "key": key, "videoId": vid, "views": v,
                           "mediana": med, "ratio": round(ratio, 2), "tipo": tag,
                           "base": base})

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
        # BUGFIX 02/09 — LIMITE STATISTICO NOTO: il confronto fra view LIFETIME di
        # video vecchi e quelle di un video appena uscito e' strutturalmente
        # sbilanciato verso FAIL, e con --apply questo bastava a SOSPENDERE
        # automaticamente un formato. Un falso positivo che spegne un formato
        # costa molto piu' di un vero positivo scoperto un giorno dopo.
        #
        # _choose_comparison() ora usa la finestra fissa a 24h (YouTube Analytics
        # API, Fase 2) quando sia il video piu' recente sia un campione omogeneo
        # di storia (>= MIN_HISTORY video) ce l'hanno — ma finche' quel campione
        # non e' abbastanza numeroso il confronto resta lifetime-vs-lifetime,
        # quindi il bias sopra puo' ancora presentarsi. --apply resta deprecato
        # per lo stesso motivo di sempre: un FAIL non deve sospendere nulla da
        # solo finche' non c'e' garanzia che il confronto sia quello corretto. I
        # FAIL si segnalano e basta. I WIN non sono simmetrici, perche' un falso
        # WIN non spegne niente.
        fails = [f for f in flags if f["tipo"] == "FAIL"]
        if fails:
            print("\n⚠️  FAIL rilevati, NON applicati automaticamente:")
            for f in fails:
                base_note = " (finestra 24h)" if f.get("base") == "views_day1" else " (lifetime)"
                print(f"   • {f['key']} ({f['categoria']}): {f['views']} views{base_note}, "
                      f"x{f['ratio']} vs mediana {f['mediana']:.0f}")
            print("   Il confronto lifetime penalizza i video appena usciti — verifica se il")
            print("   FAIL qui sopra è a finestra fissa o a lifetime prima di decidere; per")
            print("   sospendere davvero un formato, modifica rotation-state.json a mano.")
        wins = [f for f in flags if f["tipo"] == "WIN"]
        if wins:
            print("  → WIN outlier: nessuna modifica automatica di stato, va solo preferito nel prossimo slot eleggibile dello stesso formato (decisione della sessione che pubblica).")

if __name__ == "__main__":
    main()
