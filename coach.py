#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coach.py — il cervello del Coach del cruscotto Calciovich.

Risponde in italiano a domande sullo stato delle pubblicazioni e sugli obiettivi,
e soprattutto PRENDE DECISIONI: se l'autore dice "ottimizza per arrivare a 1.000
iscritti prima della fine del 2027", ricalcola il piano (piano.py), aggiorna
piano.json e scrive la direttiva nel vault.

Tutto a regole, gratis, senza chiamate a modelli esterni. La conoscenza di brand e
strategia arriva dal vault Obsidian (vault/), che cresce nel tempo.
"""
import os, json, glob, re, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(HERE, "app", "data.json")
QUEUE_PATH = os.path.join(HERE, "output", "ai-content-queue.json")
VAULT = os.path.join(HERE, "vault")

import piano as piano_mod
import stato_pipeline

GIORNI = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
MESI = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
STOPWORDS = set("il lo la i gli le un uno una di a da in con su per tra fra e o ma se che chi cosa come "
                "quando dove perche perché quale quali del della dei delle al alla ai alle dal dalla nel "
                "nella sul sulla mi ti ci vi si non piu più molto sono è ho hai ha abbiamo avete hanno "
                "essere fare dire dimmi dammi voglio vorrei puoi può parlami spiegami".split())


def _today():
    return datetime.date.today()


def _fmt_date(iso):
    d = datetime.date.fromisoformat(iso[:10])
    return f"{d.day} {MESI[d.month - 1]}"


def _load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return default


# ------------------------------------------------------------------ contesto
def todays_releases():
    """Cosa esce oggi e a che ora: calendario + programmazioni YouTube + coda AI."""
    today = _today().isoformat()
    out = []
    data = _load(DATA_PATH, {})
    for w in data.get("weeks", []):
        for it in w.get("items", []):
            yt = it.get("yt") or {}
            pub_at = yt.get("publishAt")
            sched_today = bool(pub_at and pub_at[:10] == today)
            if it.get("data") == today or sched_today:
                ora = None
                if pub_at and sched_today:
                    m = re.search(r"T(\d{2}):(\d{2})", pub_at)
                    if m:
                        ora = f"{m.group(1)}:{m.group(2)}"
                out.append({
                    "titolo": it.get("titolo"), "formato": it.get("formato"),
                    "pronto": bool(it.get("file")), "ora": ora,
                    "url": yt.get("url"), "privacy": yt.get("privacy"),
                    "piattaforme": it.get("piattaforme") or [],
                    "categoria": it.get("categoria"),
                })
    for it in _load(QUEUE_PATH, {}).get("items", []):
        pub = it.get("published") or {}
        if it.get("queued_date") == today or pub.get("date") == today:
            out.append({
                "titolo": it.get("id"), "formato": "Short (gol AI)",
                "pronto": it.get("status") in ("rendered", "published"),
                "ora": None, "url": pub.get("youtube"),
                "privacy": "public" if pub.get("youtube") else None,
                "piattaforme": ["YouTube", "Instagram", "TikTok"],
                "categoria": it.get("pillar"),
                "pubblicato": it.get("status") == "published",
            })
    return out


def context():
    return {
        "plan": piano_mod.status(),
        "pipeline": stato_pipeline.compute_status(),
        "today": todays_releases(),
        "date": _today().isoformat(),
    }


# ------------------------------------------------------------------ vault
def vault_notes():
    notes = []
    for p in glob.glob(os.path.join(VAULT, "**", "*.md"), recursive=True):
        try:
            txt = open(p, encoding="utf-8").read()
        except Exception:
            continue
        notes.append({"path": p, "name": os.path.splitext(os.path.basename(p))[0], "text": txt})
    return notes


def vault_search(question, limit=1):
    """Cerca nel vault la nota piu' pertinente alla domanda."""
    words = {w for w in re.findall(r"[a-zàèéìòù]{4,}", question.lower()) if w not in STOPWORDS}
    if not words:
        return []
    scored = []
    for n in vault_notes():
        low = n["text"].lower()
        name = n["name"].lower().replace("-", " ")
        score = sum(low.count(w) for w in words) + 5 * sum(1 for w in words if w in name)
        if score:
            scored.append((score, n))
    scored.sort(key=lambda x: -x[0])
    return [n for _, n in scored[:limit]]


