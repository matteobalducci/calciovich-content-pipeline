#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gestisci_playlist.py — crea/aggiorna le playlist YouTube del canale Calciovich.

Tre playlist mantenute:
  1) "Gol Impossibili" — tutti i gol-ai numerati (#1..#N), in ordine crescente.
  2) "La storia in ordine" — i capitoli canonici (short01..shortNN, INCLUSI i
     re-cut 21-23) in ordine crescente, cosi' un nuovo spettatore puo' seguire
     la trama nell'ordine giusto in formato breve.
  3) "La storia in formato esteso" — i long-form audiolibro. Finche' la serie
     libro-pN (formato approvato piu' recente) non copre ancora tutto il libro,
     contiene epN + libro-pN insieme in ordine di uscita (transizione). Appena
     libro-pN raggiunge l'ultimo capitolo (confrontato con 04-capitoli/, via
     rotation-state.json['longform_libro_capitoli_coperti']), lo script TOGLIE
     da solo ep1-10 (formato precedente, gia' uno spoiler completo dall'inizio
     alla fine) e lascia solo libro-pN — deciso con l'autore il 02/09.

Pensato per girare DA SOLO dopo ogni pubblicazione YouTube (viene richiamato
in automatico da carica_youtube.py a fine upload) — e' idempotente: aggiunge
solo i video mancanti, non tocca l'ordine di quelli gia' in playlist, quindi
si puo' rilanciare quante volte si vuole senza rischi.

Usa lo stesso token/scope di carica_youtube.py (gia' ha "youtube" completo dal
rescope del 21/08 — richiesto per creare/popolare playlist).

USO
  python3 gestisci_playlist.py --dry-run     # mostra il piano, non tocca nulla
  python3 gestisci_playlist.py               # crea/aggiorna per davvero
"""
import os, re, json, sys, argparse
import googleapiclient.discovery
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(HERE, "youtube_token.json")
YT_UPLOADS = os.path.join(HERE, "output", "youtube-uploads.json")
APP_DATA = os.path.join(HERE, "app", "data.json")
STATE_PATH = os.path.join(HERE, "output", "playlists-state.json")
ROTATION_STATE = os.path.join(HERE, "output", "rotation-state.json")
CAPITOLI_DIR = os.path.join(HERE, "..", "..", "04-capitoli")  # root del progetto

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube",
]

GOLAI_TITLE = "Gol Impossibili"
GOLAI_DESC = ("Tutti i gol impossibili di Calciovich, in ordine cronologico crescente dal #1.\n\n"
              "Personaggio di fantasia. Video realizzati con strumenti di AI.")
STORY_TITLE = "La storia in ordine"
STORY_DESC = ("I capitoli della Vera Storia di Calciovich, in ordine cronologico crescente — "
              "il modo giusto per guardarli se e' la prima volta che li scopri.\n\n"
              "Il libro completo: https://calciovich.gumroad.com/l/laverastoriadicalciovich")
LONGFORM_TITLE = "La storia in formato esteso"
LONGFORM_DESC = ("La Vera Storia di Calciovich raccontata in episodi lunghi, come un "
                  "audiolibro — per chi la vuole ascoltare tutta d'un fiato invece che a "
                  "pezzi.\n\nIl libro completo: "
                  "https://calciovich.gumroad.com/l/laverastoriadicalciovich")


def load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


def get_service():
    if not os.path.exists(TOKEN_PATH):
        sys.exit(f"Manca {TOKEN_PATH}.")
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        open(TOKEN_PATH, "w").write(creds.to_json())
    return googleapiclient.discovery.build("youtube", "v3", credentials=creds)


def gol_impossibili_ids(yt_uploads):
    """Ordina tutti i gol-ai numerati per #N reale (dal titolo live su YouTube,
    fonte di verita' dopo eventuali correzioni post-pubblicazione). I 3 item di
    'settimana1' (day1/day2/day6) precedono il backfill del source_id (01/08) e
    non lo hanno mai avuto - riconosciuti via pattern filename, stesso euristica
    di check_outliers.py."""
    ids = []
    for k, v in yt_uploads.items():
        sid = v.get("source_id", "")
        is_golai = sid.startswith("gol-ai|") or ("settimana" in k and "day" in k)
        if is_golai and v.get("videoId"):
            ids.append(v["videoId"])
    return ids


def story_chapter_ids():
    """short01..shortNN con categoria 'Pronto' (canonici + re-cut 21-23, esclude
    Personaggio/Re-cut da long-form/gol-ai che hanno altre categorie) in ordine
    numerico crescente."""
    data = load(APP_DATA, {})
    yt_uploads = load(YT_UPLOADS, {})
    out = []
    for w in data.get("weeks", []):
        for it in w.get("items", []):
            f = it.get("file") or ""
            cat = it.get("categoria") or ""
            m = re.match(r"/output/short0*(\d+)-", f)
            if not m or cat != "Pronto":
                continue
            key = re.sub(r"^/output/", "", f)
            key = re.sub(r"\.mp4$", "", key)
            pub = yt_uploads.get(key, {})
            if pub.get("videoId"):
                out.append((int(m.group(1)), pub["videoId"]))
    out.sort(key=lambda x: x[0])
    return [vid for _, vid in out]


def last_chapter_number():
    """Ultimo numero di capitolo del libro (da 04-capitoli/, es. '20-epilogo.md' -> 20).
    Conta i file veri invece di un numero fisso, cosi' regge se il libro cambia lunghezza."""
    best = None
    if os.path.isdir(CAPITOLI_DIR):
        for f in os.listdir(CAPITOLI_DIR):
            m = re.match(r"^(\d+)-", f)
            if m:
                n = int(m.group(1))
                best = n if best is None else max(best, n)
    return best


