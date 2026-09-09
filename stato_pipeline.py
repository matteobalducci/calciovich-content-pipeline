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
METRICHE_STORICO_PATH = os.path.join(HERE, "output", "metriche-video-storico.json")
ANALYTICS_CONSENT_STATUS_PATH = os.path.join(HERE, "output", "analytics-consent-status.json")
FINESTRE_FISSE_PATH = os.path.join(HERE, "output", "metriche-finestre-fisse.json")
LAUNCHAGENTS = os.path.expanduser("~/Library/LaunchAgents")
UPLOAD_PLIST_INSTALLED = os.path.join(LAUNCHAGENTS, "com.calciovich.upload.plist")

METRICHE_WARN_HOURS = 36
METRICHE_ERROR_HOURS = 72
FINESTRE_WARN_HOURS = 36
FINESTRE_ERROR_HOURS = 72

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


def _metriche_freshness_flag():
    """Guardia di freschezza per raccogli_metriche_video.py. Calcolo a grana-ORE: e' un tipo di
    controllo nuovo, non un riuso del confronto a grana-data usato sopra per
    last_activity — a cadenza di 4 raccolte/giorno un controllo giornaliero
    nasconderebbe un buco di quasi 24h prima di segnalarlo.

    Verificata indipendentemente dal trigger di raccolta stesso: questa funzione
    e' letta da app_server.py, che gira su un LaunchAgent separato e permanente
    (KeepAlive/RunAtLoad), non innescato dalla stessa catena che raccoglie i
    dati — la classe di buco che questa guardia deve rilevare e' proprio "il
    trigger di raccolta non e' mai scattato", che una guardia agganciata allo
    stesso trigger non potrebbe vedere."""
    try:
        records = json.load(open(METRICHE_STORICO_PATH, encoding="utf-8")).get("records", [])
    except Exception:
        records = []

    if not records:
        return {"level": "warn", "text": "Nessuna metrica video ancora raccolta "
                "(raccogli_metriche_video.py non ha ancora prodotto uno storico)."}

    latest = max((r.get("snapshot_at") for r in records if r.get("snapshot_at")), default=None)
    if latest is None:
        return None
    try:
        last_dt = datetime.datetime.fromisoformat(latest)
    except ValueError:
        return None

    hours = (datetime.datetime.now() - last_dt).total_seconds() / 3600
    if hours >= METRICHE_ERROR_HOURS:
        return {"level": "error",
                "text": f"Nessuna raccolta metriche video da {hours:.0f} ore "
                        f"(ultima: {latest}) — controlla il LaunchAgent "
                        f"com.calciovich.youtubestats."}
    if hours >= METRICHE_WARN_HOURS:
        return {"level": "warn",
                "text": f"Raccolta metriche video in ritardo: ultima rilevazione "
                        f"{hours:.0f} ore fa ({latest})."}
    return None


def _analytics_consent_flag():
    """Guardia di consenso per raccogli_finestre_fisse.py (Fase 2 del layer
    analytics). Non un riuso di _metriche_freshness_flag(): risponde a una domanda diversa — "serve un
    consenso umano", non "il dato è vecchio". Il token Analytics è dedicato e
    isolato da carica_youtube.py: se scade o non è mai stato concesso, nessun altro
    script della pipeline lo rinnova da solo (a differenza del token condiviso di
    Fase 1, che si autoripara al prossimo consenso presidiato di carica_youtube.py).

    Tre stati, deliberatamente non simmetrici: file assente → nessun flag (la Fase 2
    non ha ancora girato una volta — non è un errore da
    segnalare, la guardia di freschezza generica già copre "nessun dato mai" senza
    bisogno di duplicarlo qui); consent_needed False → nessun flag; consent_needed
    True → il flag error sotto."""
    try:
        status = json.load(open(ANALYTICS_CONSENT_STATUS_PATH, encoding="utf-8"))
    except Exception:
        return None

    if not status.get("consent_needed"):
        return None
    return {"level": "error",
            "text": "Serve un nuovo consenso YouTube Analytics — rilancia "
                    "youtube_analytics_auth.py da un terminale con browser."}


def _finestre_fisse_freshness_flag():
    """Guardia di freschezza per raccogli_finestre_fisse.py (Fase 2) — stesso
    principio di _metriche_freshness_flag(), file diverso. Senza questa, un guasto
    silenzioso (metriche-finestre-fisse.json corrotto, o errori di rete ripetuti
    durante il refresh del token che fanno saltare la raccolta senza toccare il
    consenso) non produce alcun segnale: il fix statistico che e' l'intero scopo
    della Fase 2 smetterebbe di funzionare senza che nessuno se ne accorga,
    tornando silenziosamente al confronto lifetime-vs-lifetime che doveva
    correggere. raccogli_finestre_fisse.py scrive "last_run_at" ad ogni esecuzione
    riuscita, indipendentemente da quanti valori sono stati aggiornati."""
    try:
        last_run_at = json.load(open(FINESTRE_FISSE_PATH, encoding="utf-8")).get("last_run_at")
    except Exception:
        last_run_at = None

    if last_run_at is None:
        return {"level": "warn", "text": "Nessuna raccolta finestre fisse ancora "
                "completata (raccogli_finestre_fisse.py non ha ancora prodotto uno "
                "storico)."}

    try:
        last_dt = datetime.datetime.fromisoformat(last_run_at)
    except ValueError:
        return None

    hours = (datetime.datetime.now() - last_dt).total_seconds() / 3600
    if hours >= FINESTRE_ERROR_HOURS:
        return {"level": "error",
                "text": f"Nessuna raccolta finestre fisse riuscita da {hours:.0f} ore "
                        f"(ultima: {last_run_at}) — controlla errori di rete o il "
                        f"token YouTube Analytics."}
    if hours >= FINESTRE_WARN_HOURS:
        return {"level": "warn",
                "text": f"Raccolta finestre fisse in ritardo: ultima riuscita "
                        f"{hours:.0f} ore fa ({last_run_at})."}
    return None


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

    metriche_flag = _metriche_freshness_flag()
    if metriche_flag:
        flags.append(metriche_flag)

    consent_flag = _analytics_consent_flag()
    if consent_flag:
        flags.append(consent_flag)

    finestre_flag = _finestre_fisse_freshness_flag()
    if finestre_flag:
        flags.append(finestre_flag)

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