def _note_digest(note, max_lines=10):
    """Titolo + prime righe utili di una nota, senza fronzoli."""
    lines, out, title = note["text"].splitlines(), [], note["name"]
    for l in lines:
        s = l.strip()
        if s.startswith("# "):
            title = s[2:].strip()
            continue
        if not s or s.startswith(("Collega:", "> ", "---")):
            continue
        out.append(s)
        if len(out) >= max_lines:
            break
    return title, "\n".join(out)


# ------------------------------------------------------------------ risposte
def _eta_sentence(pl):
    tgt, subs = pl["targets"]["subs"], pl["subs"]
    if subs is None:
        return "Non ho ancora i numeri del canale: appena arriva il primo aggiornamento YouTube ti dico i tempi."
    if subs >= tgt:
        return f"🎉 Sei a {subs} iscritti: soglia dei {tgt} raggiunta."
    tdate = datetime.date.fromisoformat(pl["target_date"])
    s = [f"Iscritti: **{subs}/{tgt}** (ne mancano {pl['subs_missing']}). Ore di visione: obiettivo **{pl['targets']['watch_hours']}**."]
    if pl["eta_date"]:
        eta = datetime.date.fromisoformat(pl["eta_date"])
        s.append(f"Al ritmo attuale (~{pl['observed_rate']:.2f} iscritti/giorno) li raggiungi verso il **{eta.strftime('%d/%m/%Y')}**.")
        s.append("✅ In linea con l'obiettivo." if eta <= tdate else
                 f"⚠️ È **oltre** la data obiettivo ({tdate.strftime('%d/%m/%Y')}). Dimmi entro quando vuoi arrivarci e riscrivo il piano.")
    else:
        s.append("Non ho ancora abbastanza rilevazioni per stimare il ritmo reale (servono più giorni di dati).")
    if pl["required_rate"]:
        s.append(f"Per centrare il {tdate.strftime('%d/%m/%Y')} servirebbero ~**{pl['required_rate']:.1f} iscritti/giorno**.")
    s.append(f"Cadenza pianificata: {pl['cadence']['shorts_per_week']} Short/settimana + {pl['cadence']['longform_per_month']} long-form/mese "
             f"· video prodotti {pl['videos_produced']}/{pl['videos_needed']}.")
    w = piano_mod.watch_hours_plan()
    s.append(f"⚠️ Le **4.000 ore** si fanno solo col **long-form** (le ore degli Short non contano): "
             f"servono ~{w['views_needed']:,} views su video da {w['minutes_per_video']} min. "
             f"È il contenuto che costa **€0** — priorità lì.".replace(",", "."))
    return "\n".join(s)


def _today_sentence(ctx):
    items, d = ctx["today"], _today()
    head = f"**{GIORNI[d.weekday()]} {d.day} {MESI[d.month - 1]}** — "
    if not items:
        return head + "oggi non è programmata nessuna uscita."
    lines = [head + f"in programma {len(items)} uscit{'a' if len(items) == 1 else 'e'}:"]
    for it in items:
        bits = [f"• **{it['titolo']}**"]
        if it.get("pubblicato") or (it.get("privacy") == "public" and it.get("url")):
            bits.append("— ✅ già pubblicato")
        elif it["ora"]:
            bits.append(f"— esce da solo alle **{it['ora']}** su YouTube ⏰")
        elif it["pronto"]:
            bits.append("— pronto, in attesa di pubblicazione")
        else:
            bits.append(f"— 🔧 da produrre ({it.get('categoria') or it['formato']})")
        lines.append(" ".join(bits))
    return "\n".join(lines)


def _pipeline_sentence(st):
    flags = st.get("flags") or []
    if not flags:
        return f"✅ Tutto ok: nessun problema rilevato. Ultima attività {_fmt_date(st['lastActivity'])}." if st.get("lastActivity") \
            else "✅ Nessun problema rilevato."
    return "\n".join(["Ci sono cose da guardare:"] + [f"• {f['text']}" for f in flags])


def _actions_for(st):
    """Bottoni per rilanciare i flow non andati a buon fine."""
    acts, plat = [], {"youtube": ("yt-only", "YouTube"), "instagram": ("ig-only", "Instagram"),
                      "tiktok": ("tiktok-only", "TikTok")}
    for r in st.get("renderedUnpublished", []):
        for flow, lbl in plat.values():
            acts.append({"flow": flow, "id": r["id"], "label": f"▶ {lbl} · {r['id']}"})
    for p in st.get("publishedTodayPartial", []):
        for missing in p["missing"]:
            flow, lbl = plat.get(missing, ("yt-only", missing))
            acts.append({"flow": flow, "id": p["id"], "label": f"▶ {lbl} · {p['id']}"})
    for r in st.get("instagramRetries", []):
        acts.append({"flow": "ig-only", "id": r["shortId"], "label": f"▶ Instagram · {r['shortId']}"})
    return acts[:12]