def libro_pn_complete():
    """True se la serie libro-pN (formato long-form approvato, vedi memoria
    'longform-audiolibro-formato-confermato') ha ormai coperto l'intero libro,
    in base a rotation-state.json['longform_libro_capitoli_coperti'] (es. '00-05')
    confrontato con l'ultimo capitolo reale in 04-capitoli/."""
    rot = load(ROTATION_STATE, {})
    coperti = rot.get("longform_libro_capitoli_coperti", "")
    m = re.search(r"(\d+)\s*$", coperti)
    if not m:
        return False
    fine_coperta = int(m.group(1))
    ultimo = last_chapter_number()
    if ultimo is None:
        return False
    return fine_coperta >= ultimo


def longform_ids(yt_uploads, libro_only):
    """epN + libro-pN (audiolibro long-form). Se libro_only=True (serie libro-pN
    ha ormai coperto tutto il libro, formato approvato — vedi memoria
    'longform-audiolibro-formato-confermato'): SOLO libro-pN, in ordine numerico,
    la serie ep1-10 (formato precedente, con contenuto ridondante/spoiler
    prematuro) esce dalla playlist. Altrimenti: entrambe le serie miste, in
    ordine di uscita (publishAt se presente e passato, altrimenti uploadedAt) —
    comportamento "di transizione" finche' libro-pN non e' completa."""
    if libro_only:
        rows = []
        for k, v in yt_uploads.items():
            if not v.get("videoId") or not k.startswith("libro-p"):
                continue
            m = re.match(r"^libro-p(\d+)", k)
            n = int(m.group(1)) if m else 0
            rows.append((n, v["videoId"]))
        rows.sort(key=lambda x: x[0])
        return [vid for _, vid in rows]

    rows = []
    for k, v in yt_uploads.items():
        if not v.get("videoId"):
            continue
        if re.match(r"^ep\d+-", k) or k.startswith("libro-p"):
            order = v.get("publishAt") or v.get("uploadedAt") or ""
            rows.append((order, v["videoId"]))
    rows.sort(key=lambda x: x[0])
    return [vid for _, vid in rows]


