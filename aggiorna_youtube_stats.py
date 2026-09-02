#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
aggiorna_youtube_stats.py — rileva iscritti/views/video del canale (via il
token OAuth GIA' autorizzato, youtube_token.json, scope readonly, lo stesso
di carica_youtube.py) e follower Instagram (via meta_config.json, campo
"followers_count" scoperto disponibile il 01/08 — prima non veniva letto) e
scrive app/youtube-stats.json, che la dashboard legge con un semplice
fetch(). Nessuna API key da incollare nel browser, nessuna nuova
autorizzazione: riusa le credenziali che esistono già.

Girato periodicamente da un LaunchAgent (com.calciovich.youtubestats.plist,
ogni 6 ore) così i KPI nella sezione Obiettivo restano sempre aggiornati
senza intervento manuale.

**Archivio storico completo (aggiunto 21/08)**: questo file è il seme dei
dati per la futura dashboard (Google/Microsoft) — "day 0 a oggi". Lo storico
NON viene più tagliato a 180 giorni: si tiene tutto, per sempre.

USO
  python3 aggiorna_youtube_stats.py
"""
import os, sys, json, time

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(HERE, "youtube_token.json")
META_CONFIG_PATH = os.path.join(HERE, "meta_config.json")
OUT_PATH = os.path.join(HERE, "app", "youtube-stats.json")
# Canale Calciovich esplicito — NON usare mine=True (scoperto 01/09: dopo il
# rescope del 21/08 risolveva sul canale personale dell'autore invece del
# Brand Account Calciovich, corrompendo silenziosamente questo storico dal
# 26/08 al 31/08 con i numeri del canale sbagliato). Vedi carica_youtube.py
# per la stessa guardia lato upload.
CALCIOVICH_CHANNEL_ID = "UCLPBYAv19aizEYX4MmXV7rA"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]

def fetch_ig_followers():
    """Ritorna il numero di follower IG, o None se non disponibile (non blocca lo YouTube)."""
    try:
        import urllib.request
        cfg = json.load(open(META_CONFIG_PATH, encoding="utf-8"))["meta"]
        url = (f"https://graph.instagram.com/v21.0/{cfg['instagram_business_account_id']}"
               f"?fields=followers_count&access_token={cfg['access_token']}")
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read())["followers_count"]
    except Exception as e:
        print(f"  (follower IG non disponibili in questo giro: {e})", file=sys.stderr)
        return None

def main():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    import googleapiclient.discovery

    if not os.path.exists(TOKEN_PATH):
        sys.exit(f"Manca {TOKEN_PATH} — va autorizzato almeno una volta con carica_youtube.py.")

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        open(TOKEN_PATH, "w").write(creds.to_json())

    yt = googleapiclient.discovery.build("youtube", "v3", credentials=creds)
    ch = yt.channels().list(part="statistics,snippet", id=CALCIOVICH_CHANNEL_ID).execute()["items"][0]
    st = ch["statistics"]
    today = time.strftime("%Y-%m-%d")
    ig_followers = fetch_ig_followers()

    snapshot = {
        "date": today,
        "title": ch["snippet"]["title"],
        "subs": None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0)),
        "views": int(st.get("viewCount", 0)),
        "videos": int(st.get("videoCount", 0)),
        "ig_followers": ig_followers,
    }

    try:
        out = json.load(open(OUT_PATH, encoding="utf-8"))
    except Exception:
        out = {"history": []}
    hist = out.get("history", [])
    hist = [h for h in hist if h["date"] != today]  # una rilevazione al giorno, l'ultima vince
    hist.append(snapshot)
    hist.sort(key=lambda h: h["date"])
    out["history"] = hist  # archivio completo, mai tagliato (vedi nota in testa al file)
    out["latest"] = snapshot
    out["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    json.dump(out, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"✓ {snapshot['subs']} iscritti · {snapshot['views']} views · {snapshot['videos']} video · "
          f"{ig_followers if ig_followers is not None else '?'} follower IG  -> {OUT_PATH}")

if __name__ == "__main__":
    main()