def briefing():
    """Il messaggio d'apertura: cosa esce oggi, com'è andata, quanto manca all'obiettivo."""
    ctx = context()
    txt = "\n\n".join([_today_sentence(ctx), _pipeline_sentence(ctx["pipeline"]), _eta_sentence(ctx["plan"])])
    return {"reply": txt, "actions": _actions_for(ctx["pipeline"]), "plan": ctx["plan"]}


# ------------------------------------------------------------------ intent
def ask(message):
    m = (message or "").strip()
    low = m.lower()
    ctx = context()

    # 1) DECISIONE: ottimizza / cambia obiettivo temporale
    if re.search(r"ottimizz|acceler|anticip|arrivar|raggiunger|raggiung|obiettivo|entro|prima della fine|scadenz", low):
        target = piano_mod.parse_target_date(m)
        if target:
            r = piano_mod.optimize(target, note=m if len(m) < 300 else "")
            if not r["ok"]:
                return {"reply": "Non riesco a ottimizzare: " + r["error"], "actions": [], "plan": ctx["plan"]}
            old, new = r["old_cadence"], r["new_cadence"]
            lines = [
                f"Fatto: ho riscritto il piano per arrivare a **{ctx['plan']['targets']['subs']} iscritti entro il "
                f"{datetime.date.fromisoformat(r['target_date']).strftime('%d/%m/%Y')}**.",
                "",
                f"**Cosa serve** — mancano {r['missing']} iscritti in {r['days_left']} giorni: "
                f"~**{r['required_rate']:.1f} al giorno** ({r['required_week']:.0f} a settimana).",
                f"**Cosa ho cambiato** — Short: {old['shorts_per_week']} → **{new['shorts_per_week']}/settimana**; "
                f"long-form: {new['longform_per_month']}/mese (restano, servono per le 4.000 ore). "
                f"Video totali da produrre: **{r['videos_needed']}** (prodotti {r['videos_produced']}).",
            ]
            if r["capped"]:
                lines.append("⚠️ Il ritmo teorico necessario superava il tetto sostenibile di "
                             f"{piano_mod.MAX_SHORTS_PER_WEEK} Short/settimana: l'ho messo al massimo. "
                             "Per centrare la data serve alzare anche la **resa per video** (hook e packaging), non solo il volume.")
            elif not r["reachable"]:
                lines.append("⚠️ Anche con questa cadenza le assunzioni attuali non bastano: "
                             "conviene rivedere l'obiettivo o la resa per video.")
            else:
                lines.append("✅ Con questa cadenza l'obiettivo è raggiungibile secondo le assunzioni attuali.")
            v = r["video"]
            lines.append(f"💸 **Costo**: ~**${r['monthly_cost']:.0f}/mese** "
                         f"({piano_mod.ai_clips_per_week(r['new_cadence'])} clip AI/sett. × ${r['cost_per_clip']:.2f}, "
                         f"{v['tier']} {v['resolution']} {v['duration']}s) — dentro il budget di {r['budget_usd']}$/mese.")
            if r.get("budget_capped"):
                lines.append(f"🔒 **Mi sono fermato al budget**: per centrare la data servirebbero più Short, ma oltre "
                             f"**{r['max_ai_clips_week']} clip AI/settimana** si sforano i {r['budget_usd']}$. "
                             "Per accelerare senza spendere di più: più formati gratuiti (personaggio in pipeline "
                             "locale, compilation, canonici già prodotti) e hook/packaging migliori — non più volume a pagamento.")
            lines.append("")
            lines.append("Ho scritto la direttiva nel vault (**decisioni-coach**): l'orchestratore giornaliero la seguirà da domani.")
            return {"reply": "\n".join(lines), "actions": [], "plan": piano_mod.status(), "piano_changed": True}
        if re.search(r"ottimizz|anticip|acceler", low):
            return {"reply": "Dimmi **entro quando** vuoi arrivarci e riscrivo il piano — per esempio "
                             "«ottimizza per raggiungere l'obiettivo prima della fine del 2027».",
                    "actions": [], "plan": ctx["plan"]}

    # 1-bis) budget a zero su richiesta
    if re.search(r"budget zero|costo zero|zero assoluto|azzera", low):
        p = piano_mod.load_piano()
        p["budget"]["usd_per_month"] = 0
        p["cadence"]["shorts_per_week"] = p["cadence"].get("free_shorts_per_week", 4)
        piano_mod.save_piano(p, who="coach:budget-zero")
        return {"reply": "Fatto: **budget a €0**. Nessuna nuova clip AI — si va solo con long-form dal "
                         "libro, personaggio in locale, compilation delle clip già pagate e foto gratis. "
                         "La monetizzazione non ne risente (le ore le fa il long-form). Quando vuoi "
                         "riaprire un piccolo budget, dimmelo.",
                "actions": [], "plan": piano_mod.status(), "piano_changed": True}

    # 1-ter) COSTI: quanto spendiamo, e come spendere meno
    if re.search(r"cost|spesa|spend|budget|risparmi|abbatt|econom|quanto mi costa|prezzo", low):
        pl = ctx["plan"]
        v = pl["video"]
        w = piano_mod.watch_hours_plan()
        lines = [
            f"**Spesa attuale: ~${pl['monthly_cost']:.1f}/mese** — solo {pl['ai_clips_per_week']} clip AI/settimana "
            f"× ${pl['cost_per_clip']:.2f} ({v['tier']} {v['resolution']} {v['duration']}s). Budget: {pl['budget_usd']}$/mese.",
            "",
            "**Tutto il resto è a costo zero**, ed è la parte che monetizza davvero:",
            "• Long-form dal libro: testo + `edge-tts` (voci neurali gratis) + illustrazioni + musica «obito» tua.",
            "• «Personaggio»: pipeline locale `make_video.py`.",
            "• Compilation e re-cut: riusano le clip già pagate.",
            "• Foto IG: `genera_immagini_free.py` (Pollinations, senza chiave).",
            "",
            f"**Perché conta**: le 4.000 ore YPP si fanno **solo col long-form** — le ore degli Short "
            f"nel feed non contano. Quindi i video che ti monetizzano costano **€0**. "
            f"Hai già ~{w['free_material_minutes']} minuti di materiale narrativo in casa "
            f"(≈ {w['episodes_available']} episodi da {w['minutes_per_video']} min).",
            "",
            "**Vuoi scendere a zero assoluto?** Si può: smetti di generare nuove clip AI e usi solo "
            "le 10 già pagate ricombinate. Perdi la novità del gol (il motore di scoperta), non la "
            "monetizzazione. Dimmi «budget zero» e lo imposto.",
        ]
        return {"reply": "\n".join(lines), "actions": [], "plan": pl}

    # 2) stato pubblicazioni / errori
    if re.search(r"pubblicazion|andato|fallit|error|problem|tutto ok|pipeline|flow|riprov|rilanc", low):
        st = ctx["pipeline"]
        return {"reply": _pipeline_sentence(st), "actions": _actions_for(st), "plan": ctx["plan"]}

    # 3) cosa esce oggi
    if re.search(r"\boggi\b|adesso|in programma|che ora|quando esce|usci", low):
        return {"reply": _today_sentence(ctx), "actions": _actions_for(ctx["pipeline"]), "plan": ctx["plan"]}

    # 4) obiettivi / monetizzazione / tempi
    if re.search(r"monetizz|ypp|iscritt|ore di visione|watch|quanto manca|quando arriv|previs|ritmo|obiettiv|soldi|guadagn", low):
        return {"reply": _eta_sentence(ctx["plan"]), "actions": [], "plan": ctx["plan"]}

    # 5) conoscenza dal vault (brand + strategia)
    hits = vault_search(m)
    if hits:
        title, digest = _note_digest(hits[0])
        return {"reply": f"📚 Dal vault — **{title}**\n\n{digest}", "actions": [], "plan": ctx["plan"]}

    # 6) fallback
    return {"reply": "Posso dirti **cosa esce oggi** e a che ora, se le **pubblicazioni automatiche** sono andate "
                     "a buon fine (e rilanciarle), quanto manca agli **obiettivi di monetizzazione**, e rispondere su "
                     "**brand e strategia** pescando dal vault.\n\nE posso decidere: dimmi per esempio "
                     "«ottimizza le pubblicazioni per raggiungere l'obiettivo prima della fine del 2027».",
            "actions": [], "plan": ctx["plan"]}


if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:])
    print(json.dumps(ask(q) if q else briefing(), ensure_ascii=False, indent=1, default=str))