def fetch_titles(yt, ids):
    out = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        resp = yt.videos().list(part="snippet", id=",".join(chunk)).execute()
        for item in resp.get("items", []):
            out[item["id"]] = item["snippet"]["title"]
    return out


def find_playlist(yt, title):
    # BUGFIX 02/09: senza paginazione, oltre 50 playlist non si trovava quella
    # esistente e se ne creava una DUPLICATA a ogni run.
    items, page = [], None
    while True:
        resp = yt.playlists().list(part="snippet", mine=True, maxResults=50,
                                   pageToken=page).execute()
        items.extend(resp.get("items", []))
        page = resp.get("nextPageToken")
        if not page:
            break
    resp = {"items": items}
    for it in resp.get("items", []):
        if it["snippet"]["title"] == title:
            return it["id"]
    return None


def existing_items(yt, playlist_id):
    """videoId -> playlistItem id, per sapere cosa manca senza duplicare."""
    import googleapiclient.errors
    out = {}
    page = None
    while True:
        try:
            resp = yt.playlistItems().list(part="contentDetails", playlistId=playlist_id,
                                            maxResults=50, pageToken=page).execute()
        except googleapiclient.errors.HttpError as e:
            if e.resp.status == 404:
                # playlist appena creata, non ancora propagata lato API: trattarla come vuota
                return out
            raise
        for it in resp.get("items", []):
            out[it["contentDetails"]["videoId"]] = it["id"]
        page = resp.get("nextPageToken")
        if not page:
            break
    return out


def remove_from_playlist(yt, playlist_id, video_ids_to_remove, dry_run):
    """Toglie da una playlist i video il cui id e' in video_ids_to_remove (se
    presenti). Usato SOLO per lo switch mirato ep1-10 -> libro-pN quando la
    serie libro-pN diventa completa - non e' una sync generica a doppio senso,
    per non rischiare rimozioni accidentali sulle altre playlist."""
    if not video_ids_to_remove:
        return 0
    have = existing_items(yt, playlist_id)
    removed = 0
    for vid in video_ids_to_remove:
        item_id = have.get(vid)
        if not item_id:
            continue
        print(f"    - rimuovo {vid}" + (" (dry-run)" if dry_run else ""))
        if not dry_run:
            yt.playlistItems().delete(id=item_id).execute()
        removed += 1
    return removed


