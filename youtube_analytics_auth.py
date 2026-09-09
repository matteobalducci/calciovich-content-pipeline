#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
youtube_analytics_auth.py — consenso e refresh del token YouTube Analytics
(Fase 2 del layer analytics), isolato da carica_youtube.py/reauth_youtube.py.

Progetto GCP dedicato (calciovich-video-analytics), non calciovich-analytics: quel
progetto e' scope stretto solo publishing, e yt-analytics.readonly e' gia' stato
provato li' il 21/08 causando RefreshError + 403 (consent screen configurata solo
per i 2 scope minimi). Isolare qui il file del token non basta se si condivide il
progetto/consent screen: e' la consent screen del progetto a non avere lo scope,
non il file del token.

Due funzioni, non una condivisa:
- get_analytics_credentials_unattended() — usata da raccogli_finestre_fisse.py.
  Non chiama MAI InstalledAppFlow/run_local_server, per costruzione: un browser che
  si apre da solo su un LaunchAgent headless non fallisce pulito, si blocca. Se le
  credenziali non sono utilizzabili (scadute, mai concesse, file corrotto), lascia
  propagare l'eccezione — il chiamante decide cosa fare (Fase 2: consent_needed).
- main() (eseguito da qui, a mano) — l'UNICO posto dove run_local_server() viene
  mai chiamato per questo token. Richiede un browser: esegui questo script da un
  terminale con schermo, non da un LaunchAgent.

USO
  python3 youtube_analytics_auth.py    # consenso/rinnovo interattivo (serve un browser)
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET = os.path.join(HERE, "youtube_client_secret_analytics.json")
TOKEN_PATH = os.path.join(HERE, "youtube_analytics_token.json")
ANALYTICS_SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly"]


def get_analytics_credentials_unattended():
    """Nessun ramo interattivo raggiungibile: non importa InstalledAppFlow, non
    chiama run_local_server(). Se le credenziali non sono utilizzabili, propaga
    l'eccezione originale (FileNotFoundError, ValueError, RefreshError) — sta al
    chiamante (raccogli_finestre_fisse.py) decidere come degradare."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, ANALYTICS_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        open(TOKEN_PATH, "w").write(creds.to_json())
    return creds


def main():
    """Consenso interattivo — richiede un browser. Esegui a mano quando la
    dashboard segnala 'Serve un nuovo consenso YouTube Analytics'."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    print("Apro il browser per il consenso Google (yt-analytics.readonly, progetto "
          "calciovich-video-analytics)...")
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, ANALYTICS_SCOPES)
    creds = flow.run_local_server(port=0)
    open(TOKEN_PATH, "w").write(creds.to_json())
    print(f"OK — token salvato in {TOKEN_PATH}")


if __name__ == "__main__":
    main()
