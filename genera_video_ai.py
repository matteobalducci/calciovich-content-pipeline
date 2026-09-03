#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genera_video_ai.py — genera clip video AI (Seedance 2, via PiAPI) con il volto
di Calciovich sempre coerente, usando come "ancora" le illustrazioni canoniche
del canale (character-ref/) invece di lasciare al modello mano libera sul viso.

Perché queste immagini: sono le stesse già usate per copertina libro, banner
canale e le tavole del romanzo (vedi memoria "libro-immagini-e-copertina"),
quindi zero rischio di un volto "diverso" rispetto a quello che il pubblico
già conosce.

Flusso:
  1) le immagini in character-ref/ vengono caricate una volta su R2 (pubbliche,
     permanenti — servono come URL da allegare a OGNI generazione, mode
     "omni_reference" di Seedance 2). Rifarlo solo se cambi/aggiungi immagini.
  2) ogni clip si genera con POST /task (model=seedance, task_type in base al
     tier scelto, mode=omni_reference, image_urls = riferimenti + prompt
     testuale della scena specifica);
  3) si fa polling su GET /task/{id} finché status=completed;
  4) si scarica il video risultante in output/ai-clips/.

Credenziali: sezione "piapi" in meta_config.json (gitignored) — vedi
GUIDA-VIDEO-AI.md per come ottenere la API key (account PiAPI + credito).

USO
  # 1) carica/aggiorna i riferimenti su R2 (una tantum, o quando cambi le immagini):
  python3 genera_video_ai.py --upload-refs

  # 2) stima il costo senza generare nulla:
  python3 genera_video_ai.py --prompt "..." --duration 6 --resolution 720p --dry-run

  # 3) genera per davvero:
  python3 genera_video_ai.py --prompt "Calciovich calcia una punizione a effetto
      impossibile, telecamera che segue la palla, stadio esultante" \\
      --duration 6 --resolution 720p --out gol-hero-01
