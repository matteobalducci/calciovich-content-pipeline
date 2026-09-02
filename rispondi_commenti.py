#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rispondi_commenti.py — engagement Instagram in prima persona: legge i commenti
nuovi sui media di @calciovich.official e pubblica risposte (scritte dal task
giornaliero nella voce di Calciovich, approvate dall'autore prima dell'invio).

Le risposte NON le scrive questo script: lui è solo il braccio (lettura +
invio + registro). La voce sta in 03-agenti/guida-voce.md e nel task.

USO
  python3 rispondi_commenti.py --list                     # commenti nuovi (non gestiti)
  python3 rispondi_commenti.py --reply COMMENT_ID "testo" # rispondi a un commento
  python3 rispondi_commenti.py --mark COMMENT_ID          # segna gestito senza rispondere
Registro: output/ig-comments-log.json (id commento -> esito).
"""
import os, sys, json, time, argparse
import urllib.request, urllib.parse, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "output", "ig-comments-log.json")
GRAPH = "https://graph.instagram.com/v21.0"

def load_cfg(path):
    return json.load(open(path, encoding="utf-8"))

def load_log():
    try: return json.load(open(LOG, encoding="utf-8"))
    except Exception: return {}

def save_log(d):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    json.dump(d, open(LOG, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def graph_get(path, params):
    url = f"{GRAPH}/{path}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url) as r:
        return json.load(r)

def graph_post(path, params):
    url = f"{GRAPH}/{path}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:400]}")

def list_new(cfg):
    tok = cfg["meta"]["access_token"]
    uid = cfg["meta"]["instagram_business_account_id"]
    me = cfg["meta"].get("instagram_username", "calciovich.official")
    log = load_log()
    media = graph_get(f"{uid}/media", {
        "fields": "id,caption,timestamp", "limit": 20, "access_token": tok})["data"]
    new = []
    for m in media:
        try:
            comments = graph_get(f"{m['id']}/comments", {
                "fields": "id,text,username,timestamp,like_count",
                "limit": 50, "access_token": tok})["data"]
        except Exception:
            continue
        for c in comments:
            if c["id"] in log or c.get("username") == me:
                continue
            cap = (m.get("caption") or "").split("\n")[0][:50]
            new.append({**c, "media_id": m["id"], "media_hook": cap})
    return new

def main():
    ap = argparse.ArgumentParser(description="Engagement commenti Instagram (lettura/risposta/registro).")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--reply", nargs=2, metavar=("COMMENT_ID", "TESTO"))
    ap.add_argument("--mark", metavar="COMMENT_ID")
    ap.add_argument("--config", default=os.path.join(HERE, "meta_config.json"))
    args = ap.parse_args()
    cfg = load_cfg(args.config)

    if args.list:
        new = list_new(cfg)
        if not new:
            print("Nessun commento nuovo."); return
        print(f"{len(new)} commenti nuovi:")
        for c in new:
            print(f"  [{c['id']}] @{c.get('username','?')} su «{c['media_hook']}…»")
            print(f"      \"{c['text']}\"  ({c['timestamp'][:16]})")
        return

    if args.reply:
        cid, testo = args.reply
        resp = graph_post(f"{cid}/replies", {
            "message": testo, "access_token": cfg["meta"]["access_token"]})
        log = load_log()
        log[cid] = {"replied": resp.get("id"), "text": testo,
                    "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        save_log(log)
        print(f"  ✓ risposta pubblicata (id {resp.get('id')})")
        return

    if args.mark:
        log = load_log()
        log[args.mark] = {"skipped": True, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
        save_log(log)
        print("  ✓ segnato come gestito")
        return

    ap.print_help()

if __name__ == "__main__":
    main()
