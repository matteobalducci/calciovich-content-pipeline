#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genera_thumbnail.py — crea una thumbnail YouTube (1280x720) da un'illustrazione + testo grande,
stile alta-CTR (testo bold, contorno netto, vignetta, accento ambra). €0.

USO:
  python3 genera_thumbnail.py <id-immagine> "TESTO GRANDE" "etichetta piccola" [out.png]
  es: python3 genera_thumbnail.py ill-01-passerotto "TORNAVA DA SOLO" "Calciovich · Ep.1"
Cerca l'immagine prima in illustrazioni/wide/ poi in illustrazioni/.
Output: thumbnails/<id>.png (o il path passato).
"""
import os, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1280, 720
CREAM=(248,239,224); AMBER=(243,176,90); INK=(12,9,6)

def font(sz, bold=True):
    for p in ([ "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
                "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                "/System/Library/Fonts/Supplemental/Impact.ttf"] if bold else
              ["/System/Library/Fonts/Supplemental/Georgia.ttf"]):
        if os.path.exists(p): return ImageFont.truetype(p, sz)
    return ImageFont.load_default()

def find_img(cid):
    for d in ("illustrazioni/wide", "illustrazioni"):
        p = os.path.join(HERE, d, cid+".png")
        if os.path.exists(p): return p
    sys.exit(f"immagine non trovata: {cid}")

def cover(im):
    iw,ih=im.size; s=max(W/iw,H/ih)
    im=im.resize((int(iw*s),int(ih*s)),Image.LANCZOS)
    iw,ih=im.size; l=(iw-W)//2; t=(ih-H)//2
    return im.crop((l,t,l+W,t+H))

def wrap(d,text,fnt,maxw):
    words=text.split(); lines=[]; cur=""
    for w in words:
        t=(cur+" "+w).strip()
        if d.textlength(t,font=fnt)<=maxw: cur=t
        else: lines.append(cur); cur=w
    if cur: lines.append(cur)
    return lines

def main():
    if len(sys.argv)<3: sys.exit("uso: genera_thumbnail.py <id> \"TESTO\" [etichetta] [out.png]")
    cid=sys.argv[1]; big=sys.argv[2].upper()
    label=sys.argv[3] if len(sys.argv)>3 else ""
    out=sys.argv[4] if len(sys.argv)>4 else os.path.join(HERE,"thumbnails",cid+".png")
    os.makedirs(os.path.dirname(out),exist_ok=True)

    im=cover(Image.open(find_img(cid)).convert("RGB"))
    # gradiente scuro dal basso + lato sinistro per leggibilita'
    ov=Image.new("RGBA",(W,H),(0,0,0,0)); od=ImageDraw.Draw(ov)
    for y in range(H):
        a=int(190*max(0,(y-H*0.35)/(H*0.65)))
        od.line([(0,y),(W,y)],fill=(8,6,4,a))
    od.rectangle([0,0,int(W*0.62),H],fill=(8,6,4,60))
    im=Image.alpha_composite(im.convert("RGBA"),ov).convert("RGB")
    d=ImageDraw.Draw(im)

    # titolo grande in basso-sinistra
    fb=font(150)
    lines=wrap(d,big,fb,W*0.92)
    while len(lines)>3 and fb.size>70:
        fb=font(fb.size-10); lines=wrap(d,big,fb,W*0.92)
    lh=int(fb.size*1.02); total=lh*len(lines)
    y=H-total-int(H*0.07)
    for ln in lines:
        d.text((int(W*0.05), y), ln, font=fb, fill=CREAM,
               stroke_width=8, stroke_fill=INK); y+=lh
    # etichetta piccola in alto + barretta ambra
    if label:
        fl=font(46)
        d.rectangle([int(W*0.05),int(H*0.075),int(W*0.05)+14,int(H*0.075)+54],fill=AMBER)
        d.text((int(W*0.05)+34,int(H*0.08)), label, font=fl, fill=AMBER,
               stroke_width=3, stroke_fill=INK)
    im.save(out, quality=92)
    print("OK ->", out, im.size)

if __name__=="__main__":
    main()