"""
import os, sys, re, json, time, argparse, mimetypes
import urllib.request, urllib.error

from budget import (DEFAULT_MAX_ATTEMPTS_PER_ITEM, DEFAULT_MONTHLY_CAP_USD,
                    Budget, BudgetExceeded, TooManyAttempts)

HERE = os.path.dirname(os.path.abspath(__file__))
REFS_DIR = os.path.join(HERE, "character-ref")
REFS_MANIFEST = os.path.join(REFS_DIR, "manifest.json")
OUTPUT_DIR = os.path.join(HERE, "output", "ai-clips")
QUEUE_PATH = os.path.join(HERE, "output", "ai-content-queue.json")
API = "https://api.piapi.ai/api/v1"

# Segnaposto espansi automaticamente dalle clausole canoniche di ai-content-queue.json.
# AGGIUNTO 29/08: prima questo script NON sostituiva nulla — ogni sessione incollava le
# clausole a mano dentro --prompt, ed e' cosi' che i prompt sono passati da ~600 char
# (luglio, generazioni pulite) a ~3300 char di boilerplate difensivo (fine agosto, con
# rifiuti per copyright e QC falliti). Espandendo qui, il prompt scritto a mano resta
# SOLO l'azione del giorno e le clausole restano identiche a se stesse a ogni run.
PLACEHOLDERS = {
    "{BROADCAST}":  "broadcast_framing_clause",
    "{SCENE_LOCK}": "scene_lock_clause",
    "{GEAR}":       "gear_brand_clause",
}

def expand_placeholders(prompt, era=None):
    """Sostituisce {KIT}/{GEAR}/{BROADCAST}/{SCENE_LOCK} con le clausole canoniche."""
    try:
        q = json.load(open(QUEUE_PATH, encoding="utf-8"))
    except Exception as e:
        print(f"    ⚠️  non riesco a leggere {QUEUE_PATH} ({e}): segnaposto non espansi")
        return prompt
    for token, key in PLACEHOLDERS.items():
        if token in prompt:
            val = q.get(key)
            if not val:
                sys.exit(f"Manca '{key}' in ai-content-queue.json, richiesto da {token}.")
            prompt = prompt.replace(token, val)
    if "{KIT}" in prompt:
        if not era:
            sys.exit("Il prompt usa {KIT}: specifica anche --era (pisa/barcellona/nazionale/origini).")
        kit = (q.get("kit_clauses") or {}).get(era)
        if not kit:
            sys.exit(f"Manca kit_clauses['{era}'] in ai-content-queue.json.")
        prompt = prompt.replace("{KIT}", kit)
    left = [t for t in ("{KIT}", "{GEAR}", "{BROADCAST}", "{SCENE_LOCK}") if t in prompt]
    if left:
        print(f"    ⚠️  segnaposto non riconosciuti rimasti nel prompt: {left}")
    # le clausole finiscono gia' con un punto: evita ".." dove il template ne aggiunge un altro
    return re.sub(r'\.\s*\.', '.', prompt)

# prezzo per secondo (USD). Le nostre immagini di riferimento (illustrazioni
# realistiche) fanno scattare il filtro "possibile persona reale" di Seedance
# sui tier strict, quindi usiamo sempre la variante -less-restriction (unica
# che accetta image_urls come reference in mode=omni_reference) — prezzo
# base +10% di markup, già incluso qui sotto.
PRICE = {
    "mini": {"480p": 0.077, "720p": 0.154},
    "fast": {"480p": 0.088, "720p": 0.176},
    "pro":  {"480p": 0.110, "720p": 0.220, "1080p": 0.550},
}
TASK_TYPE = {
    "mini": "seedance-2-mini-less-restriction",
    "fast": "seedance-2-fast-less-restriction",
    "pro": "seedance-2-less-restriction",
}

# ---------------------------------------------------------------- config
def load_config(path):
    if not os.path.exists(path):
        sys.exit(f"Manca {path}.")
    cfg = json.load(open(path, encoding="utf-8"))
    if not cfg.get("piapi", {}).get("api_key"):
        sys.exit("Manca 'piapi.api_key' in meta_config.json. Vedi GUIDA-VIDEO-AI.md.")
    return cfg

# ---------------------------------------------------------------- R2 (riferimenti permanenti)
def r2_client(cfg):
    import boto3
    r2 = cfg["r2"]
    return boto3.client(
        "s3", endpoint_url=r2["s3_endpoint"],
        aws_access_key_id=r2["access_key_id"],
        aws_secret_access_key=r2["secret_access_key"], region_name="auto",
    )

def upload_refs(cfg):
    s3 = r2_client(cfg)
    r2 = cfg["r2"]
    manifest = []
    for name in sorted(os.listdir(REFS_DIR)):
        if not name.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        path = os.path.join(REFS_DIR, name)
        key = f"character-ref/{name}"
        content_type = mimetypes.guess_type(path)[0] or "image/png"
        s3.upload_file(path, r2["bucket"], key, ExtraArgs={"ContentType": content_type})
        url = f"{r2['public_url_base'].rstrip('/')}/{key}"
        manifest.append(url)
        print(f"  ✓ {name} -> {url}")
    json.dump(manifest, open(REFS_MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nManifest salvato: {REFS_MANIFEST} ({len(manifest)} immagini)")
    return manifest

def load_refs():
    if not os.path.exists(REFS_MANIFEST):
        sys.exit("Manca il manifest dei riferimenti. Esegui prima: python3 genera_video_ai.py --upload-refs")
    return json.load(open(REFS_MANIFEST, encoding="utf-8"))

# ---------------------------------------------------------------- asset PiAPI permanenti
# Passare gli URL R2 direttamente con auto_upload_assets=true li ricarica come
# "ephemeral asset" ad OGNI generazione: il piano hobbyist ne permette solo 20
# in parallelo, quindi dopo ~5 clip (4 immagini l'una) l'API rifiuta tutto con
# "ephemeral asset hard cap reached". Fix: registrare le 4 immagini UNA VOLTA
# come asset permanenti (asset://<id>) e riusare sempre quelli.
ASSETS_MANIFEST = os.path.join(REFS_DIR, "manifest-assets.json")

def register_assets(cfg):
    ref_urls = load_refs()
    asset_ids = []
    for url in ref_urls:
        name = url.rsplit("/", 1)[-1]
        resp = api_request(cfg, "POST", "asset/upload", {
            "url": url, "asset_type": "Image", "name": name,
        })
        asset_id = resp["asset_id"]
        print(f"  ⬆️  {name} -> {asset_id} (in elaborazione…)")
        asset_ids.append(asset_id)
    # aspetta che diventino tutti "Active" prima di poterli usare nelle generazioni
    # (niente filtro ?status= lato server: sembra case-sensitive/inaffidabile,
    # più sicuro prendere la lista intera e controllare lo stato in locale)
    pending = set(asset_ids)
    waited = 0
    while pending and waited < 300:
        resp = api_request(cfg, "GET", "asset/list")
        by_id = {a["asset_id"]: a for a in resp["items"]}
        for aid in list(pending):
            status = (by_id.get(aid, {}).get("status") or "").lower()
            if status == "active":
                pending.discard(aid)
            elif status == "failed":
                sys.exit(f"Asset {aid} fallito: {by_id.get(aid)}")
        if pending:
            time.sleep(5); waited += 5
    if pending:
        sys.exit(f"Timeout: asset non ancora attivi dopo 5 minuti: {pending}")
    uris = [f"asset://{aid}" for aid in asset_ids]
    json.dump(uris, open(ASSETS_MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\nAsset permanenti pronti: {ASSETS_MANIFEST}")
    return uris

def load_asset_refs():
    if not os.path.exists(ASSETS_MANIFEST):
        sys.exit("Manca il manifest asset. Esegui prima: python3 genera_video_ai.py --register-assets")
    return json.load(open(ASSETS_MANIFEST, encoding="utf-8"))

# ---------------------------------------------------------------- PiAPI Seedance 2
def api_request(cfg, method, path, body=None):
    url = f"{API}/{path}"
    headers = {
        "X-API-Key": cfg["piapi"]["api_key"],
        "Content-Type": "application/json",
        # PiAPI gira dietro Cloudflare: lo user-agent di default di urllib
        # (Python-urllib/x.y) viene bloccato come bot (HTTP 403, error 1010).
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    }
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(5):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 4:
                # troppi task creati in parallelo: la generazione batch va accodata,
                # non lanciata tutta insieme.
                wait = 15 * (attempt + 1)
                print(f"    …429 rate limit, riprovo tra {wait}s")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:500]}")

def create_task(cfg, prompt, ref_urls, duration, resolution, tier):
    body = {
        "model": "seedance",
        "task_type": TASK_TYPE[tier],
        "input": {
            "prompt": prompt,
            "mode": "omni_reference",
            "image_urls": ref_urls,  # asset://<id> permanenti, vedi register_assets()
            "duration": duration,
            "resolution": resolution,
            "aspect_ratio": "9:16",
        },
    }
    resp = api_request(cfg, "POST", "task", body)
    return resp["data"]["task_id"]

def wait_task(cfg, task_id, timeout=900, interval=8):
    waited = 0
    while waited < timeout:
        resp = api_request(cfg, "GET", f"task/{task_id}")
        data = resp["data"]
        status = (data.get("status") or "").lower()
        if status == "completed":
            return data["output"]["video"]
        if status == "failed":
            raise RuntimeError(f"Generazione fallita: {json.dumps(data)[:500]}")
        print(f"    …stato: {status or 'in coda'} ({waited}s)")
        time.sleep(interval); waited += interval
    raise RuntimeError("Timeout: generazione non completata dopo 15 minuti.")

def download_video(url, out_path):
    # anche il CDN del video finito sta dietro Cloudflare: stesso fix dello user-agent.
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    })
    with urllib.request.urlopen(req) as resp, open(out_path, "wb") as f:
        f.write(resp.read())

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Genera clip AI con il volto di Calciovich coerente (Seedance 2 via PiAPI).")
    ap.add_argument("--upload-refs", action="store_true", help="carica/aggiorna le immagini di riferimento su R2")
    ap.add_argument("--register-assets", action="store_true", help="registra le immagini come asset PiAPI permanenti (necessario dopo --upload-refs, una tantum)")
    ap.add_argument("--prompt", help="descrizione della scena da generare")
    ap.add_argument("--duration", type=int, default=6, help="secondi, 4-15 (default 6)")
    ap.add_argument("--resolution", default="480p", choices=["480p", "720p", "1080p"])  # 480p = standard di budget + look archivio VHS
    ap.add_argument("--tier", default="fast", choices=["mini", "fast", "pro"], help="qualità/costo (default fast)")
    ap.add_argument("--era", choices=["pisa", "barcellona", "nazionale", "origini"],
                    help="era del canone: espande {KIT} con kit_clauses[era] (vedi ai-content-queue.json)")
    ap.add_argument("--out", help="nome file di output (senza estensione)")
    ap.add_argument("--dry-run", action="store_true", help="mostra solo il piano/costo stimato, non genera nulla")
    ap.add_argument("--budget-cap", type=float, default=DEFAULT_MONTHLY_CAP_USD,
                    help=f"tetto di spesa mensile in dollari (default {DEFAULT_MONTHLY_CAP_USD:.0f})")
    ap.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS_PER_ITEM,
                    help="tentativi a pagamento massimi per lo stesso item "
                         f"(default {DEFAULT_MAX_ATTEMPTS_PER_ITEM})")
    ap.add_argument("--config", default=os.path.join(HERE, "meta_config.json"))
    args = ap.parse_args()

    if args.upload_refs:
        cfg = json.load(open(args.config, encoding="utf-8"))
        upload_refs(cfg)
        return

    if args.register_assets:
        cfg = load_config(args.config)
        register_assets(cfg)
        return

    if not args.prompt:
        sys.exit("Specifica --prompt \"descrizione della scena\" (oppure --upload-refs).")

    args.prompt = expand_placeholders(args.prompt, args.era)

    per_sec = PRICE[args.tier].get(args.resolution)
    if per_sec is None:
        sys.exit(f"Risoluzione {args.resolution} non disponibile per il tier {args.tier}.")
    cost = args.duration * per_sec
    print(f"Piano: tier={args.tier}  risoluzione={args.resolution}  durata={args.duration}s  costo stimato=${cost:.2f}")

    # BUGFIX 03/09 (audit Codex): il README prometteva un tetto di spesa e un
    # limite di tentativi per item, e non esisteva nessuno dei due — qui si
    # stampava una stima e basta. Ora la spesa e' registrata e il tetto e' vero.
    ledger = Budget(OUTPUT_DIR, provider="piapi",
                    monthly_cap_usd=args.budget_cap,
                    max_attempts_per_item=args.max_attempts)
    print(f"  {ledger.summary()}")
    if args.dry_run:
        print("DRY RUN: nessuna generazione eseguita.")
        ledger.close()
        return 0

    item_key = args.out or (args.prompt or "")[:60]
    try:
        # Prenotazione PRIMA della chiamata: i soldi escono prima che possiamo
        # registrarli, quindi il record deve venire per primo.
        reservation = ledger.reserve(cost, item=item_key,
                                     note=f"{args.tier} {args.resolution} {args.duration}s")
    except (BudgetExceeded, TooManyAttempts) as e:
        print(f"\n⛔ {e}")
        ledger.close()
        return 2

    cfg = load_config(args.config)
    ref_urls = load_asset_refs()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_name = args.out or time.strftime("clip-%Y%m%d-%H%M%S")
    out_path = os.path.join(OUTPUT_DIR, f"{out_name}.mp4")

    print(f"⬆️  Creo il task Seedance ({len(ref_urls)} riferimenti)…")
    try:
        task_id = create_task(cfg, args.prompt, ref_urls, args.duration, args.resolution, args.tier)
    except Exception as e:
        # Il task non e' stato creato: il fornitore non ha lavorato, quindi la
        # prenotazione si libera davvero.
        reservation.release(note=f"task non creato: {str(e)[:120]}")
        ledger.close()
        raise
    print(f"    task_id: {task_id}\n⏳ Attendo il rendering…")
    try:
        video_url = wait_task(cfg, task_id)
        print(f"⬇️  Scarico: {video_url}")
        download_video(video_url, out_path)
    except Exception as e:
        # Il task ESISTE: molto probabilmente il fornitore ha generato e
        # fatturato. La prenotazione resta a carico — sbagliare per eccesso di
        # prudenza qui costa un po' di margine, sbagliare al contrario costa
        # soldi non contati.
        reservation.settle(note=f"task {task_id} creato ma non recuperato: {str(e)[:120]}")
        ledger.close()
        raise
    reservation.settle(note=f"task {task_id}")
    print(f"✅ Fatto: {out_path}")
    print(f"   {ledger.summary()}")
    ledger.close()
    return 0

if __name__ == "__main__":
    sys.exit(main())
