#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genera_voci_free.py — genera TUTTE le voci narranti GRATIS con Edge-TTS (voci neurali
Microsoft, italiano, nessuna chiave/limite). Legge il campo 'vo' di ogni scena dei JSON
e salva l'mp3 col nome del campo 'audio'. Stesso schema di genera_voci.py (ElevenLabs),
ma a costo zero. Qualita' vicina a ElevenLabs; usalo come default finche' non serve di piu'.

SETUP (una volta):
  pip3 install --user edge-tts
USO:
  python3 genera_voci_free.py                              # tutte le voci mancanti (tutti i JSON)
  python3 genera_voci_free.py scene/shorts/short01.json    # solo un video
  python3 genera_voci_free.py scene/shorts/short01.json --force   # rigenera anche le esistenti
Output: audio/<nome indicato nel json>.mp3

Voce: it-IT-DiegoNeural (maschile, calda, narrante). Ritmo leggermente rallentato e tono
abbassato per il narratore complice/malinconico di Calciovich. Cambia VOICE/RATE/PITCH sotto.
"""
import os, sys, json, glob, subprocess

HERE  = os.path.dirname(os.path.abspath(__file__))
VOICE = "it-IT-DiegoNeural"   # alt. maschile: it-IT-GiuseppeMultilingualNeural
RATE  = "+0%"                 # ritmo narrante deciso (il groove lo dà la musica)
PITCH = "-3Hz"               # tono leggermente piu' basso

def json_files(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    if args:
        return [a if os.path.isabs(a) else os.path.join(HERE, a) for a in args]
    files = sorted(glob.glob(os.path.join(HERE, "scene", "shorts", "*.json")))
    for extra in sorted(glob.glob(os.path.join(HERE, "scene", "ep*.json"))):
        files.append(extra)
    return files

def main():
    force = "--force" in sys.argv
    total = made = 0
    os.makedirs(os.path.join(HERE, "audio"), exist_ok=True)
    for jf in json_files(sys.argv):
        cfg = json.load(open(jf, encoding="utf-8"))
        for sc in cfg.get("scenes", []):
            if not (sc.get("audio") and sc.get("vo")): continue
            total += 1
            out = os.path.join(HERE, sc["audio"])
            os.makedirs(os.path.dirname(out), exist_ok=True)
            srt = os.path.splitext(out)[0] + ".srt"
            if os.path.exists(out) and not force: continue
            r = subprocess.run(
                ["python3", "-m", "edge_tts", "--voice", VOICE,
                 f"--rate={RATE}", f"--pitch={PITCH}",
                 "--text", sc["vo"], "--write-media", out, "--write-subtitles", srt],
                capture_output=True, text=True)
            if r.returncode == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
                made += 1; print(f"OK  {os.path.basename(out)} (+srt)")
            else:
                if os.path.exists(out) and os.path.getsize(out) == 0: os.remove(out)
                print(f"ERRORE {os.path.basename(out)}: {r.stderr.strip()[:200]}")
    print(f"\nFatto. Voci generate: {made}/{total} (le altre erano gia' presenti). Voce: {VOICE}")

if __name__ == "__main__":
    main()