def sync_playlist(yt, title, desc, video_ids, dry_run):
    pid = find_playlist(yt, title)
    just_created = False
    if not pid:
        print(f"  + creo playlist '{title}'")
        if not dry_run:
            body = {"snippet": {"title": title, "description": desc},
                     "status": {"privacyStatus": "public"}}
            pid = yt.playlists().insert(part="snippet,status", body=body).execute()["id"]
            just_created = True
            import time as _time
            _time.sleep(5)  # propagazione API prima di poterci inserire video
        else:
            pid = "DRY-RUN-ID"
    else:
        print(f"  = playlist '{title}' gia' esiste ({pid})")

    # una playlist appena creata e' per definizione vuota - interrogarla subito rischia un
    # 404 "playlistNotFound" per propagazione non ancora completata lato API (stessa
    # latenza gia' vista con videos().update, vedi vault/playbook/decisioni-coach.md 21/08)
    if just_created or pid == "DRY-RUN-ID":
        have = {}
    else:
        have = existing_items(yt, pid)
    added = 0
    for vid in video_ids:
        if vid in have:
            continue
        print(f"    + aggiungo {vid}" + (" (dry-run)" if dry_run else ""))
        if not dry_run:
            yt.playlistItems().insert(part="snippet", body={
                "snippet": {"playlistId": pid, "resourceId": {"kind": "youtube#video", "videoId": vid}}
            }).execute()
        added += 1
    print(f"    -> {added} video aggiunti, {len(video_ids) - added} gia' presenti")
    return pid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    yt_uploads = load(YT_UPLOADS, {})
    yt = get_service()

    golai_ids = gol_impossibili_ids(yt_uploads)
    titles = fetch_titles(yt, golai_ids)
    # esclude item gol-ai senza numero nel titolo live (es. montage non numerati come "Prima di
    # diventare Calciovich"), tiene i primi 3 storici (mai rinumerati nel titolo, restano #1-3
    # solo "sulla carta" — vedi vault/playbook/decisioni-coach.md) nell'ordine di pubblicazione.
    UNNUMBERED_BUT_KEEP_ORDER = {"znfnztbM050", "JKh7P5mr7zo", "aCqd_Dy8f6g"}

    def golai_sort_key(vid):
        t = titles.get(vid, "")
        m = re.search(r"#(\d+)", t)
        if m:
            return int(m.group(1))
        if vid in UNNUMBERED_BUT_KEEP_ORDER:
            return {"znfnztbM050": 1, "JKh7P5mr7zo": 2, "aCqd_Dy8f6g": 3}[vid]
        return None  # niente numero -> escluso (es. montage non facente parte della serie)

    golai_ordered = sorted([v for v in golai_ids if golai_sort_key(v) is not None],
                            key=golai_sort_key)
    excluded = [v for v in golai_ids if golai_sort_key(v) is None]
    if excluded:
        print("Esclusi dalla playlist Gol Impossibili (nessun # nel titolo, non fa parte della serie numerata):")
        for v in excluded:
            print(f"   {v} — {titles.get(v)}")

    story_ids = story_chapter_ids()

    libro_only = libro_pn_complete()
    longform = longform_ids(yt_uploads, libro_only)

    print(f"\nGol Impossibili: {len(golai_ordered)} video in ordine")
    print(f"La storia in ordine: {len(story_ids)} video in ordine")
    if libro_only:
        print(f"La storia in formato esteso: libro-pN COMPLETA -> solo libro-pN, "
              f"{len(longform)} video (ep1-10 in uscita dalla playlist)\n")
    else:
        print(f"La storia in formato esteso: {len(longform)} video in ordine "
              f"(libro-pN non ancora completa -> ep1-10 + libro-pN insieme)\n")

    print("== Playlist 'Gol Impossibili' ==")
    pid1 = sync_playlist(yt, GOLAI_TITLE, GOLAI_DESC, golai_ordered, args.dry_run)
    print("\n== Playlist 'La storia in ordine' ==")
    pid2 = sync_playlist(yt, STORY_TITLE, STORY_DESC, story_ids, args.dry_run)
    print("\n== Playlist 'La storia in formato esteso' ==")
    pid3 = sync_playlist(yt, LONGFORM_TITLE, LONGFORM_DESC, longform, args.dry_run)
    if libro_only and pid3 != "DRY-RUN-ID":
        # la serie libro-pN ha raggiunto l'ultimo capitolo: ep1-10 (formato precedente,
        # gia' uno spoiler completo dall'inizio alla fine) esce dalla playlist, sostituita
        # dalla narrazione ufficiale piu' recente - vedi diario-di-bordo.md 02/09.
        ep_ids = [v["videoId"] for k, v in yt_uploads.items()
                  if re.match(r"^ep\d+-", k) and v.get("videoId")]
        n_removed = remove_from_playlist(yt, pid3, ep_ids, args.dry_run)
        if n_removed:
            print(f"    -> {n_removed} video di ep1-10 rimossi (libro-pN ora copre tutto il libro)")

    if not args.dry_run:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        json.dump({"gol_impossibili_playlist_id": pid1, "story_playlist_id": pid2,
                   "longform_playlist_id": pid3,
                   "updatedAt": __import__("time").strftime("%Y-%m-%dT%H:%M:%S")},
                  open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n✅ Stato salvato in {STATE_PATH}")
    else:
        print("\nDRY RUN: nessuna modifica effettuata.")


if __name__ == "__main__":
    main()
