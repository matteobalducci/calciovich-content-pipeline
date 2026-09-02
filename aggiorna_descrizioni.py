#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aggiorna_descrizioni.py — aggiunge il link d'acquisto alle descrizioni dei video
GIÀ pubblicati sul canale.

PERCHÉ ESISTE
  I due video più visti del canale valgono da soli 93.000 visualizzazioni e la
  loro descrizione diceva "link in bio" — che su YouTube non è cliccabile e non
  porta da nessuna parte. Il link viene ora messo automaticamente sui NUOVI
  caricamenti (carica_youtube.desc_con_link), ma i video già online restano
  senza: sono l'88% del traffico del canale, senza alcun percorso d'acquisto.

REQUISITO
  Serve lo scope OAuth `youtube` o `youtube.force-ssl`: `videos.update` non è
  coperto da `youtube.upload`. Se il token in cache ha solo upload+readonly
  (com'era fino al 02/09), lancia prima:

      python3 reauth_youtube.py

  che rifà il consenso con gli scope allargati già dichiarati in SCOPES.

USO
  python3 aggiorna_descrizioni.py --dry-run     # mostra cosa cambierebbe
  python3 aggiorna_descrizioni.py               # applica davvero
  python3 aggiorna_descrizioni.py --only VIDEOID [VIDEOID ...]

L'operazione è idempotente: un video che ha già il link viene saltato.
`videos.update` richiede di rimandare indietro l'intero snippet, quindi lo
script lo rilegge sempre prima di scrivere, per non azzerare titolo, tag o
categoria.
"""
import argparse
import sys

from carica_youtube import (
    CALCIOVICH_CHANNEL_ID,
    LIBRO_URL,
    desc_con_link,
    get_service,
)


def canale_uploads(youtube):
    """Playlist 'uploads' del canale, con la guardia anti-canale-sbagliato."""
    ch = youtube.channels().list(part="contentDetails,id", mine=True).execute()
    item = ch["items"][0]
    if item["id"] != CALCIOVICH_CHANNEL_ID:
        sys.exit(
            f"⚠️  Il token risolve sul canale {item['id']}, non su Calciovich "
            f"({CALCIOVICH_CHANNEL_ID}). Rifai il consenso scegliendo il Brand "
            f"Account giusto: python3 reauth_youtube.py"
        )
    return item["contentDetails"]["relatedPlaylists"]["uploads"]


def tutti_i_video(youtube, playlist_id):
    """Tutti i video del canale, paginando (non solo i primi 50)."""
    page = None
    while True:
        resp = youtube.playlistItems().list(
            part="snippet", playlistId=playlist_id, maxResults=50, pageToken=page
        ).execute()
        for it in resp.get("items", []):
            yield it["snippet"]["resourceId"]["videoId"], it["snippet"]["title"]
        page = resp.get("nextPageToken")
        if not page:
            return


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="mostra cosa cambierebbe senza scrivere niente")
    ap.add_argument("--only", nargs="+", metavar="VIDEOID",
                    help="limita a questi video id")
    ap.add_argument("--client", default=None, help="path del client_secret OAuth")
    args = ap.parse_args()
    if args.client is None:
        import os
        from carica_youtube import HERE
        args.client = os.path.join(HERE, "youtube_client_secret.json")

    youtube = get_service(args)
    uploads = canale_uploads(youtube)

    ids = list(tutti_i_video(youtube, uploads))
    if args.only:
        wanted = set(args.only)
        ids = [(vid, t) for vid, t in ids if vid in wanted]
    print(f"📼 {len(ids)} video sul canale"
          f"{' (filtrati)' if args.only else ''}"
          f"{' — DRY RUN' if args.dry_run else ''}\n")

    aggiornati = saltati = falliti = 0
    for vid, titolo in ids:
        # Rileggo lo snippet completo: videos.update sostituisce l'intera parte
        # 'snippet', quindi mandarne uno parziale cancellerebbe tag e categoria.
        resp = youtube.videos().list(part="snippet", id=vid).execute()
        if not resp.get("items"):
            print(f"  ⚠️  {vid}: non trovato, salto")
            falliti += 1
            continue
        snippet = resp["items"][0]["snippet"]
        vecchia = snippet.get("description", "")
        nuova = desc_con_link(vecchia)

        if nuova == vecchia:
            saltati += 1
            continue

        print(f"  • {vid}  {titolo[:60]}")
        if args.dry_run:
            coda = nuova[len(vecchia):].strip() or "(link sostituito a 'link in bio')"
            print(f"      + {coda[:120]}")
            aggiornati += 1
            continue

        snippet["description"] = nuova[:4990]
        try:
            youtube.videos().update(
                part="snippet", body={"id": vid, "snippet": snippet}
            ).execute()
            aggiornati += 1
            print("      ✓ aggiornato")
        except Exception as e:
            msg = str(e)
            falliti += 1
            print(f"      ❌ {msg[:180]}")
            if "insufficientPermissions" in msg or "insufficient" in msg.lower():
                sys.exit(
                    "\n⛔ Il token non ha lo scope per modificare i video.\n"
                    "   Lancia:  python3 reauth_youtube.py\n"
                    "   e approva TUTTI i permessi, poi rilancia questo script."
                )
            if "quota" in msg.lower():
                print("      Quota giornaliera esaurita: riprendi domani.")
                break

    print(f"\n{'Sarebbero aggiornati' if args.dry_run else 'Aggiornati'}: {aggiornati}"
          f" · già a posto: {saltati} · falliti: {falliti}")
    print(f"Link usato: {LIBRO_URL}")
    return 1 if falliti else 0


if __name__ == "__main__":
    sys.exit(main())
