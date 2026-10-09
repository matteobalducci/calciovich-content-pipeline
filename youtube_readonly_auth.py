#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
youtube_readonly_auth.py — consenso YouTube Data API a SOLA LETTURA, per il job
cloud (raccogli_snapshot_cloud.py su GitHub Actions).

Perche' esiste: youtube_token.json (condiviso con carica_youtube.py) ha anche lo
scope youtube.upload. Copiato come secret su GitHub darebbe, in caso di fuga, il
potere di caricare/modificare video sul canale. Il job cloud deve solo leggere
statistiche. Stesso principio di youtube_analytics_auth.py: un client OAuth e un
token dedicati, con il minimo scope — e, con lo split di canale in arrivo (canale
attuale -> Gol Impossibili, canale nuovo -> Calciovich), un token per canale, mai
condiviso fra i due.

Client OAuth separato (youtube_client_secret_readonly.json, tipo Desktop, stesso
progetto GCP calciovich-video-analytics). Lo scope e' fissato QUI, al momento del
consenso: un token gia' concesso con piu' scope li mantiene tutti, quindi
restringerlo dopo non e' possibile — va concesso stretto fin dall'inizio.

UN TOKEN PER CANALE (client condiviso, token no): il token appartiene al canale scelto nella schermata
di consenso, non al client OAuth, quindi lo stesso client a sola lettura serve entrambi i canali.
  gol-impossibili -> youtube_readonly_token.json         (canale originale, secret YOUTUBE_READONLY_TOKEN_JSON)
  calciovich      -> youtube_readonly_libro_token.json   (canale del libro, secret YOUTUBE_READONLY_LIBRO_TOKEN_JSON)
Dopo il consenso il token viene verificato con channels.list(mine=True): se appartiene al canale sbagliato
NON viene salvato (un token sbagliato non dà errore: scriverebbe righe con l'etichetta di un altro canale).

USO (una volta per canale, da un terminale con browser)
  python3 youtube_readonly_auth.py                      # canale originale (Gol Impossibili)
  python3 youtube_readonly_auth.py --channel calciovich # canale del libro: nella schermata Google scegli QUEL canale
"""
import argparse
import os
import sys

from youtube_analytics_auth import write_private

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET = os.path.join(HERE, "youtube_client_secret_readonly.json")
READONLY_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
ORIGINAL, BOOK = "gol-impossibili", "calciovich"
TOKENS = {
    ORIGINAL: os.path.join(HERE, "youtube_readonly_token.json"),
    BOOK: os.path.join(HERE, "youtube_readonly_libro_token.json"),
}
CHANNEL_IDS = {"gol-impossibili": "UCLPBYAv19aizEYX4MmXV7rA", "calciovich": "UCy1V7Lwaeb8_6iaSEzSOtPA"}
TOKEN_PATH = TOKENS[ORIGINAL]          # compatibilita': il canale originale


def verify_owner(creds, channel_key):
    """Alza metriche_video.ChannelMismatch se il token non appartiene al canale atteso."""
    import googleapiclient.discovery
    import metriche_video
    yt = googleapiclient.discovery.build("youtube", "v3", credentials=creds)
    metriche_video.check_owner(yt, CHANNEL_IDS[channel_key])


def main(argv=None):
    ap = argparse.ArgumentParser(description="Consenso YouTube Data API a sola lettura, un token per canale")
    ap.add_argument("--channel", choices=sorted(TOKENS), default=ORIGINAL)
    args = ap.parse_args(argv)

    from google_auth_oauthlib.flow import InstalledAppFlow
    import metriche_video

    print(f"Consenso Google a SOLA LETTURA (youtube.readonly) per il canale '{args.channel}'. "
          f"Nella schermata Google scegli il canale {args.channel}...")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, READONLY_SCOPES)
    creds = flow.run_local_server(port=0)
    try:
        verify_owner(creds, args.channel)
    except metriche_video.ChannelMismatch as exc:
        sys.exit(f"TOKEN NON SALVATO: hai autorizzato il canale sbagliato ({exc}). "
                 f"Rilancia e scegli il canale {args.channel} nella schermata Google.")
    write_private(TOKENS[args.channel], creds.to_json())
    print(f"OK — token del canale '{args.channel}' verificato e salvato in {TOKENS[args.channel]}")


if __name__ == "__main__":
    main()
