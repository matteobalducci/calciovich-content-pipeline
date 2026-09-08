#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reauth_youtube.py — rifà il consenso OAuth di youtube_token.json con gli scope
attuali di carica_youtube.py (SCOPES): solo youtube.upload + youtube.readonly.
Usalo dopo aver sostituito youtube_client_secret.json con le credenziali di un
nuovo progetto GCP, o se il token va cancellato e ri-consentito per qualsiasi
altro motivo.

Assicurati di avere un browser a disposizione: questo comando apre una pagina
di login Google e aspetta che tu approvi i permessi.

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
