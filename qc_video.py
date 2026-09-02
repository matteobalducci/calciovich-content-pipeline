#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qc_video.py — controllo qualità visivo delle clip AI: estrae una griglia di
fotogrammi (default 8, distribuiti uniformemente) da un mp4, così la revisione
copre TUTTA la clip e non solo il primo fotogramma (qlmanage).

Usa l'ffmpeg statico di imageio_ffmpeg (stesso di make_video.py): niente brew,
niente dipendenze di sistema.

USO
  python3 qc_video.py output/ai-clips/NOME.mp4           # 8 frame -> NOME.qc/
  python3 qc_video.py output/ai-clips/NOME.mp4 --frames 12
  python3 qc_video.py output/ai-clips/NOME.mp4 --contact # anche foglio unico NOME.qc.png
"""
import os, sys, json, argparse, subprocess

import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

def probe_duration(path):
    """Durata in secondi via ffmpeg (parse dello stderr, niente ffprobe)."""
    p = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True)
    for line in p.stderr.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            hms = line.split("Duration:")[1].split(",")[0].strip()  # 00:00:08.03
            h, m, s = hms.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError(f"Durata non trovata per {path}")

def extract_frames(path, n, outdir):
    os.makedirs(outdir, exist_ok=True)
    dur = probe_duration(path)
    frames = []
    for i in range(n):
        # evita il primissimo/ultimissimo istante (fade, frame neri)
        t = dur * (i + 0.5) / n
        out = os.path.join(outdir, f"frame-{i+1:02d}-t{t:.1f}s.png")
        subprocess.run([FFMPEG, "-y", "-ss", f"{t:.2f}", "-i", path,
                        "-frames:v", "1", "-q:v", "2", out],
                       capture_output=True, check=True)
        frames.append(out)
    return frames, dur

def contact_sheet(frames, out_png, cols=4):
    from PIL import Image
    imgs = [Image.open(f) for f in frames]
    w, h = imgs[0].size
    scale = 480 / w
    tw, th = int(w * scale), int(h * scale)
    rows = (len(imgs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tw, rows * th), (20, 20, 20))
    for i, im in enumerate(imgs):
        sheet.paste(im.resize((tw, th)), ((i % cols) * tw, (i // cols) * th))
    sheet.save(out_png)
    return out_png

def main():
    ap = argparse.ArgumentParser(description="Estrae fotogrammi distribuiti da una clip per il controllo qualità.")
    ap.add_argument("video", help="percorso del file mp4")
    ap.add_argument("--frames", type=int, default=8, help="quanti fotogrammi (default 8)")
    ap.add_argument("--contact", action="store_true", help="genera anche un foglio unico .qc.png")
    args = ap.parse_args()

    if not os.path.exists(args.video):
        sys.exit(f"File non trovato: {args.video}")
    base = os.path.splitext(args.video)[0]
    outdir = base + ".qc"
    frames, dur = extract_frames(args.video, args.frames, outdir)
    print(f"Durata: {dur:.1f}s — {len(frames)} fotogrammi in {outdir}/")
    for f in frames:
        print(f"  {f}")
    if args.contact:
        sheet = contact_sheet(frames, base + ".qc.png")
        print(f"Foglio di contatto: {sheet}")

if __name__ == "__main__":
    main()
