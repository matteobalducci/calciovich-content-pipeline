#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_video.py — Generatore video LOCALE e AUTOMATICO per Calciovich.
Da un file di scene (JSON) produce un MP4 motion-comic completo:
  immagini (SVG/PNG/JPG) -> Ken Burns -> testo a schermo -> voce TTS (macOS `say`)
  -> intro/outro brand -> montaggio -> MP4 con audio.

Dipendenze (tutte gratis, gia' installate):
  - Pillow                (composizione frame + testo)
  - svglib + reportlab    (rasterizza gli SVG senza cairo)
  - imageio-ffmpeg        (binario ffmpeg statico, niente admin)
  - /usr/bin/say          (TTS offline italiano, macOS)

Uso:
  python3 make_video.py scene/short01.json
"""
import os, sys, json, subprocess, tempfile, shutil, re

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import imageio_ffmpeg
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPM

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
HERE   = os.path.dirname(os.path.abspath(__file__))
BASE   = os.path.dirname(os.path.dirname(HERE))   # root del progetto

# ---- palette brand ----
TERRA   = (124, 45, 18)
TERRA2  = (91, 35, 16)
CREAM   = (248, 239, 224)
SAND    = (243, 200, 154)
INK     = (26, 19, 11)

def find_font(bold=False):
    cands = [
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for c in cands:
        if os.path.exists(c):
            return c
    return None
FONT_REG  = find_font(False)
FONT_BOLD = find_font(True) or FONT_REG

def F(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)

def resolve(path):
    if os.path.isabs(path):
        return path
    for base in (HERE, BASE):                      # engine dir, poi root progetto
        cand = os.path.normpath(os.path.join(base, path))
        if os.path.exists(cand):
            return cand
    return os.path.normpath(os.path.join(HERE, path))

WIDE = False   # video orizzontale: preferisci automaticamente le immagini illustrazioni/wide/

def wide_variant(path):
    """Per i video orizzontali: se esiste illustrazioni/wide/<nome>, usalo (16:9 dedicato)."""
    if not WIDE: return path
    d, fn = os.path.split(path)
    if os.path.basename(d) == "illustrazioni":
        cand = os.path.join(d, "wide", fn)
        if os.path.exists(resolve(cand)): return cand
    return path

def load_image(path, target):
    """Carica PNG/JPG -> PIL RGBA 'cover'. SVG/errore -> None (si usa lo sfondo brand)."""
    try:
        rp = resolve(wide_variant(path))
        if rp.lower().endswith(".svg"):
            return None   # nessun rasterizzatore SVG su questa macchina
        im = Image.open(rp).convert("RGBA")
        return cover_fit(im, target)
    except Exception:
        return None

def cover_fit(im, target):
    tw, th = target
    iw, ih = im.size
    s = max(tw/iw, th/ih)
    im = im.resize((max(1,int(iw*s)), max(1,int(ih*s))), Image.LANCZOS)
    iw, ih = im.size
    left = (iw - tw)//2; top = (ih - th)//2
    return im.crop((left, top, left+tw, top+th))

def vignette(size):
    w, h = size
    v = Image.new("L", size, 0)
    d = ImageDraw.Draw(v)
    d.ellipse([-w*0.3, -h*0.2, w*1.3, h*1.2], fill=255)
    v = v.filter(ImageFilter.GaussianBlur(w*0.18))
    dark = Image.new("RGBA", size, (10, 6, 3, 0))
    alpha = Image.eval(v, lambda x: 150 - int(x*150/255))
    dark.putalpha(alpha)
    return dark

def wrap(draw, text, font, maxw):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= maxw:
            cur = t
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines

def cinematic_bg(size):
    """Sfondo cinematografico scuro (fallback se manca l'illustrazione). Niente terracotta/3."""
    W, H = size
    im = Image.new("RGB", size, (14, 16, 20))
    d = ImageDraw.Draw(im)
    for i in range(H):
        t = i/H
        d.line([(0,i),(W,i)], fill=(int(22*(1-t)+8*t), int(26*(1-t)+10*t), int(32*(1-t)+14*t)))
    im = im.convert("RGBA")
    im.alpha_composite(vignette(size))
    return im

def compose_still(scene, size, idx):
    W, H = size
    img = load_image(scene["image"], size) if scene.get("image") else None
    if img is not None:
        base = Image.new("RGBA", size, (14,16,20,255))
        base.alpha_composite(img)
        base.alpha_composite(vignette(size))
    else:
        base = cinematic_bg(size)
    d = ImageDraw.Draw(base)

    # titolo grande opzionale (hook)
    y = int(H*0.10)
    if scene.get("pre"):
        fp = F(int(W*0.045));
        d.text((W/2, y), scene["pre"].upper(), font=fp, fill=SAND+(255,), anchor="mm")
        y += int(W*0.06)
    if scene.get("big"):
        fb = F(int(W*0.12), True)
        for line in wrap(d, scene["big"].upper(), fb, W*0.9):
            d.text((W/2, y), line, font=fb, fill=CREAM+(255,), anchor="mm",
                   stroke_width=3, stroke_fill=(40,20,10,220))
            y += int(W*0.13)

    # caption (testo a schermo, in basso con banda) — saltata se ci sono i sottotitoli sincronizzati
    if scene.get("text") and not SUBS_ACTIVE:
        ft = F(int(W*0.056), True)
        lines = wrap(d, scene["text"], ft, W*0.80)
        lh = int(W*0.075)
        block_h = lh*len(lines) + int(H*0.04)
        # Margine dal basso allargato 8%->16% (revisione 29/07, stessa ragione di
        # overlay_broadcast.py): con 3 righe il vecchio margine lasciava la banda a
        # pochi px dal bordo, dentro la zona coperta dalla UI nativa in riproduzione.
        band_top = H - block_h - int(H*0.16)
        band = Image.new("RGBA", (W, block_h + int(H*0.05)), (20,12,7,150))
        band = band.filter(ImageFilter.GaussianBlur(2))
        base.alpha_composite(band, (0, band_top))
        d = ImageDraw.Draw(base)
        ty = band_top + int(H*0.03)
        for line in lines:
            d.text((W/2, ty), line, font=ft, fill=CREAM+(255,), anchor="ma",
                   stroke_width=2, stroke_fill=(0,0,0,200))
            ty += lh
    return base.convert("RGB")

def badge_overlay_png(text, size):
    """PNG trasparente WxH col badge (angolo alto a destra), da sovrapporre DOPO il
    montaggio/Ken Burns cosi' resta fisso (non va sovrapposto sulla still di ogni
    scena: li' verrebbe zoomato/spostato dal pan della scena)."""
    W, H = size
    im = Image.new("RGBA", size, (0,0,0,0))
    d = ImageDraw.Draw(im)
    # Margini allargati (revisione 29/07): 3% dal bordo era troppo vicino allo spigolo
    # dove alcune piattaforme ritagliano/coprono con icone di UI — portato al 6%.
    fbadge = F(int(W*0.034), True)
    pad_x, pad_y = int(W*0.02), int(H*0.012)
    tw = d.textlength(text, font=fbadge)
    th = int(W*0.034)
    margin = int(W*0.06)
    bx2 = W - margin
    bx1 = bx2 - tw - pad_x*2
    by1 = int(H*0.05)
    by2 = by1 + th + pad_y*2
    d.rounded_rectangle([bx1, by1, bx2, by2], radius=int(th*0.5), fill=(20,12,7,190))
    d.text((bx1+pad_x, by1+pad_y), text, font=fbadge, fill=SAND+(255,), anchor="la")
    return im

def brand_card(size, kind, bg_image=None):
    """Cartello intro/outro cinematografico (scuro). Se bg_image: la usa come fondo scurito."""
    W, H = size
    if bg_image:
        base = load_image(bg_image, size)
        base = base if base is not None else cinematic_bg(size)
        dark = Image.new("RGBA", size, (8, 9, 12, 150))
        base.alpha_composite(dark)
        im = base.convert("RGB")
    else:
        im = cinematic_bg(size).convert("RGB")
    d = ImageDraw.Draw(im)
    if kind == "intro":
        d.text((W/2, H*0.45), "LA VERA STORIA DI", font=F(int(W*0.05)), fill=SAND, anchor="mm")
        d.text((W/2, H*0.53), "CALCIOVICH", font=F(int(W*0.13), True), fill=CREAM, anchor="mm",
               stroke_width=2, stroke_fill=(0,0,0,200))
    else:
        d.text((W/2, H*0.46), "otra.", font=F(int(W*0.14), True), fill=CREAM, anchor="mm",
               stroke_width=2, stroke_fill=(0,0,0,200))
        d.text((W/2, H*0.56), "@calciovich", font=F(int(W*0.05)), fill=SAND, anchor="mm")
    return im

# ---------- audio (TTS offline) ----------
def tts(text, out_m4a, voice="Alice"):
    aiff = tempfile.mktemp(suffix=".aiff")
    subprocess.run(["/usr/bin/say", "-v", voice, "-o", aiff, text], check=True)
    subprocess.run([FFMPEG, "-y", "-i", aiff, "-c:a", "aac", "-b:a", "160k", out_m4a],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    os.remove(aiff)
    return media_duration(out_m4a)

def use_audio(src, out_m4a):
    """Usa un file audio esterno (es. mp3 di ElevenLabs) come voce della scena."""
    subprocess.run([FFMPEG, "-y", "-i", src, "-c:a", "aac", "-b:a", "160k",
                    "-ar", "44100", "-ac", "2", out_m4a],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return media_duration(out_m4a)

def silence(out_m4a, dur):
    subprocess.run([FFMPEG, "-y", "-f", "lavfi", "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=44100", "-t", str(dur),
                    "-c:a", "aac", out_m4a], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def media_duration(path):
    r = subprocess.run([FFMPEG, "-i", path], stderr=subprocess.PIPE)
    m = re.search(rb"Duration: (\d+):(\d+):(\d+\.\d+)", r.stderr)
    if not m: return 3.0
    h, mn, s = m.groups()
    return int(h)*3600 + int(mn)*60 + float(s)

# ---------- sottotitoli sincronizzati (.srt da edge-tts) ----------
def parse_srt(path):
    cues = []
    if not path or not os.path.exists(path): return cues
    for b in re.split(r"\n\s*\n", open(path, encoding="utf-8").read().strip()):
        m = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", b)
        if not m: continue
        g = list(map(int, m.groups()))
        st = g[0]*3600+g[1]*60+g[2]+g[3]/1000.0
        en = g[4]*3600+g[5]*60+g[6]+g[7]/1000.0
        txt = " ".join(l.strip() for l in b.split("\n")[2:] if l.strip())
        if txt: cues.append([st, en, txt])
    return cues

def srt_ts(s):
    h=int(s//3600); m=int((s%3600)//60); sec=s%60
    return f"{h:02d}:{m:02d}:{sec:06.3f}".replace(".", ",")

def write_master_srt(cues, path):
    with open(path, "w", encoding="utf-8") as f:
        for i,(st,en,txt) in enumerate(cues,1):
            f.write(f"{i}\n{srt_ts(st)} --> {srt_ts(en)}\n{txt}\n\n")

# stile sottotitoli ASS (Shorts): grande, bold, contorno netto, in basso. Unita' in PlayResY=288.
SUB_STYLE = ("FontName=Georgia,FontSize=15,Bold=1,PrimaryColour=&H00E0EFF8,"
             "OutlineColour=&H00000000,BackColour=&H80000000,BorderStyle=1,Outline=3,Shadow=1,"
             "Alignment=2,MarginV=45")
SUBS_ACTIVE = False   # se True, compose_still NON disegna la caption statica (la rimpiazzano i sottotitoli)

# ---------- render scena (Ken Burns) ----------
def render_scene(still_png, audio_m4a, dur, out_mp4, W, H, fps, motion, idx):
    frames = max(1, int(round(dur*fps)))
    zdir = 1 if idx % 2 == 0 else -1
    if zdir > 0:
        z = "min(zoom+0.0006,1.18)"
    else:
        z = "if(eq(on,1),1.18,max(zoom-0.0006,1.0))"
    pan = {"left":  ("iw/2-(iw/zoom/2)-on*1.2", "ih/2-(ih/zoom/2)"),
           "right": ("iw/2-(iw/zoom/2)+on*1.2", "ih/2-(ih/zoom/2)"),
           "up":    ("iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)-on*1.0"),
           "in":    ("iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)")}.get(motion, ("iw/2-(iw/zoom/2)","ih/2-(ih/zoom/2)"))
    vf = (f"scale={W*2}:{H*2},zoompan=z='{z}':d={frames}:x='{pan[0]}':y='{pan[1]}'"
          f":s={W}x{H}:fps={fps},format=yuv420p")
    subprocess.run([FFMPEG, "-y", "-loop", "1", "-framerate", str(fps), "-t", f"{dur:.2f}",
                    "-i", still_png, "-i", audio_m4a, "-filter_complex", f"[0:v]{vf}[v]",
                    "-map", "[v]", "-map", "1:a", "-t", f"{dur:.2f}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", out_mp4],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

def main(scene_json):
    cfg = json.load(open(scene_json, encoding="utf-8"))
    vertical = cfg.get("format", "vertical") == "vertical"
    W, H = (1080, 1920) if vertical else (1920, 1080)
    global WIDE
    WIDE = not vertical          # orizzontale -> preferisci le immagini illustrazioni/wide/
    fps = cfg.get("fps", 30)
    voice = cfg.get("voice", "Alice")
    work = tempfile.mkdtemp(prefix="calcio_")
    clips = []
    global SUBS_ACTIVE
    SUBS_ACTIVE = cfg.get("subtitles", True)
    subs = []           # cue sottotitoli (start,end,text) sull'intera timeline
    timeline = [0.0]    # tempo cumulato (lista per closure)
    CARD = 1.4          # durata cartelli intro/outro (ritmo piu' serrato)

    def add_card(kind):
        still = os.path.join(work, f"{kind}.png")
        brand_card((W,H), kind, cfg.get(kind+"_image")).save(still)
        au = os.path.join(work, f"{kind}.m4a"); silence(au, CARD)
        out = os.path.join(work, f"{kind}.mp4")
        render_scene(still, au, CARD, out, W, H, fps, "in", 0)
        clips.append(out); timeline[0] += CARD

    if cfg.get("intro", True): add_card("intro")

    for i, sc in enumerate(cfg["scenes"]):
        still = os.path.join(work, f"s{i}.png")
        compose_still(sc, (W,H), i).save(still)
        au = os.path.join(work, f"s{i}.m4a")
        ext = resolve(sc["audio"]) if sc.get("audio") else None
        if ext and os.path.exists(ext):           # voce esterna (edge-tts/ElevenLabs)
            dur = use_audio(ext, au) + 0.18       # coda piu' corta = meno tempo morto
            if SUBS_ACTIVE:                       # sottotitoli sincronizzati (.srt accanto all'mp3)
                for st, en, txt in parse_srt(os.path.splitext(ext)[0] + ".srt"):
                    subs.append([timeline[0]+st, timeline[0]+min(en, dur), txt])
        elif sc.get("vo"):                        # altrimenti TTS offline macOS
            dur = tts(sc["vo"], au, voice) + 0.4
        else:
            dur = sc.get("dur", 3.0); silence(au, dur)
        dur = max(dur, sc.get("min", 2.0))
        out = os.path.join(work, f"s{i}.mp4")
        render_scene(still, au, dur, out, W, H, fps, sc.get("motion","in"), i)
        clips.append(out); timeline[0] += dur
        print(f"  scena {i+1}/{len(cfg['scenes'])} ok ({dur:.1f}s)")

    if cfg.get("outro", True): add_card("outro")

    # concat (filtro: re-decodifica tutto, robusto a parametri diversi)
    outdir = os.path.join(HERE, "output"); os.makedirs(outdir, exist_ok=True)
    final = os.path.join(outdir, cfg.get("title","video") + (".vert.mp4" if vertical else ".mp4"))
    # PRE-CONCAT A LOTTI (aggiunto 29/08). Il concat finale apre TUTTE le scene insieme in un
    # solo filter_complex, insieme a sottotitoli + badge + musica: con i long-form dal libro
    # (libro-p2 = 98 scene 1080p) ffmpeg veniva ucciso dal sistema per consumo di memoria
    # (returncode non-zero con stderr VUOTO — sintomo di SIGKILL, non di un errore ffmpeg).
    # libro-p1 (77 scene) passava ancora, quindi la soglia reale sta in mezzo. Qui uniamo le
    # scene in gruppi da 25 PRIMA del montaggio finale: l'ordine e la durata totale restano
    # identici (quindi i sottotitoli, che usano timing assoluti, restano sincronizzati), ma il
    # grafo finale vede ~4 input invece di 98.
    if len(clips) > 50:
        lotti, LOT = [], 25
        for gi in range(0, len(clips), LOT):
            gruppo = clips[gi:gi+LOT]
            lotto = os.path.join(work, f"lotto_{gi//LOT:03d}.mp4")
            gin = []
            for c in gruppo: gin += ["-i", c]
            gs = "".join(f"[{i}:v][{i}:a]" for i in range(len(gruppo)))
            rg = subprocess.run([FFMPEG, "-y", *gin, "-filter_complex",
                                 f"{gs}concat=n={len(gruppo)}:v=1:a=1[v][a]",
                                 "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                                 "-pix_fmt", "yuv420p", "-r", str(fps), "-c:a", "aac",
                                 "-ar", "44100", lotto],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if rg.returncode != 0:
                sys.stderr.write(rg.stderr.decode("utf-8", "replace")[-2000:])
                raise SystemExit(f"pre-concat del lotto {gi//LOT} fallito")
            lotti.append(lotto)
            print(f"  lotto {gi//LOT+1}/{(len(clips)+LOT-1)//LOT} unito ({len(gruppo)} scene)")
        clips = lotti

    inputs = []
    for c in clips: inputs += ["-i", c]
    n = len(clips)
    streams = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    parts = [f"{streams}concat=n={n}:v=1:a=1[vc][ac]"]

    # 1) sottotitoli sincronizzati bruciati sul video
    vlabel = "vc"
    if SUBS_ACTIVE and subs:
        master = os.path.join(work, "subs.srt"); write_master_srt(subs, master)
        esc = master.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        parts.append(f"[vc]subtitles='{esc}':force_style='{SUB_STYLE}'[v]")
        vlabel = "v"

    extra_in = []
    next_idx = n

    # 1b) badge numero ("Ep. N" / "#N") sovrapposto FISSO su tutto il video, dopo il
    # montaggio/Ken Burns (mai sulla still di scena, altrimenti verrebbe zoomato/spostato)
    badge = cfg.get("badge")
    if badge:
        badge_png = os.path.join(work, "badge.png")
        badge_overlay_png(badge, (W, H)).save(badge_png)
        extra_in += ["-loop", "1", "-i", badge_png]
        parts.append(f"[{vlabel}][{next_idx}:v]overlay=0:0:shortest=1[vb]")
        vlabel = "vb"
        next_idx += 1

    # 2) musica di sottofondo (in loop) sotto la voce, con DUCKING (sale quando non c'è voce)
    music = cfg.get("music", "assets/obito.mp3")   # default: colonna sonora "obito"
    music_path = resolve(music) if music else None
    alabel = "ac"
    if music_path and os.path.exists(music_path):
        extra_in += ["-stream_loop", "-1", "-i", music_path]
        vol = cfg.get("music_volume", 0.22)
        parts.append("[ac]asplit=2[av][ak]")
        parts.append(f"[{next_idx}:a]volume={vol},aformat=sample_rates=44100:channel_layouts=stereo[mus]")
        parts.append("[mus][ak]sidechaincompress=threshold=0.04:ratio=8:attack=15:release=1500:makeup=2[mduck]")
        parts.append("[av][mduck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]")
        alabel = "a"
        next_idx += 1

    filt = ";".join(parts)
    res = subprocess.run([FFMPEG, "-y", *inputs, *extra_in, "-filter_complex", filt,
                    "-map", f"[{vlabel}]", "-map", f"[{alabel}]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
                    "-c:a", "aac", "-ar", "44100", "-shortest", "-movflags", "+faststart", final],
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if res.returncode != 0:
        sys.stderr.write(res.stderr.decode("utf-8","replace")[-2000:])
        raise SystemExit("concat fallito")
    shutil.rmtree(work, ignore_errors=True)
    dur = media_duration(final)
    print(f"\n✅ VIDEO PRONTO: {final}\n   {W}x{H} · {dur:.1f}s")
    return final

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("uso: python3 make_video.py scene/<file>.json"); sys.exit(1)
    main(sys.argv[1])
