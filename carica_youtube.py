#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
carica_youtube.py — carica i video montati su YouTube come PRIVATI con
pubblicazione PROGRAMMATA (publishAt), già completi di titolo/descrizione/tag.

Idea: i video stanno in output/ e i metadati (titolo YouTube, descrizione,
hashtag, data di calendario) sono in app/data.json. Lo script li carica privati
e imposta publishAt = data programmata all'ora scelta, così YouTube li rende
pubblici DA SOLO al momento giusto. Tu non devi fare altro.

Auth: OAuth 2.0 (l'upload NON si fa con la sola API key). Serve un file
client_secret OAuth (tipo "Desktop app") scaricato da Google Cloud. Il token
viene messo in cache in youtube_token.json dopo il primo consenso nel browser.

USO TIPICO
  # 1) prova a vuoto (NESSUN upload, NESSUNA credenziale richiesta):
  python3 carica_youtube.py --dry-run
  # 2) carica per davvero i primi (default 6/giorno per la quota), programmati:
  python3 carica_youtube.py --all
  # 3) solo alcuni, a mano:
  python3 carica_youtube.py --only short01 short02 ep1
  # 4) privati ma SENZA auto-pubblicazione (li pubblichi tu da Studio):
  python3 carica_youtube.py --all --no-schedule

OPZIONI principali: --time HH:MM (default 17:00, fuso Europe/Rome) ·
  --privacy private|unlisted|public (default private) · --category N (default 24)
  · --limit N (default 6) · --client PATH (default youtube_client_secret.json)
"""
import os, sys, json, argparse, glob, fcntl, contextlib
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Rome")
except Exception:
    TZ = None  # fallback: si usa l'ora locale di sistema

import upload_registry  # registro crash-safe condiviso fra i publisher

HERE = os.path.dirname(os.path.abspath(__file__))                 # .../08-video-engine
DATA = os.path.join(HERE, "app", "data.json")
OUTPUT = os.path.join(HERE, "output")

# Su YouTube i link nella descrizione SONO cliccabili (su Instagram no: lì
# "link in bio" e' corretto e va lasciato stare). Le descrizioni condivise fra
# le piattaforme dicevano solo "link in bio", quindi i video piu' visti non
# avevano alcun percorso d'acquisto: 93.000 view sui primi due video senza un
# link. Qui il link viene messo davvero, a ogni upload.
LIBRO_URL = "https://calciovich.gumroad.com/l/laverastoriadicalciovich"

def desc_con_link(desc):
    """Aggiunge il link al libro alla descrizione YouTube (idempotente)."""
    d = desc or ""
    if "gumroad.com" in d.lower():
        return d
    # se c'e' gia' una riga "... — link in bio", la si completa col link vero
    if "link in bio" in d:
        d = d.replace("link in bio", LIBRO_URL)
        return d
    return d.rstrip() + f"\n\n📖 La vera storia di Calciovich — il libro:\n{LIBRO_URL}"
UPLOADS = os.path.join(OUTPUT, "youtube-uploads.json")            # registro: cosa è già caricato
# Canale Calciovich (Brand Account) — vedi nota "guardia anti-canale-sbagliato"
# in get_service(): scoperto il 01/09 che il token OAuth, dopo il rescope del
# 21/08, risolveva "mine=True" sul canale PERSONALE dell'autore invece del
# Brand Account Calciovich (12 upload finiti li' tra il 26/08 e l'01/09).
CALCIOVICH_CHANNEL_ID = "UCLPBYAv19aizEYX4MmXV7rA"

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          # aggiunti 21/08 per sbloccare: fix titoli/privacy duplicati, commenti
          # sui video già pubblicati, dati reali di retention/CTR (yt-analytics)
          "https://www.googleapis.com/auth/youtube",
          "https://www.googleapis.com/auth/youtube.force-ssl",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]

# ---------------------------------------------------------------- dati & piano
def ready_items():
    """Tutti gli item con video pronto, dai dati dell'app."""
    if not os.path.exists(DATA):
        sys.exit(f"Manca {DATA}. Esegui prima: python3 app/genera_app.py")
    d = json.load(open(DATA, encoding="utf-8"))
    items = [it for w in d["weeks"] for it in w["items"] if it.get("file")]
    return items

def file_key(fileurl):
    """Chiave stabile = basename senza estensione (es. short01-...vert)."""
    return os.path.splitext(os.path.basename(fileurl))[0]

def short_id(fileurl):
    """Prefisso identificativo (short01, ep1) per --only."""
    base = os.path.basename(fileurl)
    return base.split("-")[0]

def abs_path(fileurl):
    return os.path.join(HERE, fileurl.lstrip("/"))

def tags_from_hashtags(hashtag):
    return [t.lstrip("#") for t in (hashtag or "").split() if t.startswith("#")][:15]

def publish_at_rfc3339(date_iso, hhmm):
    """date_iso='2026-06-29', hhmm='17:00' -> RFC3339 UTC ('...Z'). None se nel passato."""
    h, m = (int(x) for x in hhmm.split(":"))
    naive = datetime.fromisoformat(date_iso).replace(hour=h, minute=m)
    local = naive.replace(tzinfo=TZ) if TZ else naive.astimezone()
    if local <= datetime.now(local.tzinfo):
        return None  # publishAt deve essere nel futuro
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

@contextlib.contextmanager
def publish_lock():
    """BUGFIX 01/08: senza lock, due esecuzioni concorrenti di questo script
    possono leggere il registro PRIMA che l'altra abbia salvato il suo upload
    e caricare lo stesso video due volte (causa reale dei duplicati storici
    'Ep.3'/'La signora grigia', stesso filename ricaricato più volte)."""
    os.makedirs(OUTPUT, exist_ok=True)
    lock_path = os.path.join(OUTPUT, ".carica_youtube.lock")
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        sys.exit("⚠️  Un'altra esecuzione di carica_youtube.py è già in corso "
                 "(lock occupato) — riprova tra poco per evitare doppie pubblicazioni.")
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()

def load_uploads():
    """Registro in sola lettura, per chi deve solo consultarlo.

    BUGFIX 02/09: prima questa funzione faceva `except Exception: return {}`,
    quindi un registro troncato o illeggibile diventava silenziosamente un dict
    vuoto — cioè "non è mai stato pubblicato niente" — e la run successiva
    ricaricava l'intero catalogo. Ora un registro corrotto è un errore fatale.
    """
    return upload_registry.load(UPLOADS)


def reconcile_pending(youtube, registry):
    """Risolve gli upload rimasti 'pending' da una run morta a metà.

    Un record pending significa che avevamo dichiarato l'intenzione di caricare
    ma non abbiamo mai visto la conferma: l'esito è IGNOTO, non fallito. Qui lo
    chiediamo a YouTube, cercando fra gli ultimi caricamenti del canale un video
    con lo stesso titolo. Se lo troviamo il record diventa confirmed; se il
    canale non ce l'ha, il record si cancella e l'item torna caricabile.
    Se non riusciamo a chiedere (rete, quota) il record resta pending e l'item
    resta bloccato: ripubblicare due volte è peggio che ripubblicare tardi.
    """
    pending = registry.pending()
    if not pending:
        return
    print(f"🔎 {len(pending)} upload in sospeso da una run precedente: verifico su YouTube…")

    recent = {}
    ch = youtube.channels().list(part="contentDetails", mine=True).execute()
    uploads_pl = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    page = None
    while len(recent) < 200:
        resp = youtube.playlistItems().list(
            part="snippet", playlistId=uploads_pl, maxResults=50, pageToken=page
        ).execute()
        for it in resp.get("items", []):
            recent[it["snippet"]["title"].strip()] = it["snippet"]["resourceId"]["videoId"]
        page = resp.get("nextPageToken")
        if not page:
            break

    def probe(key, record):
        title = (record.get("title") or "").strip()
        if not title:
            raise RuntimeError("record senza titolo: non verificabile")
        return recent.get(title)

    for key, outcome in registry.reconcile(probe):
        print(f"   • {key}: {outcome}")

def build_plan(args, registry=None):
    uploaded = registry.data if registry is not None else load_uploads()
    # BUGFIX 01/08: il dedup era basato SOLO sul filename corrente. Quando un item
    # viene rigenerato (v1 -> v2 -> v3, stesso "id" logico in app/data.json ma file
    # diverso) lo script non riconosceva che era già stato caricato e lo ripubblicava
    # da capo (causa reale dei duplicati "Ep.3"/"La signora grigia" e di
    # coast-to-coast v2/v3 caricati entrambi lo stesso giorno). Ora si controlla
    # anche l'"id" stabile dell'item tramite "source_id" salvato nel registro.
    # BUGFIX 02/09: il dedup considerava "già fatto" qualunque chiave presente nel
    # registro, compresi i record 'failed' (che invece vanno ritentati). Ora si passa
    # da is_settled(), che blocca su confirmed e su pending — un pending è un esito
    # IGNOTO e va trattato come già pubblicato finché la riconciliazione non decide.
    settled = {k for k, v in uploaded.items() if upload_registry.is_settled(v)}
    published_ids = {v.get("source_id") for v in uploaded.values()
                     if upload_registry.is_settled(v) and v.get("source_id")}
    plan = []
    for it in ready_items():
        key = file_key(it["file"])
        item_id = it.get("id")
        if key in settled and not args.force:
            continue
        if item_id and item_id in published_ids and not args.force:
            print(f"  ⚠️  salto '{key}': l'item '{item_id}' è già stato pubblicato "
                  f"sotto un altro nome file (usa --force per ripubblicare comunque)")
            continue
        if args.only and short_id(it["file"]) not in args.only and key not in args.only:
            continue
        path = abs_path(it["file"])
        if not os.path.exists(path):
            print(f"  ⚠️  file mancante, salto: {path}"); continue
        title = (it.get("titolo_yt") or it.get("titolo") or key)[:100]
        desc = (it.get("desc") or "").strip()
        hashtag = it.get("hashtag") or ""
        full_desc = (desc + ("\n\n" + hashtag if hashtag else ""))[:4900]
        pub_at = None
        if not args.no_schedule:
            pub_at = publish_at_rfc3339(it["data"], args.time)
        plan.append({
            "key": key, "path": path, "title": title, "desc": full_desc,
            "tags": tags_from_hashtags(hashtag), "data": it["data"],
            "publishAt": pub_at, "formato": it.get("formato"), "source_id": item_id,
        })
    plan.sort(key=lambda p: p["data"])
    if args.limit and args.limit > 0:
        plan = plan[:args.limit]
    return plan

# ---------------------------------------------------------------- OAuth & upload
def get_service(args):
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    token_path = os.path.join(HERE, "youtube_token.json")
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        refreshed = False
        if creds and creds.expired and creds.refresh_token:
            from google.auth.exceptions import RefreshError
            try:
                creds.refresh(Request()); refreshed = True
            except RefreshError:
                # token "Testing" scaduto/revocato (~7 giorni): si ri-autorizza da capo
                print("  ⚠️  token scaduto/revocato — riapro il consenso nel browser…")
                creds = None
        if not refreshed:
            if not os.path.exists(args.client):
                sys.exit(f"Manca il file OAuth client: {args.client}\n"
                         f"Scaricalo da Google Cloud (Credenziali → ID client OAuth → Desktop)\n"
                         f"e salvalo come '{args.client}'.")
            flow = InstalledAppFlow.from_client_secrets_file(args.client, SCOPES)
            creds = flow.run_local_server(port=0)
        open(token_path, "w").write(creds.to_json())
        print(f"  ✓ token salvato in {os.path.basename(token_path)}")
    yt = build("youtube", "v3", credentials=creds)
    # Guardia anti-canale-sbagliato (aggiunta 01/09 dopo l'incidente dei 12 video
    # finiti sul canale personale dell'autore invece che su Calciovich): il token
    # OAuth di un account con piu' canali/Brand Account risolve "mine=True" sul
    # canale che era "attivo" nel browser al momento del consenso, non per forza
    # Calciovich. Verificare PRIMA di ogni upload, non dopo.
    mine = yt.channels().list(part="id,snippet", mine=True).execute().get("items", [])
    if not mine:
        # Successo reale il 01/09: un account Google senza NESSUN canale YouTube
        # (mine=True -> 0 risultati) non solleva errori API, sembra "andato bene".
        sys.exit(
            f"❌ STOP: l'account Google autenticato non ha NESSUN canale YouTube proprio "
            f"(mine=True -> 0 risultati). Nessun upload eseguito. Non è il canale sbagliato,\n"
            f"   è nessun canale: prova con un altro account Google (quello che possiede o "
            f"gestisce Calciovich, {CALCIOVICH_CHANNEL_ID}), cancellando prima "
            f"{os.path.basename(token_path)}."
        )
    who = mine[0]
    if who["id"] != CALCIOVICH_CHANNEL_ID:
        sys.exit(
            f"❌ STOP: il token OAuth punta al canale '{who['snippet']['title']}' "
            f"({who['id']}), NON Calciovich ({CALCIOVICH_CHANNEL_ID}).\n"
            f"   Nessun upload eseguito. Per correggere: cancella {os.path.basename(token_path)},\n"
            f"   su youtube.com/studio passa al canale 'Calciovich' (selettore account in alto a\n"
            f"   destra) PRIMA di autorizzare, poi rilancia questo script per rifare il consenso."
        )
    return yt

def upload_one(youtube, p, args):
    from googleapiclient.http import MediaFileUpload
    status = {"privacyStatus": ("private" if p["publishAt"] else args.privacy),
              "selfDeclaredMadeForKids": False}
    if p["publishAt"]:
        status["publishAt"] = p["publishAt"]
    # Le clip AI "finto-archivio" (output/ai-clips/) sono contenuto sintetico
    # realistico: la policy YouTube ne richiede la dichiarazione esplicita.
    # I canonici (motion-comic illustrato, dichiaratamente stilizzato) no.
    if "ai-clips" in p["path"]:
        status["containsSyntheticMedia"] = True
    body = {
        "snippet": {"title": p["title"], "description": desc_con_link(p["desc"]),
                    "tags": p["tags"], "categoryId": str(args.category)},
        "status": status,
    }
    media = MediaFileUpload(p["path"], chunksize=8*1024*1024, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        prog, resp = req.next_chunk()
        if prog:
            sys.stdout.write(f"\r    …{int(prog.progress()*100)}%"); sys.stdout.flush()
    sys.stdout.write("\r")
    return resp["id"]

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Carica i video su YouTube (privati + publishAt).")
    ap.add_argument("--only", nargs="*", help="solo questi (es. short01 ep1)")
    ap.add_argument("--all", action="store_true", help="tutti i pronti non ancora caricati")
    ap.add_argument("--dry-run", action="store_true", help="mostra il piano, non carica nulla")
    ap.add_argument("--no-schedule", action="store_true", help="privati senza publishAt (pubblichi tu)")
    ap.add_argument("--time", default="17:00", help="ora di pubblicazione (Europe/Rome), default 17:00")
    ap.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])
    ap.add_argument("--category", type=int, default=24, help="categoria YouTube (24=Entertainment)")
    ap.add_argument("--limit", type=int, default=6, help="max upload per esecuzione (quota), default 6")
    ap.add_argument("--force", action="store_true", help="ricarica anche se già nel registro")
    ap.add_argument("--client", default=os.path.join(HERE, "youtube_client_secret.json"))
    args = ap.parse_args()

    if not (args.all or args.only):
        print("Specifica --all oppure --only NAME [NAME...]  (aggiungi --dry-run per provare).")
        sys.exit(2)

    plan = build_plan(args)
    if not plan:
        print("Niente da caricare (tutto già caricato o nessun match)."); return

    print(f"\n📋 Piano di upload ({len(plan)} video){' — DRY RUN' if args.dry_run else ''}:")
    for p in plan:
        sched = f"programmato {p['publishAt']}" if p["publishAt"] else \
                ("NIENTE schedule (privato)" if args.no_schedule else
                 f"⚠️ data {p['data']} nel passato → privato senza schedule")
        size = os.path.getsize(p["path"]) / 1e6
        print(f"  • {p['key']}  [{p['formato']}, {size:.1f}MB]")
        print(f"      titolo: {p['title']}")
        print(f"      tag: {', '.join(p['tags'])}")
        print(f"      {sched}")
    print()

    if args.dry_run:
        print("DRY RUN: nessun upload eseguito. Togli --dry-run per caricare davvero.")
        return

    failures = []
    with publish_lock():
        youtube = get_service(args)
        registry = upload_registry.Registry(UPLOADS)

        # Prima di qualsiasi upload: risolvi ciò che una run precedente ha lasciato
        # a metà. Senza questo passaggio un pending resterebbe a bloccare l'item
        # per sempre.
        reconcile_pending(youtube, registry)

        # ricalcola il piano DENTRO il lock: se un'altra esecuzione ha appena
        # pubblicato qualcosa, questa vede il registro aggiornato e non duplica.
        plan = build_plan(args, registry)
        if not plan:
            print("Niente da caricare (tutto già caricato o nessun match) — ricontrollato dopo il lock.")
            return 0
        any_uploaded = False
        for i, p in enumerate(plan, 1):
            print(f"⬆️  [{i}/{len(plan)}] {p['key']} …")

            # SCRITTURA DELL'INTENZIONE, prima dell'effetto esterno. Se il processo
            # muore fra qui e la conferma, il record pending dice alla run successiva
            # che l'esito è ignoto e va verificato, invece di far ripartire l'upload.
            registry.begin(
                p["key"],
                source_id=p.get("source_id"),
                title=p["title"],
                publishAt=p["publishAt"],
                privacy=("private" if p["publishAt"] else args.privacy),
            )
            try:
                vid = upload_one(youtube, p, args)
            except Exception as e:
                msg = str(e)
                print(f"  ❌ errore: {msg[:200]}")
                failures.append((p["key"], msg[:200]))
                if "quota" in msg.lower():
                    # Quota esaurita: l'upload non è partito, quindi è un fallimento
                    # NOTO e l'item può tornare disponibile.
                    registry.fail(p["key"], msg)
                    print("  Quota giornaliera esaurita: riprova domani o richiedi aumento quota.")
                    break
                # Ogni altro errore può essere un timeout su un upload andato a buon
                # fine: si lascia pending di proposito, e sarà la riconciliazione a
                # decidere alla prossima run.
                print("  ↪︎ lasciato in sospeso: la prossima esecuzione verificherà su YouTube.")
                continue
            url = f"https://youtu.be/{vid}"
            registry.confirm(p["key"], vid, videoId=vid, url=url,
                             uploadedAt=datetime.now().isoformat())
            any_uploaded = True
            print(f"  ✓ caricato: {url}" + (f"  → pubblica il {p['publishAt']}" if p["publishAt"] else " (privato)"))

        if failures:
            print(f"\n⚠️  {len(failures)} upload non riusciti:")
            for key, msg in failures:
                print(f"   • {key}: {msg}")
        else:
            print(f"\n✅ Fatto. Registro: {UPLOADS}")

    # AGGIUNTO 02/09 (richiesta autore): ogni video nuovo va aggiunto in automatico alla
    # playlist giusta (Gol Impossibili / La storia in ordine / La storia in formato esteso),
    # cosi' le playlist restano sempre aggiunte senza un passo manuale a parte. Fuori dal lock
    # (gestisci_playlist.py ha la sua stessa logica idempotente, non serve serializzarlo) e
    # non bloccante: se fallisce, l'upload e' comunque andato a buon fine, si segnala e basta.
    if any_uploaded:
        try:
            import subprocess
            subprocess.run([sys.executable, os.path.join(HERE, "gestisci_playlist.py")], check=False)
        except Exception as e:
            print(f"  ⚠️  aggiornamento playlist fallito ({e}) — rilancia a mano: python3 gestisci_playlist.py")

    # BUGFIX 02/09: prima lo script usciva sempre con 0, anche se OGNI upload era
    # fallito, stampando "✅ Fatto". Un orchestratore vedeva successo. Ora l'exit
    # code riflette l'esito reale.
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
