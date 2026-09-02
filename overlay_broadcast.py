#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
overlay_broadcast.py — aggiunge alle clip AI le grafiche "da telecronaca
d'archivio" dei riferimenti (Baggio Lokomotiv '93 / USA '94 / Cecoslovacchia
'90): blocchi-bandiera + punteggio in alto, nome giocatore + minuto in basso
con font corsivo retrò. Le grafiche si fanno QUI in post (controllo totale,
niente scritte AI sbagliate), non nel prompt di generazione.

Preset per le tre epoche canoniche (story bible): pisa / barcellona / nazionale.

USO
  python3 overlay_broadcast.py output/ai-clips/CLIP.mp4 --era nazionale \\
      --away HON --score 3-0 --player CALCIOVICH --minute 88 --comp "COPPA DEL MONDO"
  # -> output/ai-clips/CLIP.tv.mp4

Opzioni: --era pisa|barcellona|nazionale (colori blocco casa) · --away SIGLA
  avversario (default RIV) · --away-colors "#800020" · --score · --player ·
  --minute · --comp riga piccola sotto il nome · --out PATH
"""
import os, sys, argparse, subprocess, tempfile

from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

ERA = {
    # colori del blocco-bandiera "casa" (due colonne verticali) — allineati il 27/08
    # ai nuovi kit_clauses "inventati" (ai-content-queue.json) che hanno sostituito i
    # vecchi schemi nero/blu e blu/granata (troppo simili a club reali) per i gol-ai.
    "pisa":       {"label": "PIS", "colors": ("#c2652a", "#1a8a78")},   # terracotta + verde acqua
    "barcellona": {"label": "BCN", "colors": ("#5b2a6b", "#7f1d3a")},   # prugna -> bordeaux
    "nazionale":  {"label": "SLV", "colors": ("#4a5a78", "#ffffff")},  # blu ardesia + bianco
}

def find_font(bold=False, italic=False):
    cands = []
    if italic:
        cands += ["/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf",
                  "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
                  "/System/Library/Fonts/Supplemental/Times New Roman Bold Italic.ttf"]
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

def flag_block(draw, x, y, w, h, colors, label, font):
    """Blocco-bandiera a due colonne verticali con sigla sotto, stile grafica TV anni '90."""
    half = w // 2
    draw.rounded_rectangle([x - 3, y - 3, x + w + 3, y + h + 3], radius=6, fill=(245, 245, 240, 235))
    draw.rectangle([x, y, x + half, y + h], fill=colors[0])
    draw.rectangle([x + half, y, x + w, y + h], fill=colors[1])
    tb = draw.textbbox((0, 0), label, font=font)
    tw = tb[2] - tb[0]
    draw.text((x + (w - tw) / 2, y + h + 8), label, font=font, fill=(255, 255, 255, 255),
              stroke_width=2, stroke_fill=(0, 0, 0, 200))

