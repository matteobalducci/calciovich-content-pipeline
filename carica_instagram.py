#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
carica_instagram.py — pubblica i video montati su Instagram (@calciovich.official)
come Reels, via Meta Graph API (nessuna browser automation, nessun rischio ban).

Idea: i video stanno in output/ e i metadati (didascalia, hashtag, data di
calendario) sono in app/data.json, esattamente come per carica_youtube.py.
Solo gli item con "Instagram" tra le piattaforme vengono considerati.

Flusso di pubblicazione (Instagram Graph API, Instagram Login):
  1) il video viene caricato su Cloudflare R2 (bucket pubblico r2.dev) perché
     l'API Instagram scarica il media da un URL pubblico, non accetta file locali;
  2) POST /{ig-user-id}/media crea un container (media_type=REELS, video_url, caption);
  3) si aspetta che il container finisca l'elaborazione (poll su status_code);
  4) POST /{ig-user-id}/media_publish pubblica il container;
  5) il file temporaneo su R2 viene rimosso (a meno di --keep-r2).

Credenziali: tutte in meta_config.json (gitignored) — sezioni "r2" (Access Key ID,
Secret Access Key, endpoint S3, bucket, URL pubblico) e "meta" (access_token
long-lived, instagram_business_account_id). Vedi GUIDA-INSTAGRAM-PUBLISHER.md
per come ottenerle.

Richiede: pip3 install boto3

USO TIPICO
  # 1) prova a vuoto (NESSUna pubblicazione, NESSUN upload su R2):
  python3 carica_instagram.py --dry-run
  # 2) pubblica per davvero i primi pronti (default 5 per esecuzione):
  python3 carica_instagram.py --all
  # 3) solo alcuni, a mano:
  python3 carica_instagram.py --only short01 short02
  # 4) tieni il file su R2 dopo la pubblicazione (debug):
  python3 carica_instagram.py --all --keep-r2

OPZIONI principali: --limit N (default 5) · --force (ripubblica anche se già
  nel registro) · --config PATH (default meta_config.json) · --keep-r2
