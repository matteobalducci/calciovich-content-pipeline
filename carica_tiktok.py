#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
carica_tiktok.py — pubblica i video montati su TikTok (@calciovich.official)
via Content Posting API (Direct Post, upload diretto del file, nessuna
browser automation).

IMPORTANTE — app in Sandbox, non ancora audit-ata da TikTok: finché l'audit
non è approvato, ogni post pubblicato via API è forzato a privacy_level
SELF_ONLY (visibile solo all'account che pubblica, NON pubblico). Vedi
meta_config.json → "tiktok_sandbox" → "note". Una volta approvato l'audit,
va aggiornato client_key/secret con quelli di Production e privacy_level va
cambiato a PUBLIC_TO_EVERYONE in publish_container().

Flusso di pubblicazione (Content Posting API, source FILE_UPLOAD):
  1) POST /v2/post/publish/video/init/ crea un container e restituisce un
     upload_url dedicato;
  2) PUT del file video direttamente su upload_url (nessun hosting esterno
     necessario, a differenza di Instagram);
  3) poll su /v2/post/publish/status/fetch/ finché non è PUBLISH_COMPLETE.

Credenziali in meta_config.json → sezione "tiktok_sandbox" (client_key,
client_secret, redirect_uri). L'access_token/refresh_token si ottengono con
--authorize (una tantum, poi si rinfrescano da soli). Vedi
GUIDA-INSTAGRAM-PUBLISHER.md per il pattern generale; per TikTok l'account
target dev è già autorizzato come Sandbox tester nel portale.

Richiede: nessuna libreria esterna oltre alla stdlib.

USO TIPICO
  # 1) una tantum: ottieni il token (apre il browser, poi incolli il codice)
  python3 carica_tiktok.py --authorize
  # 2) prova a vuoto (NESSUNA pubblicazione):
  python3 carica_tiktok.py --dry-run
  # 3) pubblica per davvero i primi pronti (default 5 per esecuzione):
  python3 carica_tiktok.py --all
  # 4) solo alcuni, a mano:
  python3 carica_tiktok.py --only short01 short02

OPZIONI principali: --limit N (default 5) · --force (ripubblica anche se già
  nel registro) · --config PATH (default meta_config.json)