def build_overlay(size, era, away, away_colors, score, player, minute, comp):
    W, H = size
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    f_label = ImageFont.truetype(find_font(bold=True), int(H * 0.023))
    f_score = ImageFont.truetype(find_font(bold=True), int(H * 0.055))
    f_player = ImageFont.truetype(find_font(italic=True, bold=True), int(H * 0.042))
    f_comp = ImageFont.truetype(find_font(bold=True), int(H * 0.024))

    # ---- fascia alta: bandiera casa · punteggio · bandiera ospite ----
    bw, bh = int(W * 0.16), int(W * 0.11)
    ty = int(H * 0.055)
    cx = W // 2
    home = ERA[era]
    flag_block(d, cx - int(W * 0.30) - bw // 2, ty, bw, bh, home["colors"], home["label"], f_label)
    flag_block(d, cx + int(W * 0.30) - bw // 2, ty, bw, bh, away_colors, away, f_label)

    stext = score.replace("-", " - ")
    tb = d.textbbox((0, 0), stext, font=f_score)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = int(W * 0.025)
    d.rounded_rectangle([cx - tw / 2 - pad, ty + bh / 2 - th / 2 - pad * 0.7,
                         cx + tw / 2 + pad, ty + bh / 2 + th / 2 + pad * 0.7],
                        radius=8, fill=(15, 15, 15, 200))
    d.text((cx - tw / 2, ty + bh / 2 - th / 2 - tb[1]), stext, font=f_score,
           fill=(255, 255, 255, 255))

    # ---- fascia bassa: nome giocatore + minuto (corsivo retrò) + riga competizione ----
    # Zona sicura (revisione 29/07): su TikTok/Reels/Shorts la UI nativa (didascalia,
    # username, audio) copre stabilmente l'ultimo ~18-20% del frame — tutto il blocco
    # testo deve finire prima di quella soglia, non solo iniziarci vicino.
    ptext = f"{player} {minute}'"
    tb = d.textbbox((0, 0), ptext, font=f_player)
    tw = tb[2] - tb[0]
    # 29/08: misurato su frame reale che con 0.76 la SECONDA riga (competizione) finiva a
    # 81,9% dell'altezza, oltre la soglia dell'80% coperta dalla UI nativa di TikTok/Reels.
    # 0.72 porta la fine del blocco a ~78%, dentro la zona sicura. Riverificare sempre con
    # un frame reale se si tocca questo valore.
    py = int(H * 0.72)
    d.text((cx - tw / 2 + 3, py + 3 - tb[1]), ptext, font=f_player, fill=(0, 0, 0, 190))
    d.text((cx - tw / 2, py - tb[1]), ptext, font=f_player, fill=(255, 250, 235, 255))
    if comp:
        tb2 = d.textbbox((0, 0), comp, font=f_comp)
        tw2 = tb2[2] - tb2[0]
        d.text((cx - tw2 / 2, py + int(H * 0.045) - tb2[1]), comp, font=f_comp,
               fill=(255, 255, 255, 230), stroke_width=2, stroke_fill=(0, 0, 0, 180))
    return ov

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
    """Striscia-caption stile sottotitolo telecronaca (come nei riferimenti
    d'archivio): box scuro semitrasparente + testo bianco, sopra la fascia nome."""
    W, H = size
    strip = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(strip)
    f = ImageFont.truetype(find_font(bold=True), int(H * 0.032))
    maxw = int(W * 0.86)
    # wrap semplice su due righe max
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textbbox((0, 0), t, font=f)[2] <= maxw:
            cur = t
        else:
            lines.append(cur); cur = w
    lines.append(cur)
    lines = lines[:2]
    # Zona sicura (revisione 29/07): alzata dal 70% al 60% per lasciare aria sopra la
    # fascia nome/competizione (ora a 0.76H) e restare ben lontana dalla UI nativa in basso.
    y = int(H * 0.60)
    for ln in lines:
        tb = d.textbbox((0, 0), ln, font=f)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        pad = int(W * 0.015)
        d.rounded_rectangle([W/2 - tw/2 - pad, y - pad*0.6, W/2 + tw/2 + pad, y + th + pad*0.6],
                            radius=5, fill=(10, 10, 10, 190))
        d.text((W/2 - tw/2, y - tb[1]), ln, font=f, fill=(255, 255, 255, 255))
        y += th + int(pad * 1.8)
    return strip

def main():
    ap = argparse.ArgumentParser(description="Grafiche broadcast stile archivio sulle clip AI.")
    ap.add_argument("video")
    ap.add_argument("--era", required=True, choices=list(ERA.keys()))
    ap.add_argument("--away", default="RIV", help="sigla avversario (3 lettere)")
    ap.add_argument("--away-colors", default="#7f1d1d", help="colore blocco avversario (1 o 2 hex separati da virgola)")
    ap.add_argument("--score", default="1-0")
    ap.add_argument("--player", default="CALCIOVICH")
    ap.add_argument("--minute", default="88")
    ap.add_argument("--comp", default="", help="riga piccola sotto il nome (es. SERIE B · COPPA DEL MONDO)")
    ap.add_argument("--captions", default="", help="telecronaca sincronizzata: 'INIZIO-FINE:testo|INIZIO-FINE:testo' in secondi, es. '0-4:lo prende in mezzo a due|8-12:COSA HA FATTO'")
    ap.add_argument("--no-watermark", action="store_true", help="senza filigrana canale")
    ap.add_argument("--out", help="default: VIDEO.tv.mp4")
    args = ap.parse_args()

    if not os.path.exists(args.video):
        sys.exit(f"File non trovato: {args.video}")
    W, H = probe_size(args.video)
    ac = args.away_colors.split(",")
    away_colors = (ac[0].strip(), (ac[1].strip() if len(ac) > 1 else ac[0].strip()))
    ov = build_overlay((W, H), args.era, args.away.upper(), away_colors,
                       args.score, args.player.upper(), args.minute, args.comp.upper())

    # filigrana canale (brand recall + difesa freebooting)
    wm_path = os.path.join(os.path.dirname(HERE_DIR := os.path.dirname(os.path.abspath(__file__))), "filigrana-canale-150.png")
    if not args.no_watermark and os.path.exists(wm_path):
        wm = Image.open(wm_path).convert("RGBA")
        scale = (W * 0.10) / wm.width
        wm = wm.resize((int(wm.width * scale), int(wm.height * scale)))
        alpha = wm.getchannel("A").point(lambda a: int(a * 0.55))
        wm.putalpha(alpha)
        ov.alpha_composite(wm, (W - wm.width - int(W * 0.03), int(H * 0.155)))

    out = args.out or os.path.splitext(args.video)[0] + ".tv.mp4"
    tmpfiles = []
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            ov.save(tf.name); tmpfiles.append(tf.name)
        inputs = ["-i", args.video, "-i", tmpfiles[0]]
        chain = "[0:v][1:v]overlay=0:0"
        # caption sincronizzate: un PNG per riga, overlay con finestra temporale
        caps = [c for c in args.captions.split("|") if c.strip()] if args.captions else []
        for i, cap in enumerate(caps):
            timing, _, text = cap.partition(":")
            t0, _, t1 = timing.partition("-")
            strip = build_caption_strip((W, H), text.strip())
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                strip.save(tf.name); tmpfiles.append(tf.name)
            inputs += ["-i", tmpfiles[-1]]
            chain = f"{chain}[v{i}];[v{i}][{i+2}:v]overlay=0:0:enable='between(t,{float(t0)},{float(t1)})'"
        subprocess.run([FFMPEG, "-y", *inputs, "-filter_complex", chain,
                        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
                        "-c:a", "copy", out], check=True, capture_output=True)
    finally:
        for f in tmpfiles:
            try: os.unlink(f)
            except OSError: pass
    print(f"✅ {out}")

if __name__ == "__main__":
    main()
