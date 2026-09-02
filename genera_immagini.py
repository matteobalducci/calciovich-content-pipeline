#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genera_immagini.py — genera in automatico le ~20 illustrazioni epiche col VOLTO coerente
usando Nano Banana (Gemini 2.5 Flash Image), partendo dalle 2 foto di riferimento.

LO ESEGUI TU sul tuo computer (ha internet). Claude non ha rete: scrive lo script, tu lo lanci.

SETUP (una volta):
  pip3 install google-genai pillow
  export GEMINI_API_KEY="la-tua-chiave"      # da Google AI Studio (anche free tier)
USO:
  python3 genera_immagini.py                 # genera tutte le mancanti (riprende da dove era)
  python3 genera_immagini.py ill-05-gol-impossibile   # rigenera solo una
Output: illustrazioni/<id>.png  (1080x1920, 9:16)
"""
import os, sys, io
from PIL import Image

HERE   = os.path.dirname(os.path.abspath(__file__))
REFDIR = os.path.normpath(os.path.join(HERE, "..", "reference"))
OUTDIR = os.path.join(HERE, "illustrazioni")
os.makedirs(OUTDIR, exist_ok=True)
MODEL  = "gemini-2.5-flash-image"   # "Nano Banana"
W, H   = 1080, 1920

# Identità da preservare SEMPRE (Calciovich è un CALCIATORE: niente bandana, niente orecchini)
IDENTITY = ("the SAME young mestizo Salvadoran footballer across every image — warm bronze skin, "
    "LONG dark wavy hair worn loose OR held back by a thin plain sporty headband across the forehead, "
    "strong cheekbones, calm intense melancholic eyes, gentle never arrogant. "
    "He is a footballer. ABSOLUTELY NO bandana, NO patterned cloth on the head, NO earrings, NO jewelry")

# Variante BAMBINO (per le scene d'infanzia): stesso volto, ~9 anni
CHILD = ("the SAME boy as a CHILD, around 9 years old — warm bronze skin, long dark wavy hair "
    "(loose or with a thin plain headband), big melancholic eyes, small and slight. "
    "A little Salvadoran footballer boy. NO bandana, NO earrings, NO jewelry")
STYLE = ("epic painterly illustration, cinematic dramatic lighting, dynamic heroic composition, "
    "anime key-art intensity (Hunter x Hunter inspired), deep blue and amber palette, volumetric "
    "light, dust and particles, mythic atmosphere, highly detailed, vertical 9:16 composition. "
    "NO red or brown flat background, NO number, NO text/logos.")

# id -> descrizione della scena
SCENES = {
 "ill-volto-1":        "a close-up portrait of him, calm and a little melancholic, soft rim light",
 "ill-volto-2":        "an intense close-up portrait of him in dramatic backlight, shadow and gold",
 "ill-volto-giovane":  "a heroic three-quarter portrait of him, a vast blurred stadium behind",
 "ill-bambino":        "a CLOSE-UP of him as a child, eyes wide with a mix of fear and wonder, a red-dust courtyard at dawn behind",
 "ill-abuela":         "an old Salvadoran grandmother with big hands at a window of a small blue house, warm and worried",
 "ill-granchio":       "a sleek sinister agent in a suit walking sideways like a crab, oily grin, black cars behind (NOT the main character)",
 "ill-cartellone":     "a giant night billboard with a heroic image of him and the words 'SII LEGGENDA', a tiny man looking up",
 "ill-01-passerotto":  "a small boy in a red-dust courtyard, a worn hand-stitched ball orbiting his foot as if alive, a smoking volcano far away",
 "ill-02-primo-tiro":  "a small boy striking a ball that bends an impossible curve through golden air, dust trail, villagers watching in awe",
 "ill-03-profezia-vecio":"a wiry old coach kneeling and holding the boy's head in calloused hands, intense prophetic gaze, dusk chiaroscuro",
 "ill-04-cattedrale":  "EXTREME WIDE ANGLE architectural establishing shot, no visible faces: a colossal floodlit night football stadium towering like a cathedral of concrete and light, tiny ant-sized silhouettes of 80,000 fans filling the vast curved stands, an empty green pitch far below, godlike scale, lens flare, painterly cinematic matte-painting composition, camera pulled far back and high",
 "ill-05-gol-impossibile":"him mid-strike, the ball rocketing into the top corner from impossible distance, net rippling, crowd exploding, low dynamic angle",
 "ill-06-zenit":       "him standing alone and luminous at the center of a roaring arena, towering, divine light beams from above",
 "ill-07-sbadiglio":   "a vast HALF-EMPTY stadium, a single bored spectator yawning, him small and still on the pitch, cold light",
 "ill-08-grande-silenzio":"him alone in a totally empty stadium after a goal, looking at the rippling net, melancholic, one bird, vast emptiness",
 "ill-09-noiona":      "a grey faceless woman in an empty grandstand looking down with pity, him tiny on the field, ominous",
 "ill-10-telefonata":  "his face lit only by a phone screen in a dark luxury room, a tear held back, profound loneliness",
 "ill-11-realizzazione":"an extreme close-up of his face in the dark, the exact instant of a devastating quiet understanding",
 "ill-12-ritorno":     "him older, returning to the red-dust courtyard at dusk, red dust rising around his feet, two rusty netless goals",
 "ill-13-otra-finale": "him laughing in the red dust, dirty and free, surrounded by children playing, warm golden light, joyful and epic",
 "ill-14-auto-nere":   "sleek black cars on a red dust village road, sharp-suited men with too-wide smiles getting out, ominous corporate intrusion into a rural place (NOT the main character)",
 "ill-15-ultima-sera": "an elderly Salvadoran grandmother, warm worn hands, tenderly holding a young boy's head against her chest at night by lamplight, seen from behind or in profile so her face and hands lead the shot, an old worn hand-stitched ball hidden inside an open suitcase nearby, tender heartbreaking farewell, warm humble domestic interior (grandmother is the focus, NOT the main hero character)",
 "ill-16-spazio-creato":"mid-action: him receiving the ball with his back to two defenders closing in, a sudden sharp low turn ripping open an impossible gap between them, dynamic motion blur, dust and grass flying, dramatic low heroic angle",
 "ill-17-complimento-avversario":"him face to face with a rival defender after the match, the rival smiling warmly and touching his shoulder in genuine respect, floodlit pitch, human warmth cutting through rivalry",
 "ill-18-vecio-bicchiere":"the old wiry coach alone at a dim village bar counter, slowly setting down a full glass of liquor untouched, distant unsettled troubled expression, a small glowing TV in the background, quiet foreboding (NOT the main character)",
 "ill-19-video-virale": "countless smartphone screens glowing in the dark, the same viral goal replaying on every screen at once, view counters scrolling upward endlessly, digital fever spreading, no faces visible",
 "ill-20-folla-stadio": "eighty thousand fans rising to their feet at once inside a colossal floodlit stadium, arms raised, deafening roar of love, confetti drifting, seen from pitch level looking up",
 "ill-21-marchio":      "a stark black silhouette crest logo with a giant number 10, stamped identically across jerseys, shoe boxes, perfume bottles, pasta packaging and sports water bottles laid out like product photography, satirical branding overload, graphic design mockup style, no faces",
 "ill-24-linea-prodotti":"an absurdly infinite supermarket aisle receding into the distance, every product identical and stamped with the same black silhouette logo and number 3 — shirts, shoes, perfume, pasta, mattresses — endless consumerist perspective, satirical product-shot lighting, no faces",
 "ill-22-ristorante-solo":"him alone at a restaurant table, hood up, seen from behind or the side, surrounded by walls covered in giant advertisements of his own face selling water and his silhouette selling shoes, a glowing city billboard visible through the window, isolating surreal contrast between warm restaurant light and cold ad glow",
 "ill-23-mogli-figurine":"rows of glossy trading-card style portraits of glamorous women lined up on a shelf like collectible figurines, tabloid press mockup aesthetic, satirical objectification, small numbers printed under each card, no main character",
 "ill-26-uomo-solo-ovunque":"him standing utterly alone in the middle of an opulent empty penthouse suite at night, a vast empty bed behind him, floor-to-ceiling window overlooking an anonymous foreign city skyline, profound isolation despite luxury",
 "ill-27-risveglio-confuso":"him sitting up disoriented in an unfamiliar hotel bed at night, confused frightened expression, hand pressed to his own forehead trying to remember, generic luxury room with no identifying features, a single dim lamp",
 "ill-28-pisa-serieb":  "him from the front wearing a plain modest navy-blue Italian lower-division football kit with a large clearly readable white number 10 on the chest, standing in a small shabby half-empty concrete stadium under flat grey daylight, damp stands, puddles on a worn pitch, humble unglamorous beginnings, no sponsors visible",
 "ill-29-goleador-seriale":"mid-action scoring goal after goal in a rapid dynamic collage-like composition, defenders scattered on the ground, a scoreboard-like overlay of an absurd goal tally, relentless dominance in a modest stadium, motion blur and dust",
 "ill-30-barcellona":   "him unveiled in an iconic colossal world-famous stadium, deep red and blue colors, tens of thousands of fans, camera flashes, record-transfer press-conference energy, godlike scale, painterly cinematic wide shot",
 "ill-31-nazionale-mondiale":"him from the front wearing his small nation's blue and white national team jersey with a large clearly readable number 10 on the chest, walking out onto a colossal World Cup stadium stage, holding back emotion, the whole stadium roaring, immense pride and scale",
 "ill-32-record-un-altro":"a torn newspaper front page pinned to a wall, mostly blank white space, one small headline in bold: 'UN ALTRO', a small blurry photo of a goalkeeper standing still watching the ball fly past, satirical minimalism, no main character",
 "ill-33-sesto-gol":     "him scoring calmly and effortlessly with almost no emotion, goalkeeper standing frozen and resigned instead of diving, ball already in net, a scoreboard glimpsed showing a lopsided score, cold clinical perfection instead of joy",
 "ill-34-silenzio-stadio":"a packed colossal stadium under flat dull grey light, NO confetti, NO fireworks, NO cheering arms raised: eighty thousand people sitting perfectly still in their seats, arms down, blank resigned faces, a mute funeral-like hush, muted desaturated colors, unsettling anticlimactic stillness",
 "ill-35-bambino-cartello":"a small boy about eight years old sitting alone in the front row of a stadium, wearing a jersey with a big number 10, holding a homemade cardboard sign lowered onto his lap instead of raised, sad quiet disappointed expression, stadium blurred behind",
 "ill-36-fine-vince-tutto":"a giant gold trophy alone on a pedestal under a single spotlight in an otherwise dark colossal stadium, a torn newspaper front page overlapping the image with one huge black word 'FINE', no cheering crowd visible, hollow victory",
 "ill-37-vetta-vuota":  "him standing utterly alone at the very top of a stadium, arms empty, no trophy in hand anymore, looking down at a distant blurred crowd far below, vast empty sky above him, profound emptiness at the summit, painterly wide shot",
 "ill-38-posti-vuoti":  "a half-empty stadium seen from the pitch, scattered empty seats like missing teeth throughout the crowd, a few people quietly leaving through the exits, muted daylight, melancholic not dramatic",
 "ill-39-telecronista":  "a TV sports commentator alone in a broadcast booth overlooking the pitch, headset microphone close to his mouth, frozen mid-sentence with a stunned emotional expression, studio lights, intimate close shot (NOT the main character)",
 "ill-40-macchine-perfette":"a cozy living room glowing with the light of many screens, each screen showing flawless AI-made content (a painting, a song waveform, a perfect plated meal), the family on the couch watching with blank uninterested faces, everything technically perfect and emotionally dead, satirical warm-cold contrast",
 "ill-41-bambina-cane-patata":"a small girl looking down ashamed at her own crooked crayon drawing of a lopsided potato-shaped dog on the fridge, a tablet beside her showing a flawless AI-generated dog illustration, a dropped crayon on the floor, heartbreaking quiet moment, no main character",
 "ill-42-campetto-silenzioso":"a dusty red-earth football courtyard behind a small church at dusk, two rusty goalposts with torn nets, a lone adult goalkeeper figure standing motionless not playing, warm fading light, quiet foreboding stillness, distant silhouette not the main character",
 "ill-43-tomasito-risponde":"a grown man in work clothes answering an old phone next to a dusty red-earth football courtyard at dusk, surprised emotional expression, rusty goalposts behind him, warm humble neighborhood, NOT the main character",
 "ill-44-tv-click":     "a dark bedroom lit only by a glowing TV screen showing a replay: a beautiful goal, then the camera panning to reveal empty stadium seats in the same shot, him sitting on the edge of a bed in silhouette watching, an old worn ball in his hands, the exact instant of a realization dawning",
 "ill-45-tentativo-sbagliato":"him trying deliberately to miss a shot, an awkward forced strained motion, but the ball still curves perfectly into the top corner anyway, a conflicted anguished expression instead of joy, betrayed by his own perfect body",
 "ill-46-errore-finto":  "him having just kicked the ball wildly into the crowd stands on purpose, standing still waiting, the crowd silent and unmoved with knowing unimpressed faces instead of cheering, awkward failed anticlimax, no applause",
 "ill-47-sparizione":    "an empty spot of grass at the center of a football pitch where a player should be, teammates glancing at the empty space with confusion, a distant figure walking away off the pitch with a cap pulled low, vanishing into a tunnel, quiet unsettling absence",
 "ill-48-bambino-tomas": "him kneeling in red dust next to a curious small boy on a humble courtyard football pitch at golden dusk, handing the boy a worn stitched ball, warm tender mentoring moment, rusty netless goalposts behind, dirty knees, joyful simplicity",
 "ill-49-campetto-pieno":"a small red-dust courtyard football pitch packed with many children playing joyfully at once, a grown man laughing in a goalmouth between rusty posts, warm golden dusk light, dust kicked up everywhere, pure chaotic joy, wide shot",
 "ill-50-tomasito-amico":"him as a child taking a joyful shot in a red-dust courtyard at golden dusk, another small boy diving to block it between two rusty netless goalposts and missing, both laughing, dust kicked up everywhere, warm carefree childhood friendship, no sadness, pure joy",
 "ill-52-subentro-panchina":"him sitting on the substitutes bench in full kit, focused determined expression, a substitution board glowing nearby with a number on it, floodlit stadium at night in the background, coiled tension of a moment before entering a decisive match, no crowd faces visible",
 "ill-53-reazione-incredulita":"EXTREME CLOSE-UP of a fictional generic rival defender's face frozen in pure comic disbelief, mouth wide open, eyes bulging, sweat, blurred floodlit stadium behind, plain unbranded dark kit, no real team crest, no real player, exaggerated cartoonish shock",
 "ill-54-reazione-resa":"EXTREME CLOSE-UP of a fictional generic rival defender's face with hands pressed to his own head in comic resigned defeat, eyes closed, blurred floodlit stadium behind, plain unbranded kit, no real team crest, no real player, exaggerated tragicomic despair",
}

# Scene d'infanzia: usano l'identita' CHILD (Calciovich da bambino)
CHILD_IDS = {"ill-bambino", "ill-01-passerotto", "ill-02-primo-tiro", "ill-03-profezia-vecio", "ill-50-tomasito-amico"}

# Scene SENZA Calciovich (altri personaggi): niente identita' di Calciovich nel prompt
OTHER_IDS = {"ill-abuela", "ill-granchio", "ill-09-noiona", "ill-14-auto-nere",
             "ill-15-ultima-sera", "ill-18-vecio-bicchiere", "ill-19-video-virale", "ill-20-folla-stadio",
             "ill-04-cattedrale", "ill-21-marchio", "ill-24-linea-prodotti", "ill-23-mogli-figurine",
             "ill-32-record-un-altro", "ill-34-silenzio-stadio", "ill-35-bambino-cartello",
             "ill-36-fine-vince-tutto", "ill-38-posti-vuoti", "ill-39-telecronista",
             "ill-40-macchine-perfette", "ill-41-bambina-cane-patata", "ill-42-campetto-silenzioso",
             "ill-43-tomasito-risponde", "ill-53-reazione-incredulita", "ill-54-reazione-resa"}

def who_for(cid):
    """Identita' da anteporre alla scena: '' per altri personaggi, CHILD per l'infanzia, IDENTITY adulto."""
    if cid in OTHER_IDS: return ""
    return CHILD if cid in CHILD_IDS else IDENTITY

def load_refs():
    refs = []
    for n in ("calciovich-ref-1.png", "calciovich-ref-2.png"):
        p = os.path.join(REFDIR, n)
        if os.path.exists(p):
            refs.append(Image.open(p).convert("RGB"))
    if not refs:
        sys.exit("ERRORE: nessuna reference in %s (servono calciovich-ref-1.png / -2.png)" % REFDIR)
    return refs

def cover(im):
    iw, ih = im.size; s = max(W/iw, H/ih)
    im = im.resize((int(iw*s), int(ih*s)), Image.LANCZOS)
    iw, ih = im.size; l=(iw-W)//2; t=(ih-H)//2
    return im.crop((l, t, l+W, t+H))

def main():
    from google import genai
    key = os.environ.get("GEMINI_API_KEY")
    if not key: sys.exit("ERRORE: esporta GEMINI_API_KEY (Google AI Studio).")
    client = genai.Client(api_key=key)
    refs = load_refs()
    only = sys.argv[1] if len(sys.argv) > 1 else None
    todo = {only: SCENES[only]} if only else SCENES
    for i, (cid, scene) in enumerate(todo.items(), 1):
        out = os.path.join(OUTDIR, cid + ".png")
        if os.path.exists(out) and not only:
            print(f"[{i}/{len(todo)}] salto {cid} (gia' presente)"); continue
        who = who_for(cid)
        subj = f"{who}. " if who else ""
        prompt = f"Create a vertical 9:16 illustration. {subj}Scene: {scene}. Style: {STYLE}"
        try:
            resp = client.models.generate_content(model=MODEL, contents=[prompt, *refs])
            saved = False
            for part in resp.candidates[0].content.parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    im = Image.open(io.BytesIO(part.inline_data.data)).convert("RGB")
                    cover(im).save(out)
                    print(f"[{i}/{len(todo)}] OK  {cid}"); saved = True; break
            if not saved:
                print(f"[{i}/{len(todo)}] NESSUNA IMMAGINE per {cid} (riprova/cambia prompt)")
        except Exception as e:
            print(f"[{i}/{len(todo)}] ERRORE {cid}: {e}")
    print("\nFatto. Controlla la cartella illustrazioni/ e approva/rigenera ciò che non convince.")

if __name__ == "__main__":
    main()
