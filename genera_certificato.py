#!/usr/bin/env python3
"""Genera il certificato di lettura PDF per il funnel OTRA (premio finale, sbloccato
scrivendo la parola segreta "sbagliosità" in risposta a una Storia).
Pagina 1: certificato con ritratto sepia + firmato da Calciovich.
Pagina 2: dedica finale dell'autore (anonima).
"""
import os
import importlib.util
from reportlab.lib.pagesizes import landscape, A5
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "output", "certificato-lettura-calciovich.pdf")
PORTRAIT = os.path.join(HERE, "output", "certificato-ritratto-sepia.png")
SOURCE_PORTRAIT = os.path.join(HERE, "illustrazioni", "ill-volto-2.png")

# Numero di edizione — fisso per tutti i lettori finché non esiste un backend che
# generi un certificato realmente unico per persona. Alzare EDIZIONE_NUM e aggiornare
# EDIZIONE_DATA a mano ogni tanto (es. ogni revisione quindicinale) per dare comunque
# un minimo di variazione nel tempo, senza costruire infrastruttura live.
EDIZIONE_NUM = "1"
EDIZIONE_DATA = "31 luglio 2026"

CREAM = HexColor("#f8efe0")
PAPER = HexColor("#f5e9d6")
INK = HexColor("#2a0f06")
INK_SOFT = HexColor("#4a2a12")
GOLD = HexColor("#a9752f")

pdfmetrics.registerFont(TTFont("BigCaslon", "/System/Library/Fonts/Supplemental/BigCaslon.ttf"))
pdfmetrics.registerFont(TTFont("Georgia", "/System/Library/Fonts/Supplemental/Georgia.ttf"))
pdfmetrics.registerFont(TTFont("Georgia-Italic", "/System/Library/Fonts/Supplemental/Georgia Italic.ttf"))
pdfmetrics.registerFont(TTFont("Zapfino", "/System/Library/Fonts/Supplemental/Zapfino.ttf"))


def ensure_portrait():
    """Genera (una volta) il ritratto sepia a matita da ill-volto-2.png, riusando
    la stessa resa delle tavole del libro (render_plates.pencil/sepia)."""
    if os.path.exists(PORTRAIT):
        return
    spec = importlib.util.spec_from_file_location(
        "render_plates", os.path.join(HERE, "..", "..", "06-export", "render_plates.py")
    )
    rp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rp)
    from PIL import Image

    im = Image.open(SOURCE_PORTRAIT).convert("RGB")
    box = (100, 250, 980, 1130)
    cropped = im.crop(box)
    if cropped.width > 900:
        cropped = cropped.resize((900, 900), Image.LANCZOS)
    toned = rp.pencil(cropped)
    tint = rp.sepia(toned)
    tint.save(PORTRAIT)


ensure_portrait()

W, H = landscape(A5)  # ~559 x 420 pt


def border(c):
    margin = 22
    c.setFillColor(PAPER)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.4)
    c.rect(margin, margin, W - 2 * margin, H - 2 * margin, fill=0, stroke=1)
    c.setLineWidth(0.6)
    c.rect(margin + 6, margin + 6, W - 2 * (margin + 6), H - 2 * (margin + 6), fill=0, stroke=1)
    return margin


c = canvas.Canvas(OUT, pagesize=(W, H))

# ---------- PAGINA 1: certificato (layout a due colonne) ----------
margin = border(c)

c.setFont("Georgia", 9)
c.setFillColor(GOLD)
c.drawCentredString(W / 2, H - 46, "C A L C I O V I C H   —   D O C U M E N T O   U F F I C I A L E   ( N O N   T R O P P O )")

# colonna sinistra: ritratto medaglione + sigillo
col_cx = margin + 18 + 92
portrait_cy = H - 160
portrait_r = 88

c.saveState()
path = c.beginPath()
path.circle(col_cx, portrait_cy, portrait_r)
c.clipPath(path, stroke=0)
img = ImageReader(PORTRAIT)
c.drawImage(img, col_cx - portrait_r, portrait_cy - portrait_r,
            width=portrait_r * 2, height=portrait_r * 2, mask="auto")
c.restoreState()

c.setStrokeColor(GOLD)
c.setLineWidth(2)
c.circle(col_cx, portrait_cy, portrait_r, fill=0, stroke=1)
c.setLineWidth(0.6)
c.circle(col_cx, portrait_cy, portrait_r - 5, fill=0, stroke=1)

