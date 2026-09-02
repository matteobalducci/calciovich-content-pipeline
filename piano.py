#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
piano.py — il piano di monetizzazione di Calciovich: stato, previsioni e
OTTIMIZZAZIONE. E' il pezzo che permette al Coach di "prendere decisioni":
quando l'autore dice "raggiungi i 1.000 iscritti entro fine 2027", qui si
ricalcola la cadenza necessaria, si aggiorna piano.json e si scrive una
direttiva nel vault che l'orchestratore giornaliero deve rispettare.

Tutta la matematica e' esplicita e spiegabile: nessuna precisione finta.
"""
import os, json, datetime, re

HERE = os.path.dirname(os.path.abspath(__file__))
PIANO_PATH = os.path.join(HERE, "piano.json")
STATS_PATH = os.path.join(HERE, "app", "youtube-stats.json")
DATA_PATH = os.path.join(HERE, "app", "data.json")
DECISIONI_PATH = os.path.join(HERE, "vault", "playbook", "decisioni-coach.md")

DEFAULT_PIANO = {
    "targets": {"subs": 1000, "watch_hours": 4000},
    "target_date": "2027-01-03",
    "videos_needed": 107,
    "cadence": {"shorts_per_week": 5, "longform_per_month": 2, "free_shorts_per_week": 2},
    "assumptions": {"subs_per_short": 1.2, "subs_per_long": 6.0},
    "budget": {"usd_per_month": 5},
    "video": {"tier": "fast", "resolution": "480p", "duration": 12},
    # Il long-form e' il MOTORE DI MONETIZZAZIONE (solo le sue ore contano per le 4.000)
    # e si produce a costo zero: testo del libro + edge-tts + illustrazioni + musica propria.
    "longform": {"minutes": 15, "retention": 0.35, "free_material_minutes": 173},
}
MAX_SHORTS_PER_WEEK = 14  # 2/giorno: oltre non e' sostenibile ne' credibile
WEEKS_PER_MONTH = 4.33

# Listino PiAPI/Seedance al secondo — tenuto allineato a genera_video_ai.py
PRICE = {
    "mini": {"480p": 0.077, "720p": 0.154},
    "fast": {"480p": 0.088, "720p": 0.176},
    "pro":  {"480p": 0.110, "720p": 0.220, "1080p": 0.550},
}


def video_cost(video):
    """Costo di UNA clip AI con i parametri correnti."""
    per_sec = PRICE.get(video.get("tier", "fast"), {}).get(video.get("resolution", "480p"))
    if per_sec is None:
        return None
    return per_sec * video.get("duration", 12)


def ai_clips_per_week(cad):
    """Le clip che COSTANO: gli Short oltre quelli gratis (canonici gia' prodotti,
    personaggio in pipeline locale, compilation di materiale esistente)."""
    return max(0, cad.get("shorts_per_week", 0) - cad.get("free_shorts_per_week", 0))


def monthly_cost(cad, video):
    c = video_cost(video)
    return None if c is None else ai_clips_per_week(cad) * WEEKS_PER_MONTH * c


def max_ai_clips_per_week(budget_usd, video):
    """Quante clip AI a settimana stanno dentro il budget mensile."""
    c = video_cost(video)
    if not c:
        return MAX_SHORTS_PER_WEEK
    return int(budget_usd / (c * WEEKS_PER_MONTH))


def watch_hours_plan(p=None):
    """Le 4.000 ore YPP si fanno SOLO col long-form: le ore degli Short nel feed
    Shorts non contano (l'unica alternativa e' 10M views Short in 90 giorni).
    Qui si calcola quanto long-form serve — ed e' materiale a costo ZERO."""
    p = p or load_piano()
    lf = p.get("longform", DEFAULT_PIANO["longform"])
    target_h = p["targets"]["watch_hours"]
    minutes, retention = lf["minutes"], lf["retention"]
    hours_per_view = minutes * retention / 60.0
    views_needed = int(target_h / hours_per_view) if hours_per_view else None
    # quanto materiale gratuito abbiamo gia' in casa
    bank_min = lf.get("free_material_minutes", 0)
    episodes = int(bank_min / minutes) if minutes else 0
    return {
        "target_hours": target_h,
        "minutes_per_video": minutes,
        "retention": retention,
        "hours_per_view": hours_per_view,
        "views_needed": views_needed,
        "free_material_minutes": bank_min,
        "episodes_available": episodes,
        "hours_if_all_watched_once": round(bank_min * retention / 60.0, 1),
    }


