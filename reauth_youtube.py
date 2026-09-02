#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reauth_youtube.py — rifà il consenso OAuth con gli scope allargati aggiunti
il 21/08 a carica_youtube.py (SCOPES): youtube (fix titoli/privacy/duplicati),
youtube.force-ssl (commenti sui video già pubblicati), yt-analytics.readonly
(retention/CTR reali, non solo view count).

Prima di lanciarlo:
  1) Vai su https://console.cloud.google.com/apis/library , cerca
     "YouTube Analytics API" e clicca Enable (se non è già abilitata sul
     progetto — YouTube Data API v3 è già abilitata, questa è separata).
  2) Assicurati di avere un browser a disposizione: questo comando apre una
     pagina di login Google e aspetta che tu approvi i nuovi permessi.

USO
  python3 reauth_youtube.py
"""
import os, sys, shutil, argparse
from carica_youtube import get_service, HERE

if __name__ == "__main__":
    token_path = os.path.join(HERE, "youtube_token.json")
    backup_path = token_path + ".bak-pre-rescope-20260821"
    if os.path.exists(token_path):
        shutil.copy(token_path, backup_path)
        os.remove(token_path)
        print(f"Backup del token vecchio salvato in {backup_path}")
    args = argparse.Namespace(client=f"{HERE}/youtube_client_secret.json")
    print("Apro il browser per il consenso Google — approva TUTTI i permessi richiesti...")
    get_service(args)
    print("✓ Fatto. youtube_token.json aggiornato con i nuovi scope.")
