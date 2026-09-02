#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
leggi_commenti.py — NON FUNZIONA (verificato 2026-07-11): commentThreads.list ritorna
403 "insufficient authentication scopes" anche con youtube.readonly concesso. L'app OAuth
e' "In produzione" e non verificata da Google: sembra che qualsiasi endpoint sui commenti
sia bloccato a prescindere dallo scope dichiarato, non solo le scritture (vedi anche il
blocco di videos.delete con youtube.force-ssl, stesso giorno). Non ha senso riprovare con
scope diversi: la verifica formale dell'app (Google, settimane, serve dominio+privacy policy)
e' l'unica vera soluzione API-side, sproporzionata per uso personale.
Percorso scelto invece: leggere i commenti via BROWSER (YouTube Studio, come screenshot
manuali o lettura pagina), scrivere le risposte in-voce (vedi 03-agenti/guida-voce.md §9b),
pubblicarle in semi-automatico (precompilo io, click finale dell'autore) — stesso pattern
di IG/TikTok. Vedi [[calciovich-social-accounts]] e [[calciovich-bestseller-e-youtube-stato]].

Codice lasciato qui solo come riferimento/tentativo documentato, non e' in uso.
"""
import os, sys, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
CHANNEL_ID = "UCLPBYAv19aizEYX4MmXV7rA"
REPLIED_LOG = os.path.join(HERE, "output", "commenti-risposti.json")

def get_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    token_path = os.path.join(HERE, "youtube_token.json")
    creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        open(token_path, "w").write(creds.to_json())
    return build("youtube", "v3", credentials=creds)

def load_replied():
    try: return set(json.load(open(REPLIED_LOG, encoding="utf-8")))
    except Exception: return set()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=20)
    args = ap.parse_args()

    yt = get_service()
    replied = load_replied()

    videos = []
    req = yt.search().list(part="id", channelId=CHANNEL_ID, type="video", maxResults=50, order="date")
    while req:
        resp = req.execute()
        videos += [it["id"]["videoId"] for it in resp.get("items", [])]
        req = yt.search().list_next(req, resp)

    total_new = 0
    for vid in videos:
        try:
            resp = yt.commentThreads().list(part="snippet", videoId=vid, maxResults=args.max, order="time").execute()
        except Exception as e:
            continue
        items = resp.get("items", [])
        new_here = [c for c in items if c["id"] not in replied]
        if not new_here: continue
        vtitle = items[0]["snippet"]["topLevelComment"]["snippet"].get("videoId")
        print(f"\n=== video https://youtu.be/{vid} ===")
        for c in new_here:
            sn = c["snippet"]["topLevelComment"]["snippet"]
            print(f"  [{c['id']}] {sn['authorDisplayName']}: {sn['textDisplay']}")
            total_new += 1

    print(f"\nTotale commenti nuovi da valutare: {total_new}")
    if total_new == 0:
        print("Niente di nuovo — tutto già coperto.")

if __name__ == "__main__":
    main()
