#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crea_audiolibro.py — costruisce i LONG-FORM a costo ZERO dal libro.

E' la fabbrica del contenuto che monetizza: le 4.000 ore YPP si fanno solo col
long-form (le ore degli Short non contano), e qui il long-form non costa nulla —
testo del libro + edge-tts (voci neurali gratis) + illustrazioni gia' prodotte +
musica "obito" di proprieta' dell'autore.

Produce una scena JSON compatibile con make_video.py; poi:
  python3 genera_voci_free.py scene/<nome>.json     # voci gratis (edge-tts)
  python3 make_video.py scene/<nome>.json           # render locale 16:9

USO
  # anteprima: quanto dura e come viene spezzato
  python3 crea_audiolibro.py --capitoli 00 01 02 --titolo "Il libro · Parte 1" --dry-run

  # crea la scena JSON vera
  python3 crea_audiolibro.py --capitoli 00 01 02 --titolo "Il libro · Parte 1" --out libro-p1
"""
import os, re, json, glob, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
CAPITOLI = os.path.join(os.path.dirname(os.path.dirname(HERE)), "04-capitoli")
ILL = os.path.join(HERE, "illustrazioni")
WPM = 150  # ritmo di narrazione stimato (edge-tts DiegoNeural, rate +0%)

# Illustrazioni associate ai capitoli: si riusa quello che gia' esiste, in ordine
# narrativo. Chiave = prefisso del capitolo.
MAPPA_ILL = {
    "00": ["ill-volto-giovane", "ill-bambino"],
    "01": ["ill-01-passerotto", "ill-bambino", "ill-abuela"],
    "02": ["ill-02-primo-tiro", "ill-05-gol-impossibile"],
    "03": ["ill-03-profezia-vecio", "ill-volto-giovane"],
    "04": ["ill-02-primo-tiro", "ill-granchio"],
    "05": ["ill-19-video-virale", "ill-cartellone"],
    "06": ["ill-06-zenit", "ill-16-spazio-creato", "ill-17-complimento-avversario"],
    "07": ["ill-cartellone", "ill-granchio"],
    "08": ["ill-07-sbadiglio", "ill-cartellone"],
    "09": ["ill-05-gol-impossibile", "ill-16-spazio-creato"],
    "10": ["ill-07-sbadiglio", "ill-09-noiona"],
    "11": ["ill-06-zenit", "ill-09-noiona"],
    "12": ["ill-09-noiona", "ill-08-grande-silenzio"],
    "13": ["ill-08-grande-silenzio", "ill-15-ultima-sera"],
    "14": ["ill-09-noiona", "ill-15-ultima-sera"],
    "15": ["ill-15-ultima-sera", "ill-08-grande-silenzio"],
    "16": ["ill-11-realizzazione", "ill-volto-2"],
    "17": ["ill-10-telefonata", "ill-11-realizzazione"],
    "18": ["ill-10-telefonata", "ill-volto-1"],
    "19": ["ill-12-ritorno", "ill-abuela"],
    "20": ["ill-12-ritorno", "ill-volto-2"],
}
FALLBACK = ["ill-volto-1", "ill-volto-2", "ill-volto-giovane"]


def esiste(nome):
    return os.path.exists(os.path.join(ILL, nome + ".png"))


def illustrazioni_per(prefix, n):
    """n immagini per il capitolo, ciclando su quelle mappate ed esistenti."""
    cand = [x for x in MAPPA_ILL.get(prefix, []) if esiste(x)]
    if not cand:
        cand = [x for x in FALLBACK if esiste(x)]
    return [cand[i % len(cand)] for i in range(n)]


def pulisci(md):
    """Da markdown a prosa leggibile ad alta voce."""
    t = re.sub(r"^---.*?---", "", md, flags=re.S)          # frontmatter
    t = re.sub(r"^#+\s*(.+)$", r"\1.", t, flags=re.M)      # heading -> frase
    t = re.sub(r"[*_`>]", "", t)                            # enfasi markdown
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", t)        # link/immagini
    t = re.sub(r"\n{2,}", "\n\n", t)
    return t.strip()


def blocchi(testo, max_parole=110):
    """Spezza in blocchi da ~max_parole rispettando i paragrafi e le frasi:
    ogni blocco diventa una scena (una immagine + una voce)."""
    out = []
    for par in [p.strip() for p in testo.split("\n\n") if p.strip()]:
        frasi = re.split(r"(?<=[.!?…»])\s+", par)
        cur = []
        for f in frasi:
            cur.append(f)
            if len(" ".join(cur).split()) >= max_parole:
                out.append(" ".join(cur).strip()); cur = []
        if cur:
            out.append(" ".join(cur).strip())
    return [b for b in out if b]


def titolo_capitolo(md, fallback):
    m = re.search(r"^#\s*(.+)$", md, flags=re.M)
    return m.group(1).strip() if m else fallback


def main():
    ap = argparse.ArgumentParser(description="Crea un long-form audiolibro dal libro (costo zero).")
    ap.add_argument("--capitoli", nargs="+", required=True, help="prefissi, es. 00 01 02")
    ap.add_argument("--titolo", required=True, help="titolo della puntata")
    ap.add_argument("--out", help="nome file scena (senza estensione)")
    ap.add_argument("--parole-per-scena", type=int, default=110)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    scenes, tot_parole, indice = [], 0, []
    for pref in a.capitoli:
        cand = glob.glob(os.path.join(CAPITOLI, f"{pref}-*.md"))
        if not cand:
            print(f"⚠️  nessun capitolo con prefisso {pref}, salto."); continue
        md = open(cand[0], encoding="utf-8").read()
        titolo = titolo_capitolo(md, os.path.basename(cand[0]))
        testo = pulisci(md)
        # la prima riga e' il titolo trasformato in frase: la togliamo dalla voce
        testo = re.sub(r"^" + re.escape(titolo) + r"\.\s*", "", testo)
        bl = blocchi(testo, a.parole_per_scena)
        imgs = illustrazioni_per(pref, len(bl))
        indice.append((titolo, len(bl), sum(len(b.split()) for b in bl)))

        # card di capitolo: prima scena del capitolo con il titolo a schermo
        for i, (b, im) in enumerate(zip(bl, imgs)):
            sc = {
                "image": f"illustrazioni/{im}.png",
                "audio": f"audio/{a.out or 'audiolibro'}_{len(scenes)+1}.mp3",
                "vo": b,
                "motion": ["in", "out", "left", "right"][len(scenes) % 4],
            }
            if i == 0:
                sc["pre"] = "Capitolo"
                sc["big"] = titolo
            scenes.append(sc)
            tot_parole += len(b.split())

    minuti = tot_parole / WPM
    print(f"\n📖 {a.titolo}")
    for t, n, w in indice:
        print(f"   • {t} — {n} scene, {w} parole (~{w/WPM:.1f} min)")
    print(f"\n   TOTALE: {len(scenes)} scene · {tot_parole} parole · ~{minuti:.1f} minuti")
    if minuti < 12:
        print("   ⚠️  sotto i 12 min: per le ore di visione conviene aggiungere capitoli.")
    elif minuti > 30:
        print("   ⚠️  sopra i 30 min: valuta di dividerlo in due parti.")
    else:
        print("   ✅ durata ottimale per le ore di visione (15-25 min).")

    if a.dry_run:
        print("\nDRY RUN: nessun file scritto.")
        return

    cfg = {
        "title": a.out or "audiolibro",
        "format": "horizontal",
        "fps": 30,
        "voice": "Alice",
        "_nota": "Long-form audiolibro generato da crea_audiolibro.py — costo zero "
                 "(testo del libro + edge-tts + illustrazioni + musica obito).",
        "intro": a.titolo,
        "intro_image": "illustrazioni/ill-volto-2.png",
        "outro": "La Vera Storia di Calciovich",
        "outro_image": "illustrazioni/ill-volto-1.png",
        "music": "assets/obito.mp3",
        "scenes": scenes,
    }
    out = os.path.join(HERE, "scene", f"{a.out or 'audiolibro'}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(cfg, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n✓ scena scritta: scene/{os.path.basename(out)}")
    print(f"  1) python3 genera_voci_free.py scene/{os.path.basename(out)}   # voci gratis")
    print(f"  2) python3 make_video.py scene/{os.path.basename(out)}         # render 16:9")


if __name__ == "__main__":
    main()
