#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genera_immagini_free.py — genera le illustrazioni epiche col volto di Calciovich GRATIS
via Pollinations (FLUX), senza chiave e senza billing. Riusa IDENTITY/STYLE/SCENES da
genera_immagini.py (stessa identita' visiva). Output upscalato a 1080x1920 (9:16).

Coerenza del volto: prompt-identita' fisso + seed costante per personaggio. Coerenza
TEMATICA forte (bandana blu, orecchino, capelli, incarnato), non foto-identica al 100%.
Per il volto perfetto serve Nano Banana (genera_immagini.py, a pagamento).

USO (illustrazioni per i video, id da SCENES in genera_immagini.py):
  python3 genera_immagini_free.py                 # tutte le mancanti (riprende da dove era)
  python3 genera_immagini_free.py ill-01-passerotto   # rigenera solo una
  python3 genera_immagini_free.py ill-01-passerotto --seed 123   # cambia seed (varia il volto)
Output: illustrazioni/<id>.png (1080x1920)

USO (foto IG ad-hoc, formati liberi diario_di_lalo/foto_archivio/figurina/retroscena/legami_*):
  python3 genera_immagini_free.py --prompt "<scena>" --aspect 4:5 --out <nome>
Output: output/ai-photos/<nome>.jpg — stile fotografico (non illustrazione), gratis via Pollinations.
"""
import os, sys, io, time, argparse, urllib.parse, urllib.request
from PIL import Image
from genera_immagini import STYLE, SCENES, IDENTITY, who_for   # stessa identita' visiva

HERE   = os.path.dirname(os.path.abspath(__file__))
SEED   = 777   # seed-personaggio fisso per massimizzare la coerenza del volto
# Default verticale 9:16 (Shorts). Con --wide diventa orizzontale 16:9 (long-form) in illustrazioni/wide/.
W, H   = 1080, 1920
OUTDIR = os.path.join(HERE, "illustrazioni")
ORIENT = "Vertical 9:16"

PHOTO_DIR = os.path.join(HERE, "output", "ai-photos")
# Stile fotografico "vera foto", opposto all'illustrazione pittorica usata per i libri/video.
PHOTO_STYLE = ("real photograph, photorealistic, natural light, candid documentary photography, "
               "authentic film grain, no illustration, no painting, no digital art look, no text, no watermark, no logo")

ASPECTS = {"4:5": (1080, 1350), "1:1": (1080, 1080), "9:16": (1080, 1920), "16:9": (1920, 1080)}

def set_wide():
    global W, H, OUTDIR, ORIENT
    W, H = 1920, 1080
    OUTDIR = os.path.join(HERE, "illustrazioni", "wide")
    ORIENT = "Horizontal 16:9 cinematic widescreen"

def cover_to(im, w, h):
    iw, ih = im.size; s = max(w/iw, h/ih)
    im = im.resize((int(iw*s), int(ih*s)), Image.LANCZOS)
    iw, ih = im.size; l=(iw-w)//2; t=(ih-h)//2
    return im.crop((l, t, l+w, t+h))

def cover(im):
    return cover_to(im, W, H)

def fetch(prompt, w, h, seed, enhance=True):
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
           + f"?width={w}&height={h}&seed={seed}&model=flux&nologo=true&enhance={'true' if enhance else 'false'}")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = urllib.request.urlopen(req, timeout=180).read()
    return Image.open(io.BytesIO(data)).convert("RGB")

def generate(cid, scene, seed):
    who = who_for(cid)
    subj = f"{who}. " if who else ""
    prompt = f"{ORIENT} illustration. {subj}Scene: {scene}. Style: {STYLE}"
    cover(fetch(prompt, W, H, seed)).save(os.path.join(OUTDIR, cid + ".png"))

def generate_photo(prompt_scene, out_name, aspect, seed, no_identity=False, enhance=True):
    """Foto IG ad-hoc (diario_di_lalo, foto_archivio, figurina, retroscena, legami_*):
    stile fotografico reale, non l'illustrazione pittorica dei video/libro."""
    w, h = ASPECTS.get(aspect, ASPECTS["4:5"])
    subj = "" if no_identity else f"{IDENTITY}. "
    prompt = f"{subj}Scene: {prompt_scene}. Style: {PHOTO_STYLE}"
    os.makedirs(PHOTO_DIR, exist_ok=True)
    out_path = os.path.join(PHOTO_DIR, out_name + ".jpg")
    cover_to(fetch(prompt, w, h, seed, enhance=enhance), w, h).save(out_path, quality=92)
    print(f"✅ Fatto: {out_path}")
    return out_path

def main():
    if "--prompt" in sys.argv:
        ap = argparse.ArgumentParser()
        ap.add_argument("--prompt", required=True)
        ap.add_argument("--aspect", default="4:5")
        ap.add_argument("--out", required=True)
        ap.add_argument("--seed", type=int, default=SEED)
        ap.add_argument("--no-identity", action="store_true", help="scena senza il volto di Calciovich (es. solo oggetti)")
        ap.add_argument("--no-enhance", action="store_true", help="disattiva il rewriting automatico del prompt lato Pollinations")
        args = ap.parse_args()
        generate_photo(args.prompt, args.out, args.aspect, args.seed, args.no_identity, enhance=not args.no_enhance)
        return

    if "--wide" in sys.argv: set_wide()
    os.makedirs(OUTDIR, exist_ok=True)
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    seed = SEED
    if "--seed" in sys.argv:
        seed = int(sys.argv[sys.argv.index("--seed") + 1])
    only = args[0] if args else None
    todo = {only: SCENES[only]} if only else SCENES
    for i, (cid, scene) in enumerate(todo.items(), 1):
        out = os.path.join(OUTDIR, cid + ".png")
        if os.path.exists(out) and not only:
            print(f"[{i}/{len(todo)}] salto {cid} (gia' presente)"); continue
        for attempt in range(1, 4):
            try:
                t = time.time(); generate(cid, scene, seed)
                print(f"[{i}/{len(todo)}] OK  {cid} ({time.time()-t:.0f}s)"); break
            except Exception as e:
                print(f"[{i}/{len(todo)}] tentativo {attempt} fallito {cid}: {str(e)[:120]}")
                time.sleep(3)
    print("\nFatto. Controlla illustrazioni/ e rigenera con --seed N cio' che non convince.")

if __name__ == "__main__":
    main()
