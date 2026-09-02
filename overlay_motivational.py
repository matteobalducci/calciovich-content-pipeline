#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
overlay_motivational.py — post-produzione per il format Pillar D2 "edit
motivazionale, quote-over-footage" (vedi FORMATI-VIDEO-AI.md): caption
originali (MAI testo di canzoni reali, questioni di copyright) sincronizzate
sopra una clip AI, colonna sonora "obito" (di proprietà, monetizzabile) in
sottofondo mixata con l'audio ambientale della clip, filigrana canale.

Riusa build_caption_strip/watermark logic di overlay_broadcast.py ma senza
grafica da tabellino (nessuna partita/punteggio: e' un montaggio solista).

USO
  python3 overlay_motivational.py output/ai-clips/CLIP.mp4 \\
      --captions "0-2.3:Cado.|2.3-4.7:Mi rialzo. Ancora.|4.7-7:Un giorno guarderanno tutti." \\
      --music assets/obito.mp3 --music-volume 0.35
  # -> output/ai-clips/CLIP.tv.mp4
"""
import os, sys, argparse, subprocess, tempfile

from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
HERE = os.path.dirname(os.path.abspath(__file__))


def find_font(bold=False):
    cands = []
    if bold:
        cands += ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                  "/Library/Fonts/Arial Bold.ttf",
                  "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"]
    cands += ["/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc"]
    for c in cands:
        if os.path.exists(c):
            return c
    sys.exit("Nessun font trovato.")


def probe_size(path):
    p = subprocess.run([FFMPEG, "-i", path], capture_output=True, text=True)
    for line in p.stderr.splitlines():
        if "Video:" in line:
            for tok in line.replace(",", " ").split():
                if "x" in tok:
                    a, _, b = tok.partition("x")
                    if a.isdigit() and b.isdigit():
                        return int(a), int(b)
    sys.exit("Dimensioni video non trovate.")


def build_caption_strip(size, text):
    """Caption centrale, grande, stile 'quote-over-footage' (non striscia da
    telecronaca): zona sicura sopra il 60% dell'altezza, come da revisione
    29/07 su overlay_broadcast.py (la UI nativa copre l'ultimo ~20%)."""
    W, H = size
    strip = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(strip)
    f = ImageFont.truetype(find_font(bold=True), int(H * 0.038))
    maxw = int(W * 0.82)
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textbbox((0, 0), t, font=f)[2] <= maxw:
            cur = t
        else:
            lines.append(cur); cur = w
    lines.append(cur)
    lines = lines[:2]
    total_h = sum(d.textbbox((0, 0), ln, font=f)[3] for ln in lines) + int(H * 0.02) * (len(lines) - 1)
    y = int(H * 0.55) - total_h // 2
    for ln in lines:
        tb = d.textbbox((0, 0), ln, font=f)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        d.text((W / 2 - tw / 2 + 2, y - tb[1] + 2), ln, font=f, fill=(0, 0, 0, 170))
        d.text((W / 2 - tw / 2, y - tb[1]), ln, font=f, fill=(255, 255, 255, 255))
        y += th + int(H * 0.02)
    return strip


def main():
    ap = argparse.ArgumentParser(description="Caption motivazionali + musica per il format D2.")
    ap.add_argument("video")
    ap.add_argument("--captions", required=True,
                     help="'INIZIO-FINE:testo|...' in secondi, es. '0-2.3:Cado.|2.3-4.7:Mi rialzo.'")
    ap.add_argument("--music", default=os.path.join(HERE, "assets", "obito.mp3"))
    ap.add_argument("--music-volume", type=float, default=0.35)
    ap.add_argument("--ambient-volume", type=float, default=0.5,
                     help="volume dell'audio ambientale originale della clip AI")
    ap.add_argument("--no-watermark", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()

    if not os.path.exists(args.video):
        sys.exit(f"File non trovato: {args.video}")
    W, H = probe_size(args.video)

    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    wm_path = os.path.join(HERE, "..", "filigrana-canale-150.png")
    wm_path = os.path.normpath(wm_path)
    if not args.no_watermark and os.path.exists(wm_path):
        wm = Image.open(wm_path).convert("RGBA")
        scale = (W * 0.10) / wm.width
        wm = wm.resize((int(wm.width * scale), int(wm.height * scale)))
        alpha = wm.getchannel("A").point(lambda a: int(a * 0.55))
        wm.putalpha(alpha)
        ov.alpha_composite(wm, (W - wm.width - int(W * 0.03), int(H * 0.04)))

    out = args.out or os.path.splitext(args.video)[0] + ".tv.mp4"
    tmpfiles = []
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            ov.save(tf.name); tmpfiles.append(tf.name)
        inputs = ["-i", args.video, "-i", tmpfiles[0]]
        vchain = "[0:v][1:v]overlay=0:0"
        caps = [c for c in args.captions.split("|") if c.strip()]
        for i, cap in enumerate(caps):
            timing, _, text = cap.partition(":")
            t0, _, t1 = timing.partition("-")
            strip = build_caption_strip((W, H), text.strip())
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                strip.save(tf.name); tmpfiles.append(tf.name)
            inputs += ["-i", tmpfiles[-1]]
            vchain = f"{vchain}[v{i}];[v{i}][{i + 2}:v]overlay=0:0:enable='between(t,{float(t0)},{float(t1)})'"
        vchain += "[vout]"

        has_music = args.music and os.path.exists(args.music)
        if has_music:
            music_idx = len(inputs) // 2
            inputs += ["-stream_loop", "-1", "-i", args.music]
            achain = (f"[0:a]volume={args.ambient_volume}[amb];"
                      f"[{music_idx}:a]volume={args.music_volume}[mus];"
                      f"[amb][mus]amix=inputs=2:duration=first:dropout_transition=0[aout]")
            filter_complex = f"{vchain};{achain}"
            map_args = ["-map", "[vout]", "-map", "[aout]"]
        else:
            filter_complex = vchain
            map_args = ["-map", "[vout]", "-map", "0:a?"]

        subprocess.run([FFMPEG, "-y", *inputs, "-filter_complex", filter_complex,
                        *map_args, "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                        "-c:a", "aac", "-b:a", "160k", "-shortest", out],
                       check=True, capture_output=True)
    finally:
        for f in tmpfiles:
            try: os.unlink(f)
            except OSError: pass
    print(f"✅ {out}")


if __name__ == "__main__":
    main()