"""
import os, sys, re, json, time, argparse, webbrowser, secrets, fcntl, contextlib
import urllib.request, urllib.parse, urllib.error

import upload_registry  # registro crash-safe condiviso fra i publisher

HERE = os.path.dirname(os.path.abspath(__file__))                 # .../08-video-engine
DATA = os.path.join(HERE, "app", "data.json")
OUTPUT = os.path.join(HERE, "output")
UPLOADS = os.path.join(OUTPUT, "tiktok-uploads.json")             # registro: cosa è già pubblicato
CONFIG_DEFAULT = os.path.join(HERE, "meta_config.json")
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API = "https://open.tiktokapis.com/v2"
SCOPES = "user.info.basic,video.publish,video.upload"

# ---------------------------------------------------------------- dati & piano
def ready_items():
    """Tutti gli item con video pronto e TikTok tra le piattaforme."""
    if not os.path.exists(DATA):
        sys.exit(f"Manca {DATA}. Esegui prima: python3 app/genera_app.py")
    d = json.load(open(DATA, encoding="utf-8"))
    items = [it for w in d["weeks"] for it in w["items"]
             if it.get("file") and "TikTok" in (it.get("piattaforme") or [])]
    return items

def file_key(fileurl):
    return os.path.splitext(os.path.basename(fileurl))[0]

def short_id(fileurl):
    base = os.path.basename(fileurl)
    return base.split("-")[0]

def abs_path(fileurl):
    return os.path.join(HERE, fileurl.lstrip("/"))

def chapter_number(key):
    """Stesso numero del badge visivo già bruciato nel video (COME-PRODURRE.md)."""
    m = re.match(r"short0*(\d+)", key)
    return int(m.group(1)) if m else None

def build_caption(it, key):
    """Didascalia TikTok: hook specifico + numero capitolo + CTA verso YouTube
    (obiettivo primario: monetizzare le view lì) + hashtag originali."""
    desc = (it.get("desc") or "").strip()
    hook = desc.split("\n\n")[0].strip() if desc else ""
    categoria = it.get("categoria") or ""
    # BUGFIX 01/09: vedi stessa nota in carica_instagram.py - "Re-cut da long-form" non e'
    # un capitolo numerato del libro, il numero nel filename e' solo un indice di produzione.
    is_canonico = not categoria.startswith("Personaggio") and categoria != "Re-cut da long-form"
    num = chapter_number(key) if is_canonico else None

    body_lines = []
    if num is not None:
        body_lines.append(f"📍 Capitolo {num}")
    body_lines.append("📖 La mia storia vera è un libro — link in bio")
    body_lines.append("🔔 Ogni settimana un nuovo capitolo su YouTube")

    hashtag = (it.get("hashtag") or "").strip()

    blocks = [b for b in (hook, "\n".join(body_lines), hashtag) if b]
    return "\n\n".join(blocks)[:2200]

@contextlib.contextmanager
def publish_lock():
    """BUGFIX 01/08: previene duplicati da esecuzioni concorrenti (vedi carica_youtube.py)."""
    os.makedirs(OUTPUT, exist_ok=True)
    lock_path = os.path.join(OUTPUT, ".carica_tiktok.lock")
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        sys.exit("⚠️  Un'altra esecuzione di carica_tiktok.py è già in corso "
                 "(lock occupato) — riprova tra poco per evitare doppie pubblicazioni.")
    try:
        yield
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()

def load_uploads():
    """Registro in sola lettura.

    BUGFIX 02/09: `except Exception: return {}` trasformava un registro corrotto
    in "non e' mai stato pubblicato niente", e la run dopo ripubblicava tutto.
    Ora un registro illeggibile e' un errore fatale.
    """
    return upload_registry.load(UPLOADS)

def save_uploads(u):
    """Scrittura ATOMICA (temp file + fsync + os.replace) invece di troncare."""
    upload_registry.save(UPLOADS, u)

def build_plan(args):
    uploaded = load_uploads()
    # BUGFIX 01/08: stesso bug di carica_youtube.py — dedup solo per filename,
    # non riconosce un item già pubblicato se il file cambia nome (v1->v2->v3).
    # BUGFIX 02/09: is_settled() esclude i record 'failed' (da ritentare) e
    # include i 'pending' (esito ignoto: vanno trattati come gia' pubblicati).
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
        caption = build_caption(it, key)
        plan.append({
            "key": key, "path": path, "caption": caption, "data": it["data"], "source_id": item_id,
        })
    plan.sort(key=lambda p: p["data"])
    if args.limit and args.limit > 0:
        plan = plan[:args.limit]
    return plan

# ---------------------------------------------------------------- config
def load_config(path):
    if not os.path.exists(path):
        sys.exit(f"Manca {path}.")
    cfg = json.load(open(path, encoding="utf-8"))
    if "tiktok_sandbox" not in cfg:
        sys.exit(f"Manca la sezione 'tiktok_sandbox' in {path}.")
    return cfg

def save_config(path, cfg):
    json.dump(cfg, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

# ---------------------------------------------------------------- OAuth
def print_authorize_url(args):
    cfg = load_config(args.config)
    tk = cfg["tiktok_sandbox"]
    state = secrets.token_urlsafe(16)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_key": tk["client_key"],
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": tk["redirect_uri"],
        "state": state,
    })
    print("Apri questo URL nel browser, autorizza, poi copia il codice mostrato")
    print("dalla pagina di callback ed esegui:")
    print("  python3 carica_tiktok.py --code IL_CODICE\n")
    print(url)
    try:
        webbrowser.open(url)
    except Exception:
        pass

def exchange_code(args, code):
    cfg = load_config(args.config)
    tk = cfg["tiktok_sandbox"]
    data = urllib.parse.urlencode({
        "client_key": tk["client_key"],
        "client_secret": tk["client_secret"],
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": tk["redirect_uri"],
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req) as resp:
            tok = json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"Errore scambio codice: HTTP {e.code}: {e.read().decode()[:500]}")

    if "access_token" not in tok:
        sys.exit(f"Risposta inattesa: {tok}")

    tk["access_token"] = tok["access_token"]
    tk["refresh_token"] = tok.get("refresh_token")
    tk["open_id"] = tok.get("open_id")
    save_config(args.config, cfg)
    print(f"✓ Autorizzato come open_id={tok.get('open_id')}. Token salvato in {args.config}.")

def refresh_access_token(cfg, path):
    tk = cfg["tiktok_sandbox"]
    if not tk.get("refresh_token"):
        sys.exit("Manca refresh_token. Esegui prima: python3 carica_tiktok.py --authorize")
    data = urllib.parse.urlencode({
        "client_key": tk["client_key"],
        "client_secret": tk["client_secret"],
        "grant_type": "refresh_token",
        "refresh_token": tk["refresh_token"],
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req) as resp:
        tok = json.load(resp)
    tk["access_token"] = tok["access_token"]
    tk["refresh_token"] = tok.get("refresh_token", tk["refresh_token"])
    save_config(path, cfg)
    return tk["access_token"]

# ---------------------------------------------------------------- Content Posting API
def api_post(path, access_token, body):
    req = urllib.request.Request(f"{API}/{path}",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {access_token}",
                 "Content-Type": "application/json; charset=UTF-8"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:500]}")

def init_upload(access_token, caption, video_size, draft=False):
    """draft=True: 'Upload video for user to complete' (endpoint /inbox/video/init/) —
    carica il file nell'inbox TikTok dell'account senza pubblicarlo; l'autore lo apre
    nell'app, aggiunge/conferma didascalia e privacy e pubblica lui stesso. Non richiede
    l'audit dell'app né forza SELF_ONLY (quel vincolo si applica solo al Direct Post).
    draft=False: comportamento storico, Direct Post immediato (richiede audit approvato
    per uscire da SELF_ONLY — vedi note in testa al file)."""
    if draft:
        resp = api_post("post/publish/inbox/video/init/", access_token, {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": video_size,
                "total_chunk_count": 1,
            },
        })
    else:
        resp = api_post("post/publish/video/init/", access_token, {
            "post_info": {
                "title": caption,
                "privacy_level": "SELF_ONLY",  # forzato da TikTok finché l'app non passa l'audit
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": video_size,
                "total_chunk_count": 1,
            },
        })
    d = resp.get("data", {})
    if not d.get("publish_id") or not d.get("upload_url"):
        raise RuntimeError(f"Risposta init inattesa: {resp}")
    return d["publish_id"], d["upload_url"]

def upload_video(upload_url, path, video_size):
    with open(path, "rb") as f:
        video_bytes = f.read()
    req = urllib.request.Request(upload_url, data=video_bytes, method="PUT",
        headers={"Content-Type": "video/mp4",
                 "Content-Range": f"bytes 0-{video_size - 1}/{video_size}"})
    with urllib.request.urlopen(req) as resp:
        resp.read()

def wait_publish_complete(access_token, publish_id, timeout=300, interval=5, draft=False):
    ok_status = "SEND_TO_USER_INBOX" if draft else "PUBLISH_COMPLETE"
    waited = 0
    while waited < timeout:
        resp = api_post("post/publish/status/fetch/", access_token, {"publish_id": publish_id})
        status = resp.get("data", {}).get("status")
        if status == ok_status:
            return
        if status == "FAILED":
            raise RuntimeError(f"Pubblicazione fallita: {resp}")
        time.sleep(interval)
        waited += interval
    raise RuntimeError(f"Timeout: non {ok_status} dopo 5 minuti.")

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Pubblica i video su TikTok via Content Posting API.")
    ap.add_argument("--authorize", action="store_true", help="passo 1: stampa/apre l'URL di autorizzazione OAuth")
    ap.add_argument("--code", help="passo 2: scambia il codice ottenuto dalla pagina di callback per il token")
    ap.add_argument("--only", nargs="*", help="solo questi (es. short01 ep1)")
    ap.add_argument("--all", action="store_true", help="tutti i pronti non ancora pubblicati")
    ap.add_argument("--dry-run", action="store_true", help="mostra il piano, non pubblica nulla")
    ap.add_argument("--limit", type=int, default=5, help="max pubblicazioni per esecuzione, default 5")
    ap.add_argument("--force", action="store_true", help="ripubblica anche se già nel registro")
    ap.add_argument("--draft", action="store_true", default=True,
                     help="carica come bozza nell'inbox TikTok (l'autore pubblica lui stesso dall'app) — default, evita il blocco account-privato del Direct Post")
    ap.add_argument("--direct-post", dest="draft", action="store_false",
                     help="pubblica direttamente via API (forzato SELF_ONLY finché l'app non è audit-ata)")
    ap.add_argument("--config", default=CONFIG_DEFAULT)
    args = ap.parse_args()

    if args.authorize:
        print_authorize_url(args)
        return

    if args.code:
        exchange_code(args, args.code)
        return

    if not (args.all or args.only):
        print("Specifica --all oppure --only NAME [NAME...]  (aggiungi --dry-run per provare).")
        print("Oppure --authorize se non hai ancora un token.")
        sys.exit(2)

    plan = build_plan(args)
    if not plan:
        print("Niente da pubblicare (tutto già pubblicato o nessun match)."); return

    modo = "BOZZA nell'inbox TikTok (pubblichi tu dall'app)" if args.draft else "Direct Post — privacy: SELF_ONLY (Sandbox, non audit-ata)"
    print(f"\n📋 Piano di pubblicazione ({len(plan)} video){' — DRY RUN' if args.dry_run else ''} — {modo}:")
    for p in plan:
        size = os.path.getsize(p["path"]) / 1e6
        print(f"  • {p['key']}  [{size:.1f}MB]  data: {p['data']}")
        print(f"      caption: {p['caption'][:80]}...")
    print()

    if args.dry_run:
        print("DRY RUN: nessuna pubblicazione eseguita. Togli --dry-run per pubblicare davvero.")
        return

    cfg = load_config(args.config)
    tk = cfg["tiktok_sandbox"]
    if not tk.get("access_token"):
        sys.exit("Manca access_token. Esegui prima: python3 carica_tiktok.py --authorize")
    access_token = refresh_access_token(cfg, args.config)

    failures = []
    with publish_lock():
        plan = build_plan(args)
        if not plan:
            print("Niente da pubblicare (tutto già pubblicato o nessun match) — ricontrollato dopo il lock.")
            return 0
        registry = upload_registry.Registry(UPLOADS)
        for i, p in enumerate(plan, 1):
            print(f"⬆️  [{i}/{len(plan)}] {p['key']} …")
            # Intenzione scritta PRIMA dell'effetto esterno.
            registry.begin(p["key"], source_id=p.get("source_id"))
            try:
                video_size = os.path.getsize(p["path"])
                print("    …creo container")
                publish_id, upload_url = init_upload(access_token, p["caption"], video_size, draft=args.draft)
                print("    …carico il video")
                upload_video(upload_url, p["path"], video_size)
                print("    …attendo" + (" invio all'inbox" if args.draft else " pubblicazione"))
                wait_publish_complete(access_token, publish_id, draft=args.draft)
            except Exception as e:
                msg = str(e)[:300]
                print(f"  ❌ errore: {msg}")
                failures.append((p["key"], msg))
                # Resta pending: puo' essere un timeout su un invio riuscito.
                print("  ↪︎ lasciato in sospeso: verificare su TikTok prima di ritentare.")
                continue
            # BUGFIX 02/09: una bozza in inbox veniva registrata come pubblicata e
            # deduplicata per sempre, anche se l'utente non l'avrebbe mai
            # confermata dall'app. Il record ora distingue la CONSEGNA (avvenuta,
            # quindi non va rifatta) dalla PUBBLICAZIONE (che dipende dall'utente).
            registry.confirm(
                p["key"], publish_id,
                publishId=publish_id,
                deliveredAt=time.strftime("%Y-%m-%dT%H:%M:%S"),
                delivery="inbox_draft" if args.draft else "direct_post",
                user_published=False if args.draft else True,
                privacy="DRAFT_INBOX" if args.draft else "SELF_ONLY",
                caption_bozza=p["caption"] if args.draft else None,
            )
            if args.draft:
                print(f"  ✓ inviato come bozza all'inbox TikTok: publish_id {publish_id} — completa la pubblicazione dall'app")
            else:
                print(f"  ✓ pubblicato (SELF_ONLY): publish_id {publish_id}")
        if failures:
            print(f"\n⚠️  {len(failures)} invii non riusciti:")
            for key, msg in failures:
                print(f"   • {key}: {msg}")
        else:
            print(f"\n✅ Fatto. Registro: {UPLOADS}")
        n_draft = sum(1 for r in registry.data.values()
                      if r.get("delivery") == "inbox_draft" and not r.get("user_published"))
        if n_draft:
            print(f"\n📥 {n_draft} bozze in attesa di essere pubblicate a mano dall'app TikTok "
                  f"(consegnate, NON ancora pubbliche).")

    # BUGFIX 02/09: si usciva sempre con 0 anche se ogni invio era fallito.
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