def cost_options(cad, budget_usd, duration=12):
    """Alternative tier/risoluzione ordinate per costo, con quante clip/sett. permettono."""
    out = []
    for tier, res_map in PRICE.items():
        for res, per_sec in res_map.items():
            v = {"tier": tier, "resolution": res, "duration": duration}
            out.append({
                "tier": tier, "resolution": res, "per_clip": per_sec * duration,
                "monthly_at_current": monthly_cost(cad, v),
                "max_clips_week": max_ai_clips_per_week(budget_usd, v),
            })
    return sorted(out, key=lambda x: x["per_clip"])


def _today():
    return datetime.date.today()


def load_piano():
    p = json.loads(json.dumps(DEFAULT_PIANO))
    try:
        disk = json.load(open(PIANO_PATH, encoding="utf-8"))
        for k, v in disk.items():
            if isinstance(v, dict) and isinstance(p.get(k), dict):
                p[k].update(v)
            else:
                p[k] = v
    except Exception:
        pass
    return p


def save_piano(p, who="coach"):
    p["updated_at"] = _today().isoformat()
    p["updated_by"] = who
    json.dump(p, open(PIANO_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return p


def _stats():
    try:
        return json.load(open(STATS_PATH, encoding="utf-8"))
    except Exception:
        return {}


def current_subs():
    return (_stats().get("latest") or {}).get("subs")


def observed_rate():
    """Iscritti/giorno osservati sugli ultimi rilevamenti (None se non stimabile)."""
    hist = [h for h in _stats().get("history", []) if h.get("subs") is not None]
    if len(hist) < 2:
        return None
    recent = hist[-4:]
    a, b = recent[0], recent[-1]
    days = (datetime.date.fromisoformat(b["date"]) - datetime.date.fromisoformat(a["date"])).days
    if days <= 0:
        return None
    return (b["subs"] - a["subs"]) / days


def produced_count():
    """Video gia' prodotti (item del calendario con un file reale)."""
    try:
        data = json.load(open(DATA_PATH, encoding="utf-8"))
        return sum(1 for w in data.get("weeks", []) for it in w.get("items", []) if it.get("file"))
    except Exception:
        return 0


def eta_date(subs, rate, target):
    if subs is None or not rate or rate <= 0 or subs >= target:
        return None
    days = int((target - subs) / rate) + 1
    return _today() + datetime.timedelta(days=days)


def status():
    """Fotografia del piano: dove siamo, dove dovremmo essere, quando arriviamo."""
    p = load_piano()
    subs, tgt = current_subs(), p["targets"]["subs"]
    tdate = datetime.date.fromisoformat(p["target_date"])
    days_left = (tdate - _today()).days
    rate = observed_rate()
    eta = eta_date(subs, rate, tgt)
    missing = None if subs is None else max(0, tgt - subs)
    required_rate = (missing / days_left) if (missing and days_left > 0) else None
    cad = p["cadence"]
    return {
        "targets": p["targets"],
        "target_date": p["target_date"],
        "days_left": days_left,
        "subs": subs,
        "subs_missing": missing,
        "observed_rate": rate,
        "required_rate": required_rate,
        "eta_date": eta.isoformat() if eta else None,
        "on_track": (eta is not None and eta <= tdate),
        "videos_needed": p["videos_needed"],
        "videos_produced": produced_count(),
        "cadence": cad,
        "assumptions": p["assumptions"],
        "video": p.get("video", DEFAULT_PIANO["video"]),
        "budget_usd": p.get("budget", {}).get("usd_per_month", 20),
        "cost_per_clip": video_cost(p.get("video", DEFAULT_PIANO["video"])),
        "ai_clips_per_week": ai_clips_per_week(cad),
        "monthly_cost": monthly_cost(cad, p.get("video", DEFAULT_PIANO["video"])),
    }


def _weekly_subs(cad, asm):
    """Iscritti/settimana attesi da una cadenza, secondo le assunzioni correnti."""
    return cad["shorts_per_week"] * asm["subs_per_short"] + \
        (cad["longform_per_month"] / 4.33) * asm["subs_per_long"]


def parse_target_date(text):
    """Estrae una data obiettivo dal linguaggio naturale italiano.
    Riconosce: 'entro fine 2027', 'prima della fine del 2027', 'entro il 31/12/2027',
    'entro marzo 2027', '2027-12-31'."""
    t = text.lower()
    MESI = {"gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
            "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12}
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", t)
    if m:
        return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r"\b(" + "|".join(MESI) + r")\s+(\d{4})", t)
    if m:  # "entro marzo 2027" -> fine di quel mese
        y, mo = int(m.group(2)), MESI[m.group(1)]
        nxt = datetime.date(y + (mo == 12), (mo % 12) + 1, 1)
        return nxt - datetime.timedelta(days=1)
    m = re.search(r"\b(20\d{2})\b", t)
    if m:
        y = int(m.group(1))
        # "entro/prima della fine del <anno>" -> 31/12; "entro inizio <anno>" -> 31/01
        if re.search(r"inizio", t):
            return datetime.date(y, 1, 31)
        if re.search(r"met[àa]", t):
            return datetime.date(y, 6, 30)
        return datetime.date(y, 12, 31)
    return None


def optimize(target_date, note=""):
    """Ricalcola cadenza e video necessari per centrare target_date, applica e
    scrive la direttiva nel vault. Ritorna un riassunto spiegabile."""
    p = load_piano()
    if isinstance(target_date, str):
        target_date = datetime.date.fromisoformat(target_date)
    today = _today()
    days_left = (target_date - today).days
    if days_left <= 14:
        return {"ok": False, "error": "La data obiettivo è troppo vicina per costruirci un piano sopra."}

    subs, tgt = current_subs(), p["targets"]["subs"]
    if subs is None:
        return {"ok": False, "error": "Non ho ancora i numeri del canale: non posso ottimizzare senza sapere da dove partiamo."}

    missing = max(0, tgt - subs)
    weeks_left = days_left / 7.0
    required_rate = missing / days_left          # iscritti/giorno
    required_week = missing / weeks_left          # iscritti/settimana
    asm, old_cad = p["assumptions"], dict(p["cadence"])

    # I long-form li teniamo (servono per le 4.000 ore): il gap lo chiudono gli Short.
    long_contrib = (old_cad["longform_per_month"] / 4.33) * asm["subs_per_long"]
    shorts_needed = max(0.0, (required_week - long_contrib) / max(asm["subs_per_short"], 0.01))
    new_shorts = int(min(MAX_SHORTS_PER_WEEK, max(old_cad["shorts_per_week"], round(shorts_needed))))
    capped = shorts_needed > MAX_SHORTS_PER_WEEK

    # IL BUDGET COMANDA: le clip AI oltre quelle gratuite non possono sforare la spesa
    # mensile decisa dall'autore. Se serve piu' volume, si taglia qui.
    video = p.get("video", DEFAULT_PIANO["video"])
    budget_usd = p.get("budget", {}).get("usd_per_month", 20)
    free_week = old_cad.get("free_shorts_per_week", 2)
    max_ai = max_ai_clips_per_week(budget_usd, video)
    budget_capped = (new_shorts - free_week) > max_ai
    if budget_capped:
        new_shorts = free_week + max_ai

    new_cad = {"shorts_per_week": new_shorts,
               "longform_per_month": old_cad["longform_per_month"],
               "free_shorts_per_week": free_week}
    expected_week = _weekly_subs(new_cad, asm)
    reachable = expected_week * weeks_left >= missing

    produced = produced_count()
    total_per_week = new_shorts + new_cad["longform_per_month"] / 4.33
    new_needed = int(produced + round(total_per_week * weeks_left))

    p["target_date"] = target_date.isoformat()
    p["cadence"] = new_cad
    p["videos_needed"] = new_needed
    save_piano(p, who="coach:optimize")

    directive = (
        f"**Obiettivo:** {tgt} iscritti entro il {target_date.strftime('%d/%m/%Y')}.\n"
        f"  - Cadenza richiesta: **{new_shorts} Short/settimana** "
        f"(erano {old_cad['shorts_per_week']}) + **{new_cad['longform_per_month']} long-form/mese**.\n"
        f"  - Servono ~**{required_rate:.1f} iscritti/giorno** ({required_week:.0f}/settimana).\n"
        f"  - Totale video da produrre verso l'obiettivo: **{new_needed}**.\n"
        f"  - Priorita': motore iscritti = Short (gol impossibili + serie bingeabili); "
        f"i long-form restano per le 4.000 ore.\n"
    )
    if capped:
        directive += ("  - ⚠️ Il ritmo teorico necessario superava il tetto sostenibile "
                      f"({MAX_SHORTS_PER_WEEK}/settimana): impostato al massimo. "
                      "Per centrare la data serve anche alzare la resa per video (hook/packaging), "
                      "non solo il volume.\n")

    cost = monthly_cost(new_cad, video)
    directive += (f"  - 💸 Costo: ~**{cost:.0f}$/mese** di video AI "
                  f"({ai_clips_per_week(new_cad)} clip/sett. × ${video_cost(video):.2f} — "
                  f"{video['tier']} {video['resolution']} {video['duration']}s), "
                  f"dentro il budget di {budget_usd}$/mese.\n")
    if budget_capped:
        directive += (f"  - 🔒 Cadenza LIMITATA DAL BUDGET: servirebbero piu' Short, ma oltre "
                      f"{max_ai} clip AI/settimana si sfora i {budget_usd}$/mese. "
                      f"Per andare piu' veloce senza spendere di piu': aumentare i formati "
                      f"gratuiti (personaggio in pipeline locale, compilation, canonici) "
                      f"o alzare la resa per video (hook/packaging), non il volume a pagamento.\n")
    if note:
        directive += f"  - Nota dell'autore: {note}\n"

    _write_directive(target_date, directive)

    return {
        "ok": True, "target_date": target_date.isoformat(), "days_left": days_left,
        "subs": subs, "missing": missing,
        "required_rate": required_rate, "required_week": required_week,
        "old_cadence": old_cad, "new_cadence": new_cad,
        "expected_week": expected_week, "reachable": reachable, "capped": capped,
        "videos_needed": new_needed, "videos_produced": produced,
        "monthly_cost": cost, "budget_usd": budget_usd, "budget_capped": budget_capped,
        "max_ai_clips_week": max_ai, "cost_per_clip": video_cost(video), "video": video,
        "directive": directive,
    }


def _write_directive(target_date, directive):
    """Sostituisce la sezione 'Direttive attive' del vault e appende allo storico.
    Le sezioni si individuano con heading ancorati a inizio riga: cosi' il testo
    introduttivo che *nomina* le sezioni non viene mai tagliato per sbaglio."""
    try:
        txt = open(DECISIONI_PATH, encoding="utf-8").read()
    except Exception:
        txt = ("# 🧭 Decisioni & direttive attive del Coach\n\n"
               "## Direttive attive\n\n## Storico decisioni\n")
    today = _today().isoformat()
    block = f"## Direttive attive\n\n### Direttiva del {today}\n{directive}\n"
    entry = (f"- **{today}** — Ottimizzazione verso il "
             f"{target_date.strftime('%d/%m/%Y')}: cadenza e video necessari ricalcolati.\n")

    att = re.search(r"^## Direttive attive[ \t]*$", txt, re.M)
    sto = re.search(r"^## Storico decisioni[ \t]*$", txt, re.M)
    if att and sto and att.start() < sto.start():
        txt = txt[:att.start()] + block + "\n" + txt[sto.start():].rstrip("\n") + "\n" + entry
    else:
        txt = txt.rstrip("\n") + "\n\n" + block + "\n## Storico decisioni\n\n" + entry

    os.makedirs(os.path.dirname(DECISIONI_PATH), exist_ok=True)
    open(DECISIONI_PATH, "w", encoding="utf-8").write(txt)


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=1, default=str))
