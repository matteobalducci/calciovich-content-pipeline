#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
genera_voci.py — genera in automatico tutte le voci narranti (ElevenLabs) leggendo il campo
'vo' di ogni scena dei JSON, e salvandole col nome indicato nel campo 'audio'.

LO ESEGUI TU (ha bisogno di internet + la tua chiave ElevenLabs).

SETUP (una volta):
  pip3 install elevenlabs
  export ELEVEN_API_KEY="la-tua-chiave"
  # scegli una voce maschile italiana su elevenlabs.io e incolla il suo Voice ID qui sotto:
  export ELEVEN_VOICE_ID="xxxxxxxxxxxxxxxxxxxx"
USO:
  python3 genera_voci.py                 # genera tutte le voci mancanti di tutti i JSON
  python3 genera_voci.py scene/shorts/short01.json   # solo un video
Output: audio/<nome indicato nel json>.mp3
"""
import os, sys, json, glob

HERE  = os.path.dirname(os.path.abspath(__file__))
MODEL = "eleven_multilingual_v2"          # gestisce bene l'italiano
SETTINGS = {"stability": 0.45, "similarity_boost": 0.85, "style": 0.30, "use_speaker_boost": True}

def json_files(argv):
    if len(argv) > 1:
        return [os.path.join(HERE, argv[1])] if not os.path.isabs(argv[1]) else [argv[1]]
    files = sorted(glob.glob(os.path.join(HERE, "scene", "shorts", "*.json")))
    for extra in ("scene/ep1.json",):
        p = os.path.join(HERE, extra)
        if os.path.exists(p): files.append(p)
    return files

def main():
    from elevenlabs.client import ElevenLabs
    key = os.environ.get("ELEVEN_API_KEY")
    vid = os.environ.get("ELEVEN_VOICE_ID")
    if not key: sys.exit("ERRORE: esporta ELEVEN_API_KEY.")
    if not vid: sys.exit("ERRORE: esporta ELEVEN_VOICE_ID (l'ID di una voce maschile IT su elevenlabs.io).")
    client = ElevenLabs(api_key=key)
    os.makedirs(os.path.join(HERE, "audio"), exist_ok=True)

    total = made = 0
    for jf in json_files(sys.argv):
        cfg = json.load(open(jf, encoding="utf-8"))
        for sc in cfg.get("scenes", []):
            if not (sc.get("audio") and sc.get("vo")): continue
            total += 1
            out = os.path.join(HERE, sc["audio"])
            os.makedirs(os.path.dirname(out), exist_ok=True)
            if os.path.exists(out): continue
            try:
                stream = client.text_to_speech.convert(
                    voice_id=vid, model_id=MODEL, text=sc["vo"],
                    output_format="mp3_44100_128", voice_settings=SETTINGS)
                with open(out, "wb") as f:
                    for chunk in stream:
                        if chunk: f.write(chunk)
                made += 1
                print(f"OK  {os.path.basename(out)}")
            except Exception as e:
                print(f"ERRORE {os.path.basename(out)}: {e}")
    print(f"\nFatto. Voci generate: {made}/{total} (le altre erano gia' presenti).")

if __name__ == "__main__":
    main()
