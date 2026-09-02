#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genera_foto_ai.py — genera FOTO "d'archivio" di Calciovich (Seedream 5 via
PiAPI, stesso account/credito dei video) con il volto coerente: le stesse 4
immagini di riferimento della pipeline video vengono allegate a ogni
generazione come reference (image-to-image, fino a 10 ref supportate).

Serve per il pillar foto (figurine, prime pagine, foto d'epoca, retroscena)
richiesto dall'autore il 19/07: contenuti-personaggio ad alta varianza, costo
~0,07-0,09$ a foto.

USO
  python3 genera_foto_ai.py --prompt "..." --aspect 4:5 --out figurina-pisa-01
  # -> output/ai-photos/figurina-pisa-01.jpg
Opzioni: --aspect (default 4:5, feed IG) · --tier lite|pro (default pro:
  supporta le reference; lite NON le supporta) · --dry-run
"""
import os, sys, json, time, argparse
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
# NB: per le FOTO servono gli URL R2 diretti (manifest.json): l'endpoint immagini
# non risolve gli asset:// del pool video.
REFS_MANIFEST = os.path.join(HERE, "character-ref", "manifest.json")
OUTPUT_DIR = os.path.join(HERE, "output", "ai-photos")
API = "https://api.piapi.ai/api/v1"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# come per i video: le nostre reference realistiche richiedono i tier less-restriction
TASK_TYPE = {"lite": "seedream-5-lite-less-restriction", "pro": "seedream-5-pro-less-restriction"}
PRICE = {"lite": 0.065, "pro": 0.085}

def api_request(cfg, method, path, body=None):
    url = f"{API}/{path}"
    headers = {"X-API-Key": cfg["piapi"]["api_key"], "Content-Type": "application/json", "User-Agent": UA}
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(4):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(15 * (attempt + 1)); continue
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:400]}")

def main():
    ap = argparse.ArgumentParser(description="Genera foto d'archivio di Calciovich (Seedream 5 via PiAPI).")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--aspect", default="4:5")
    ap.add_argument("--tier", default="pro", choices=["lite", "pro"])
    ap.add_argument("--out", help="nome file senza estensione (default: foto-YYYYmmdd-HHMMSS)")
    ap.add_argument("--no-refs", action="store_true", help="senza immagini riferimento (scene senza Calciovich)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--config", default=os.path.join(HERE, "meta_config.json"))
    args = ap.parse_args()

    print(f"Piano: tier={args.tier}  aspect={args.aspect}  costo stimato=${PRICE[args.tier]:.3f}")
    if args.dry_run:
        print("DRY RUN."); return

    cfg = json.load(open(args.config, encoding="utf-8"))
    body = {
        "model": "seedream",
        "task_type": TASK_TYPE[args.tier],
        "input": {
            "prompt": args.prompt,
            "aspect_ratio": args.aspect,
            "output_format": "jpeg",
            "size": "1K" if args.tier == "pro" else "2K",
        },
    }
    if not args.no_refs:
        if args.tier != "pro":
            sys.exit("Le immagini di riferimento (volto coerente) richiedono --tier pro.")
        body["input"]["image_urls"] = json.load(open(REFS_MANIFEST, encoding="utf-8"))

    resp = api_request(cfg, "POST", "task", body)
    task_id = resp["data"]["task_id"]
    print(f"    task_id: {task_id}\n⏳ Attendo…")
    waited = 0
    while waited < 300:
        data = api_request(cfg, "GET", f"task/{task_id}")["data"]
        status = (data.get("status") or "").lower()
        if status == "completed":
            out = data["output"]
            url = out.get("image_url") or (out.get("image_urls") or [None])[0]
            if not url:
                sys.exit(f"Nessuna image_url nell'output: {json.dumps(out)[:300]}")
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            name = args.out or time.strftime("foto-%Y%m%d-%H%M%S")
            path = os.path.join(OUTPUT_DIR, f"{name}.jpg")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req) as r, open(path, "wb") as f:
                f.write(r.read())
            print(f"✅ Fatto: {path}")
            return
        if status == "failed":
            sys.exit(f"Generazione fallita: {json.dumps(data.get('error') or data.get('logs'))[:400]}")
        time.sleep(6); waited += 6
    sys.exit("Timeout dopo 5 minuti.")

if __name__ == "__main__":
    main()
