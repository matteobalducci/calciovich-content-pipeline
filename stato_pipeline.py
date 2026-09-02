#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stato_pipeline.py — stato giornaliero della pipeline di pubblicazione automatica
(coda contenuti AI in output/ai-content-queue.json + retry Instagram in corso).
Letto da app_server.py per il pannello "Stato pubblicazioni automatiche" nel tab
Coach: cosi' non serve piu' aprire la sessione Claude "calciovich daily content"
solo per sapere se oggi e' andato tutto bene.
"""
import os, json, glob, plistlib, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_PATH = os.path.join(HERE, "output", "ai-content-queue.json")
IG_UPLOADS_PATH = os.path.join(HERE, "output", "instagram-uploads.json")
KNOWN_ISSUES_PATH = os.path.join(HERE, "output", "known-issues.json")
LAUNCHAGENTS = os.path.expanduser("~/Library/LaunchAgents")
UPLOAD_PLIST_INSTALLED = os.path.join(LAUNCHAGENTS, "com.calciovich.upload.plist")

PLATFORM_KEYS = {"youtube": "youtube", "instagram": "instagram_media_id", "tiktok": "tiktok_publish_id"}


def _today():
    return datetime.date.today().isoformat()


def _load_queue_items():
    try:
        return json.load(open(QUEUE_PATH, encoding="utf-8")).get("items", [])
    except Exception:
        return []


def _ig_published_ids():
    """Solo cio' che e' DAVVERO pubblicato.

    BUGFIX 02/09 (audit Codex): il cruscotto contava come pubblicato qualunque
    record presente nel registro, inclusi i 'pending' (esito ignoto) e i
    'failed' (da ritentare). Nascondeva quindi proprio gli item che avevano
    bisogno di attenzione.
    """
    try:
        import upload_registry
        reg = upload_registry.Registry(IG_UPLOADS_PATH)
        ids = {k.split("-")[0] for k, v in reg.data.items()
               if upload_registry.state_of(v) == upload_registry.CONFIRMED}
        reg.close()
        return ids
    except Exception:
        return set()


def _ig_needs_attention():
    """Item bloccati in pending o falliti, che il cruscotto deve mostrare."""
    try:
        import upload_registry
        reg = upload_registry.Registry(IG_UPLOADS_PATH)
        out = {k: upload_registry.state_of(v) for k, v in reg.data.items()
               if upload_registry.state_of(v) != upload_registry.CONFIRMED}
        reg.close()
        return out
    except Exception:
        return {}


def _known_issues():
    """Debiti di canone/qualita' noti e non bloccanti (es. illustrazioni fuori
    canone gia' in onda) — registrati a mano in output/known-issues.json cosi'
    restano visibili nel pannello finche' non vengono risolti."""
    try:
        return json.load(open(KNOWN_ISSUES_PATH, encoding="utf-8")).get("items", [])
    except Exception:
        return []


def _ig_retry_jobs():
    """Job di retry IG ancora installati (auto-instagram-once.sh li rimuove appena
    l'esito e' noto: se un plist esiste ANCORA per uno short_id gia' pubblicato,
    e' solo scoria orfana — non un problema — quindi va escluso qui."""
    published = _ig_published_ids()
    out = []
    for p in glob.glob(os.path.join(LAUNCHAGENTS, "com.calciovich.ig.*.plist")):
        label = os.path.splitext(os.path.basename(p))[0]
        short_id = label[len("com.calciovich.ig."):]
        if short_id in published:
            continue
        attempt = 1
        try:
            args = plistlib.load(open(p, "rb")).get("ProgramArguments", [])
            if len(args) >= 5:
                attempt = int(args[4])
        except Exception:
            pass
        out.append({"shortId": short_id, "plistLabel": label, "attempt": attempt})
    return out


def compute_status():
    today = _today()
    items = _load_queue_items()

    published_partial, rendered_unpub = [], []
    last_activity = None

    for it in items:
        qd = it.get("queued_date")
        pub = it.get("published") or {}
        pdate = pub.get("date")
        for d in (qd, pdate):
            if d and (last_activity is None or d > last_activity):
                last_activity = d
        if it.get("status") == "published" and pdate == today:
            has = {plat: bool(pub.get(key)) for plat, key in PLATFORM_KEYS.items()}
            missing = [plat for plat, ok in has.items() if not ok]
            if missing:
                published_partial.append({"id": it["id"], "era": it.get("era"), "missing": missing})
        elif it.get("status") == "rendered":
            rendered_unpub.append({
                "id": it["id"], "era": it.get("era"),
                "notes": it.get("notes") or "", "queued_date": qd,
            })

    pending_count = sum(1 for it in items if it.get("status") == "pending")
    ig_retries = _ig_retry_jobs()
    auto_upload_active = os.path.exists(UPLOAD_PLIST_INSTALLED)

    flags = []
    if last_activity is None:
        flags.append({"level": "warn", "text": "Nessuna attività registrata nella coda contenuti AI."})
    elif last_activity < today:
        days = (datetime.date.fromisoformat(today) - datetime.date.fromisoformat(last_activity)).days
        if days >= 1:
            flags.append({
                "level": "error" if days >= 2 else "warn",
                "text": f"Nessuna generazione/pubblicazione oggi — l'ultima attività registrata è del {last_activity} ({days} giorno/i fa).",
            })
    for r in rendered_unpub:
        flags.append({"level": "warn", "text": f"«{r['id']}» è renderizzato ma non ancora pubblicato — controlla le note prima di lanciarlo."})
    for p in published_partial:
        ok_plats = [pl for pl in PLATFORM_KEYS if pl not in p["missing"]]
        flags.append({"level": "error", "text": f"«{p['id']}» pubblicato solo su {', '.join(ok_plats) or 'nessuna piattaforma'} — mancano: {', '.join(p['missing'])}."})
    for r in ig_retries:
        if r["attempt"] <= 1:
            flags.append({"level": "warn", "text": f"Instagram «{r['shortId']}» non risulta ancora pubblicato (il job automatico non è mai scattato — verifica se il Mac era spento/in stop all'ora programmata)."})
        else:
            flags.append({"level": "warn", "text": f"Instagram «{r['shortId']}» ancora in retry automatico (tentativo {r['attempt']}/8)."})

    known_issues = _known_issues()
    for k in known_issues:
        flags.append({"level": k.get("level", "warn"), "text": k.get("text", "")})

    return {
        "generatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "today": today,
        "lastActivity": last_activity,
        "pendingCount": pending_count,
        "renderedUnpublished": rendered_unpub,
        "publishedTodayPartial": published_partial,
        "instagramRetries": ig_retries,
        "autoUploadActive": auto_upload_active,
        "knownIssues": known_issues,
        "flags": flags,
    }


if __name__ == "__main__":
    print(json.dumps(compute_status(), ensure_ascii=False, indent=1))