# sigillo "10" sotto il ritratto
seal_cy = portrait_cy - portrait_r - 40
seal_r = 26
c.setFillColor(CREAM)
c.setStrokeColor(GOLD)
c.setLineWidth(1.4)
c.circle(col_cx, seal_cy, seal_r, fill=1, stroke=1)
c.setFont("Georgia", 20)
c.setFillColor(INK)
c.drawCentredString(col_cx, seal_cy - 7, "10")

# numero di edizione (fisso, aggiornabile a mano — vedi EDIZIONE_NUM/EDIZIONE_DATA sopra)
c.setFont("Georgia", 7.5)
c.setFillColor(GOLD)
c.drawCentredString(col_cx, seal_cy - seal_r - 14, f"Edizione N. {EDIZIONE_NUM} — {EDIZIONE_DATA}")

# colonna destra: titolo + testo
right_x = margin + 18 + 92 * 2 + 30
right_w = W - margin - 24 - right_x
cx2 = right_x + right_w / 2

c.setFont("BigCaslon", 27)
c.setFillColor(INK)
c.drawCentredString(cx2, H - 92, "Certificato di Lettura")

c.setStrokeColor(GOLD)
c.setLineWidth(1)
c.line(cx2 - 75, H - 104, cx2 + 75, H - 104)

body = [
    "Si certifica che chi tiene questo in mano",
    "ha letto «La Vera Storia di Calciovich»",
    "dalla prima all'ultima riga — pallone scucito compreso.",
]
c.setFont("Georgia", 11)
c.setFillColor(INK_SOFT)
y = H - 132
for line in body:
    c.drawCentredString(cx2, y, line)
    y -= 16

c.setFont("Georgia-Italic", 10.5)
c.setFillColor(INK)
y -= 12
c.drawCentredString(cx2, y, "“Bravo abbastanza da far ridere la gente,")
y -= 14
c.drawCentredString(cx2, y, "non così bravo da farla smettere.”")
y -= 13
c.setFont("Georgia", 8.5)
c.setFillColor(GOLD)
c.drawCentredString(cx2, y, "— l'abuela")

# firma — firmato da Calciovich, corsivo a mano
c.setFont("Zapfino", 14)
c.setFillColor(INK)
c.drawCentredString(cx2, margin + 58, "Calciovich")
c.setFont("Georgia", 7.5)
c.setFillColor(INK_SOFT)
c.drawCentredString(cx2, margin + 44, "il più forte di sempre")

c.setFont("Georgia", 8)
c.setFillColor(GOLD)
c.drawCentredString(W / 2, margin + 16, "Otra, sempre otra.")

c.showPage()

# ---------- PAGINA 2: dedica finale (anonima) ----------
margin = border(c)
cx = W / 2

c.setFont("Georgia", 9.5)
c.setFillColor(GOLD)
c.drawCentredString(cx, H - 62, "U N A   C O S A ,   F U O R I   D A L   P E R S O N A G G I O")

c.setStrokeColor(GOLD)
c.setLineWidth(1)
c.line(cx - 90, H - 80, cx + 90, H - 80)

dedica = [
    "Fuori dal personaggio, per un secondo.",
    "",
    "Da piccolo pensavo che essere il migliore in qualcosa fosse il punto",
    "d'arrivo — che bastasse essere abbastanza bravi, e tutto il resto",
    "(la fiducia, la gioia, il sentirsi a posto) sarebbe venuto da sé.",
    "",
    "Scrivendo questo libro ho capito una cosa più semplice e più strana:",
    "la perfezione non consola nessuno. Non chi la guarda, non chi ce l'ha.",
    "Essere il più bravo, il più famoso, il più perfetto — è quasi sempre",
    "una gabbia travestita da traguardo.",
    "",
    "Calciovich non sbaglia mai. Ed è proprio per questo che smette",
    "di stupire qualcuno.",
    "",
    "Se sei arrivato fin qui, grazie. Questa storia non parla solo di lui.",
]
c.setFont("Georgia-Italic", 10.5)
c.setFillColor(INK_SOFT)
y = H - 104
for line in dedica:
    if line:
        c.drawCentredString(cx, y, line)
    y -= 15.5

c.setFont("Georgia", 8.5)
c.setFillColor(GOLD)
c.drawCentredString(cx, margin + 16, "— chi ha scritto questa storia")

c.showPage()
c.save()
print(f"Salvato: {OUT}")
