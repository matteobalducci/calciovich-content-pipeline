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

USO (una volta, da un terminale con browser)
  python3 youtube_readonly_auth.py
Scegli, nella schermata Google, l'account/canale del canale attuale ("Calciovich"
oggi, Gol Impossibili dopo il rebrand).
"""
import os

from youtube_analytics_auth import write_private

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET = os.path.join(HERE, "youtube_client_secret_readonly.json")
TOKEN_PATH = os.path.join(HERE, "youtube_readonly_token.json")
READONLY_SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    print("Consenso Google a SOLA LETTURA (youtube.readonly, progetto "
          "calciovich-video-analytics)...")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, READONLY_SCOPES)
    creds = flow.run_local_server(port=0)
    write_private(TOKEN_PATH, creds.to_json())
    print(f"OK — token salvato in {TOKEN_PATH}")


if __name__ == "__main__":
    main()