"""
import os, sys, re, json, time, argparse, mimetypes, fcntl, contextlib
import urllib.request, urllib.parse, urllib.error

import upload_registry  # stato di pubblicazione condiviso (SQLite)
from rotation_policy import RotationPolicy
from publish_attempt import AlreadySettled, PublishAttempt, classify

HERE = os.path.dirname(os.path.abspath(__file__))                 # .../08-video-engine
DATA = os.path.join(HERE, "app", "data.json")
OUTPUT = os.path.join(HERE, "output")
UPLOADS = os.path.join(OUTPUT, "instagram-uploads.json")          # registro: cosa è già pubblicato
GRAPH = "https://graph.instagram.com/v21.0"

# ---------------------------------------------------------------- dati & piano
def ready_items():
    """Tutti gli item con video pronto e Instagram tra le piattaforme."""
    if not os.path.exists(DATA):
        sys.exit(f"Manca {DATA}. Esegui prima: python3 app/genera_app.py")
    d = json.load(open(DATA, encoding="utf-8"))
    items = [it for w in d["weeks"] for it in w["items"]
             if it.get("file") and "Instagram" in (it.get("piattaforme") or [])]
    return items

def file_key(fileurl):
    return os.path.splitext(os.path.basename(fileurl))[0]

def short_id(fileurl):
    base = os.path.basename(fileurl)
    return base.split("-")[0]

def abs_path(fileurl):
    return os.path.join(HERE, fileurl.lstrip("/"))

def chapter_number(key):
    """Numero di capitolo per il badge 'dove siamo nella storia' (short02 -> 2).
    Stessa fonte numerica del badge '#N' già bruciato nel video (COME-PRODURRE.md)."""
    m = re.match(r"short0*(\d+)", key)
    return int(m.group(1)) if m else None

def build_caption(it, key):
    """Didascalia Instagram standardizzata: hook specifico del video (prima
    riga di 'desc'), numero di capitolo (progressione storia), CTA verso
    YouTube (obiettivo primario: views/iscritti lì, non il libro) e hashtag
    adattati a Instagram (via '#Shorts', specifico di YouTube, con '#Reels')."""
    desc = (it.get("desc") or "").strip()
    hook = desc.split("\n\n")[0].strip() if desc else ""
    categoria = it.get("categoria") or ""
    # BUGFIX 01/09: "Re-cut da long-form" non e' un capitolo numerato del libro (il numero
    # del file short31.. e' solo un indice sequenziale di produzione) - il badge "Capitolo N"
    # ci finiva comunque sopra perche' non iniziava per "Personaggio". Trovato quando il primo
    # recut da libro-p2 e' uscito con "Capitolo 31" (il libro non arriva a 31 capitoli).
    is_canonico = not categoria.startswith("Personaggio") and categoria != "Re-cut da long-form"
    num = chapter_number(key) if is_canonico else None

    body_lines = []
    if num is not None:
        body_lines.append(f"📍 Capitolo {num}")
    body_lines.append("📖 La mia storia vera è un libro — link in bio")
    body_lines.append("🔔 Ogni settimana un nuovo capitolo su YouTube")

    hashtag = (it.get("hashtag") or "").strip()
    hashtag = re.sub(r"#Shorts\b", "#Reels", hashtag, flags=re.IGNORECASE)

    blocks = [b for b in (hook, "\n".join(body_lines), hashtag) if b]
    return "\n\n".join(blocks)[:2200]

@contextlib.contextmanager
def publish_lock():
    """BUGFIX 01/08: previene duplicati da esecuzioni concorrenti (vedi carica_youtube.py)."""
    os.makedirs(OUTPUT, exist_ok=True)
    lock_path = os.path.join(OUTPUT, ".carica_instagram.lock")
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        sys.exit("⚠️  Un'altra esecuzione di carica_instagram.py è già in corso "
                 "(lock occupato) — riprova tra poco per evitare doppie pubblicazioni.")
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()

def load_uploads():
    """Registro in sola lettura.

    BUGFIX 02/09: prima `except Exception: return {}` trasformava un registro
    troncato o illeggibile in "non e' mai stato pubblicato niente", e la run
    successiva ripubblicava l'intero catalogo. Ora un registro corrotto e' un
    errore fatale.
    """
    return upload_registry.load(UPLOADS)


def save_uploads(u):
    """Scrittura ATOMICA del registro (temp file + fsync + os.replace).

    BUGFIX 02/09: era un json.dump diretto su open(..., "w"), che tronca il file
    prima di scrivere: un crash a meta' lasciava un registro corrotto, che con il
    vecchio load_uploads diventava un dict vuoto e faceva ripubblicare tutto.
    """
    upload_registry.save(UPLOADS, u)


def reconcile_pending(ig_user_id, access_token, registry):
    """Risolve i pending lasciati da una run morta a meta'.

    BUGFIX 02/09 (audit Codex): la prima versione scriveva 'pending' senza
    prevedere alcun recovery, quindi un timeout bloccava l'item PER SEMPRE —
    un blocco permanente, peggio del problema che si voleva risolvere.

    Instagram non permette di risalire dal container al media pubblicato, quindi
    si guarda l'elenco dei media recenti e si cerca la didascalia. Un match
    ambiguo (stessa didascalia su piu' media) NON conferma niente: meglio restare
    bloccati che associare il media sbagliato.
    """
    pending = registry.pending()
    if not pending:
        return
    print(f"🔎 {len(pending)} pubblicazioni in sospeso da una run precedente: verifico su Instagram…")

    recenti = {}
    try:
        resp = graph_request(f"{ig_user_id}/media",
                             {"fields": "id,caption", "limit": 50,
                              "access_token": access_token}, method="GET")
        for m in resp.get("data", []):
            cap = (m.get("caption") or "").strip()[:200]
            if cap:
                recenti.setdefault(cap, []).append(m["id"])
    except Exception as e:
        print(f"   ⚠️  impossibile leggere i media recenti ({e}); i pending restano bloccati.")
        return

    def probe(key, record):
        if not record.get("containerId"):
            # Morto prima di creare il container: nessun effetto remoto.
            return None
        cap = (record.get("caption") or "").strip()[:200]
        if not cap:
            raise RuntimeError("record senza didascalia: non verificabile")
        candidati = recenti.get(cap, [])
        if len(candidati) > 1:
            raise RuntimeError(f"{len(candidati)} media con la stessa didascalia: risolvere a mano")
        return candidati[0] if candidati else None

    for key, outcome in registry.reconcile(probe):
        print(f"   • {key}: {outcome}")


def build_plan(args):
    uploaded = load_uploads()
    # BUGFIX 01/08: dedup solo per filename non riconosceva un item gia' pubblicato
    # se il file cambiava nome (v1->v2->v3): si controlla anche il source_id.
    # BUGFIX 02/09: si considerava "gia' fatto" qualunque chiave nel registro,
    # compresi i record 'failed' (da ritentare). is_settled() blocca su confirmed
    # e su pending, perche' un pending e' un esito IGNOTO, non un successo.
    settled = {k for k, v in uploaded.items() if upload_registry.is_settled(v)}
    published_ids = {v.get("source_id") for v in uploaded.values()
                     if upload_registry.is_settled(v) and v.get("source_id")}
    # BUGFIX 03/09: la pausa editoriale delle clip AI viveva solo in
    # rotation-state.json e nel registro decisioni del coach, letti dalla
    # sessione che PIANIFICA. I publisher non li guardavano, quindi il giorno
    # dopo lo stop una clip Gol-AI e' uscita comunque.
    policy = RotationPolicy(OUTPUT)
    plan = []
    for it in ready_items():
        key = file_key(it["file"])
        item_id = it.get("id")
        motivo = policy.pause_reason(it.get("categoria"), it.get("file"))
        if motivo and not args.force:
            print(f"  ⏸  salto '{key}': formato in pausa — {motivo[:110]}")
            continue
        if key in settled and not args.force:
            continue
        if item_id and item_id in published_ids and not args.force:
            print(f"  \u26a0\ufe0f  salto '{key}': l'item '{item_id}' \u00e8 gi\u00e0 stato pubblicato "
                  f"sotto un altro nome file (usa --force per ripubblicare comunque)")
            continue
        if args.only and short_id(it["file"]) not in args.only and key not in args.only:
            continue
        path = abs_path(it["file"])
        if not os.path.exists(path):
            print(f"  \u26a0\ufe0f  file mancante, salto: {path}"); continue
        caption = build_caption(it, key)
        plan.append({
            "key": key, "path": path, "caption": caption, "data": it["data"], "source_id": item_id,
        })
    plan.sort(key=lambda p: p["data"])
    if args.limit and args.limit > 0:
        plan = plan[:args.limit]
    return plan


def load_config(args):
    if not os.path.exists(args.config):
        sys.exit(f"Manca {args.config}. Vedi GUIDA-INSTAGRAM-PUBLISHER.md per generarlo.")
    cfg = json.load(open(args.config, encoding="utf-8"))
    for section, keys in (("r2", ("bucket", "s3_endpoint", "public_url_base",
                                   "access_key_id", "secret_access_key")),
                          ("meta", ("access_token", "instagram_business_account_id"))):
        if section not in cfg:
            sys.exit(f"Manca la sezione '{section}' in {args.config}.")
        for k in keys:
            if not cfg[section].get(k):
                sys.exit(f"Manca '{section}.{k}' in {args.config}.")
    return cfg

# ---------------------------------------------------------------- R2 (staging pubblico)
def r2_client(cfg):
    import boto3
    r2 = cfg["r2"]
    return boto3.client(
        "s3",
        endpoint_url=r2["s3_endpoint"],
        aws_access_key_id=r2["access_key_id"],
        aws_secret_access_key=r2["secret_access_key"],
        region_name="auto",
    )

def upload_to_r2(cfg, path, key):
    s3 = r2_client(cfg)
    r2 = cfg["r2"]
    content_type = mimetypes.guess_type(path)[0] or "video/mp4"
    s3.upload_file(path, r2["bucket"], key, ExtraArgs={"ContentType": content_type})
    return f"{r2['public_url_base'].rstrip('/')}/{key}"

def delete_from_r2(cfg, key):
    s3 = r2_client(cfg)
    s3.delete_object(Bucket=cfg["r2"]["bucket"], Key=key)

# ---------------------------------------------------------------- Instagram Graph API
def graph_request(path, params, method="POST"):
    url = f"{GRAPH}/{path}"
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        req = urllib.request.Request(url + "?" + data.decode())
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}")

def create_container(ig_user_id, video_url, caption, access_token):
    resp = graph_request(f"{ig_user_id}/media", {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "access_token": access_token,
    })
    return resp["id"]

def wait_container_ready(container_id, access_token, timeout=300, interval=5):
    """Aspetta che Instagram finisca di scaricare/elaborare il video."""
    waited = 0
    while waited < timeout:
        resp = graph_request(f"{container_id}", {
            "fields": "status_code,status",
            "access_token": access_token,
        }, method="GET")
        status = resp.get("status_code")
        if status == "FINISHED":
            return True
        if status == "ERROR":
            raise RuntimeError(f"Instagram ha rifiutato il video: {resp.get('status')}")
        time.sleep(interval)
        waited += interval
    raise RuntimeError("Timeout: il container non è FINISHED dopo 5 minuti.")

def publish_container(ig_user_id, creation_id, access_token):
    resp = graph_request(f"{ig_user_id}/media_publish", {
        "creation_id": creation_id,
        "access_token": access_token,
    })
    return resp["id"]

# ---------------------------------------------------------------- foto singola
def publish_photo(args):
    """Pubblica una FOTO nel feed (fuori da app/data.json: le foto sono
    contenuti IG-only del pillar personaggio, non passano dal calendario video)."""
    cfg = load_config(args)
    ig_user_id = cfg["meta"]["instagram_business_account_id"]
    access_token = cfg["meta"]["access_token"]
    path = args.photo
    if not os.path.exists(path):
        sys.exit(f"Foto non trovata: {path}")
    key = f"photos/{os.path.splitext(os.path.basename(path))[0]}.jpg"

    # BUGFIX 02/09 (audit Codex, secondo giro): la prima correzione usava
    # begin()+confirm() a mano, senza salvare il containerId — quindi un pending
    # non era riconciliabile e la riconciliazione lo cancellava come "mai
    # iniziato", permettendo di ripubblicare la foto. Ora usa lo STESSO
    # protocollo dei video, context manager compreso.
    registry = upload_registry.Registry(UPLOADS)
    if registry.claim(key, source_id=None, type="photo",
                      caption=(args.caption or "")[:200]) is None:
        print(f"  ⚠️  '{key}' risulta gia' gestita: salto (cancella il record per forzare).")
        return 0

    with PublishAttempt(registry, key, type="photo",
                        caption=(args.caption or "")[:200]) as attempt:
        try:
            print("    …upload su R2")
            image_url = upload_to_r2(cfg, path, key)
            print("    …creo container foto")
            resp = graph_request(f"{ig_user_id}/media", {
                "image_url": image_url,
                "caption": args.caption or "",
                "access_token": access_token,
            })
            container_id = resp["id"]
            attempt.record(containerId=container_id)
            wait_container_ready(container_id, access_token, timeout=120)
            print("    …pubblico")
            media_id = publish_container(ig_user_id, container_id, access_token)
        except Exception as e:
            raise classify(e) from e
        finally:
            if not args.keep_r2:
                try: delete_from_r2(cfg, key)
                except Exception: pass
        attempt.succeeded(media_id, mediaId=media_id, type="photo",
                          publishedAt=time.strftime("%Y-%m-%dT%H:%M:%S"))
    print(f"  ✓ foto pubblicata: media id {media_id}")

# ---------------------------------------------------------------- story
def publish_story(args):
    """Pubblica una STORY (foto o video): rilancio quotidiano del contenuto
    del giorno, costo zero, tocca chi non vede il feed."""
    cfg = load_config(args)
    ig_user_id = cfg["meta"]["instagram_business_account_id"]
    access_token = cfg["meta"]["access_token"]
    path = args.story
    if not os.path.exists(path):
        sys.exit(f"File non trovato: {path}")
    is_video = path.lower().endswith((".mp4", ".mov"))
    # La chiave include la data: una story si ripubblica di proposito ogni
    # giorno, quindi la deduplica deve valere per GIORNATA, non per file.
    key = f"stories/{time.strftime('%Y-%m-%d')}/{os.path.basename(path)}"

    # BUGFIX 02/09: anche questo percorso scavalcava la macchina a stati — non
    # registrava nulla, quindi un crash dopo publish_container ripubblicava la
    # story. Trovato dal test strutturale, non a occhio.
    registry = upload_registry.Registry(UPLOADS)
    try:
        with PublishAttempt(registry, key, type="story", is_video=is_video) as attempt:
            try:
                print("    …upload su R2")
                media_url = upload_to_r2(cfg, path, key)
                print("    …creo container story")
                params = {"media_type": "STORIES", "access_token": access_token}
                params["video_url" if is_video else "image_url"] = media_url
                resp = graph_request(f"{ig_user_id}/media", params)
                container_id = resp["id"]
                attempt.record(containerId=container_id)
                wait_container_ready(container_id, access_token, timeout=300)
                print("    …pubblico")
                media_id = publish_container(ig_user_id, container_id, access_token)
            except Exception as e:
                raise classify(e) from e
            finally:
                if not args.keep_r2:
                    try: delete_from_r2(cfg, key)
                    except Exception: pass
            attempt.succeeded(media_id, mediaId=media_id, type="story",
                              publishedAt=time.strftime("%Y-%m-%dT%H:%M:%S"))
    except AlreadySettled:
        print(f"  ⏭  story gia' pubblicata oggi per '{os.path.basename(path)}', salto.")
        return 0
    print(f"  ✓ story pubblicata: media id {media_id}")

# ---------------------------------------------------------------- primo commento
def post_first_comment(args):
    """Aggiunge un commento (in voce, primo della discussione) a un media già pubblicato."""
    cfg = load_config(args)
    media_id, text = args.comment
    resp = graph_request(f"{media_id}/comments", {
        "message": text, "access_token": cfg["meta"]["access_token"]})
    print(f"  ✓ primo commento pubblicato (id {resp.get('id')})")

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Pubblica i video su Instagram come Reels (o --photo feed, --story, --comment).")
    ap.add_argument("--only", nargs="*", help="solo questi (es. short01 ep1)")
    ap.add_argument("--all", action="store_true", help="tutti i pronti non ancora pubblicati")
    ap.add_argument("--photo", help="pubblica UNA foto nel feed (percorso jpg/png), con --caption")
    ap.add_argument("--caption", help="didascalia per --photo")
    ap.add_argument("--story", help="pubblica una STORY (jpg/png/mp4)")
    ap.add_argument("--comment", nargs=2, metavar=("MEDIA_ID", "TESTO"), help="primo commento in voce su un media pubblicato")
    ap.add_argument("--dry-run", action="store_true", help="mostra il piano, non pubblica nulla")
    ap.add_argument("--limit", type=int, default=5, help="max pubblicazioni per esecuzione, default 5")
    ap.add_argument("--force", action="store_true", help="ripubblica anche se già nel registro")
    ap.add_argument("--keep-r2", action="store_true", help="non cancellare il file da R2 dopo la pubblicazione")
    ap.add_argument("--config", default=os.path.join(HERE, "meta_config.json"))
    args = ap.parse_args()

    if args.photo:
        publish_photo(args)
        return
    if args.story:
        publish_story(args)
        return
    if args.comment:
        post_first_comment(args)
        return

    if not (args.all or args.only):
        print("Specifica --all oppure --only NAME [NAME...] o --photo FILE  (aggiungi --dry-run per provare).")
        sys.exit(2)

    plan = build_plan(args)
    if not plan:
        print("Niente da pubblicare (tutto già pubblicato o nessun match)."); return

    print(f"\n📋 Piano di pubblicazione ({len(plan)} reel){' — DRY RUN' if args.dry_run else ''}:")
    for p in plan:
        size = os.path.getsize(p["path"]) / 1e6
        print(f"  • {p['key']}  [{size:.1f}MB]  data: {p['data']}")
        print(f"      caption: {p['caption'][:80]}...")
    print()

    if args.dry_run:
        print("DRY RUN: nessuna pubblicazione eseguita. Togli --dry-run per pubblicare davvero.")
        return

    cfg = load_config(args)
    ig_user_id = cfg["meta"]["instagram_business_account_id"]
    access_token = cfg["meta"]["access_token"]

    failures = []
    with publish_lock():
        plan = build_plan(args)
        if not plan:
            print("Niente da pubblicare (tutto già pubblicato o nessun match) — ricontrollato dopo il lock.")
            return 0
        registry = upload_registry.Registry(UPLOADS)
        reconcile_pending(ig_user_id, access_token, registry)
        plan = [q for q in plan if not registry.already_handled(q["key"], q.get("source_id"))]
        if not plan:
            print("Niente da pubblicare dopo la riconciliazione.")
            return 0
        for i, p in enumerate(plan, 1):
            print(f"⬆️  [{i}/{len(plan)}] {p['key']} …")
            r2_key = f"reels/{p['key']}.mp4"
            try:
                with PublishAttempt(registry, p["key"], source_id=p.get("source_id"),
                                    caption=p["caption"][:200]) as attempt:
                    try:
                        print("    …upload su R2")
                        video_url = upload_to_r2(cfg, p["path"], r2_key)
                        print("    …creo container Instagram")
                        container_id = create_container(ig_user_id, video_url, p["caption"], access_token)
                        # Persistito SUBITO: e' l'unico appiglio del recovery.
                        attempt.record(containerId=container_id)
                        print("    …attendo elaborazione")
                        wait_container_ready(container_id, access_token)
                        print("    …pubblico")
                        media_id = publish_container(ig_user_id, container_id, access_token)
                    except Exception as e:
                        raise classify(e) from e
                    finally:
                        if not args.keep_r2:
                            try: delete_from_r2(cfg, r2_key)
                            except Exception: pass
                    attempt.succeeded(media_id, mediaId=media_id,
                                      publishedAt=time.strftime("%Y-%m-%dT%H:%M:%S"))
            except AlreadySettled:
                print("  ⏭  gia' preso in carico da un'altra esecuzione, salto.")
                continue
            except Exception as e:
                msg = str(e)[:300]
                print(f"  ❌ errore: {msg}")
                failures.append((p["key"], msg))
                print("  ↪︎ lasciato in sospeso: la prossima esecuzione verifichera' su Instagram.")
                continue
            print(f"  ✓ pubblicato: media id {media_id}")
        if failures:
            print(f"\n⚠️  {len(failures)} pubblicazioni non riuscite:")
            for key, msg in failures:
                print(f"   • {key}: {msg}")
        else:
            print(f"\n✅ Fatto. Registro: {UPLOADS}")

    # BUGFIX 02/09: prima si usciva sempre con 0, anche se OGNI pubblicazione era
    # fallita, stampando "Fatto". Un orchestratore vedeva successo.
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
