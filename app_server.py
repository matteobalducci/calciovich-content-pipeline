#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app_server.py — server locale del cruscotto Calciovich.
Serve i file statici (app/, output/, ecc.) esattamente come prima
(python3 -m http.server), e in piu' espone due endpoint JSON che rendono
l'app un vero pannello di controllo, senza dover aprire una sessione Claude
solo per lanciare o verificare uno script:

  GET  /api/pipeline-status  → stato di oggi (stato_pipeline.compute_status())
  GET  /api/briefing         → messaggio d'apertura del Coach (cosa esce oggi, esiti, obiettivi)
  POST /api/coach            → {"message": "..."} il Coach risponde E decide (puo'
                                riscrivere piano.json e le direttive nel vault)
  POST /api/run              → {"flow": "<id>", "id": "<opzionale>"} lancia
                                uno script della whitelist FLOWS qui sotto e
                                restituisce l'esito. Nessun comando arbitrario:
                                solo cio' che e' elencato in FLOWS puo' partire.

Bind solo su 127.0.0.1: il pannello puo' eseguire script, quindi non deve
essere raggiungibile dalla rete locale, solo dal browser su questa macchina.

Include anche un watchdog in background (_freshness_watchdog_loop): stato_pipeline
ha gia' le soglie di freschezza giuste (36h/72h), ma prima erano solo leggibili
via /api/pipeline-status — un problema restava invisibile finche' qualcuno non
apriva il pannello di sua iniziativa. E' cosi' che sono passati inosservati per
giorni due buchi reali di raccolta youtubestats (16-17/09 e 24-27/09, LaunchAgent
saltato durante cicli di sleep prolungati) — scoperti solo a posteriori dal grafico
Looker Studio, non dal proprio sistema di controllo. Questo processo gira comunque
sempre (KeepAlive in com.calciovich.appserver.plist), quindi e' il posto giusto per
controllare la freschezza ogni 30 min e mandare una notifica macOS quando c'e' un
flag error/warn — senza dover ricordarsi di aprire il pannello.
"""
import os, sys, json, subprocess, shutil, re, threading, time, datetime
import http.server
from socketserver import ThreadingMixIn

HERE = os.path.dirname(os.path.abspath(__file__))

# I publisher attendono fino a 300s l'elaborazione lato piattaforma
# (wait_container_ready / wait_publish_complete). Il margine deve stare SOPRA:
# se il server li uccide prima, il contenuto puo' essere gia' pubblicato senza
# che il registro locale lo sappia.
# NOTA (audit Codex 02/09): 300s e' solo l'attesa di elaborazione lato
# piattaforma; a quella vanno sommati il caricamento su R2, la creazione del
# container e la latenza delle API. 420s puo' ancora non bastare per un file
# grande su una linea lenta. Questo timeout resta come rete di sicurezza contro
# un processo appeso, NON come limite di correttezza: la protezione vera contro
# il taglio a meta' e' il registro write-ahead, che al giro dopo riconcilia.
PUBLISH_TIMEOUT = 900
os.chdir(HERE)
sys.path.insert(0, HERE)
import stato_pipeline
import coach as coach_mod

PORT = 8753
ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]*$")
UPLOAD_PLIST_SRC = os.path.join(HERE, "com.calciovich.upload.plist")
UPLOAD_PLIST_DST = os.path.expanduser("~/Library/LaunchAgents/com.calciovich.upload.plist")


def _flow_yt(item_id):
    cmd = [sys.executable, "carica_youtube.py"]
    cmd += ["--only", item_id, "--privacy", "public"] if item_id else ["--all", "--limit", "6"]
    return cmd


def _flow_ig(item_id):
    cmd = [sys.executable, "carica_instagram.py"]
    cmd += ["--only", item_id] if item_id else ["--all"]
    return cmd


def _flow_tiktok(item_id):
    cmd = [sys.executable, "carica_tiktok.py"]
    # Senza un item preciso: uno solo per richiesta. Ogni post TikTok resta una bozza nella Inbox
    # finche' una persona non la conferma; piu' post insieme superano quello smaltimento e fanno
    # scattare spam_risk_too_many_pending_share (ENGINEERING-LOG ENG-5).
    cmd += ["--only", item_id] if item_id else ["--all", "--limit", "1"]
    return cmd


# whitelist: SOLO questi id possono partire da una richiesta POST /api/run.
FLOWS = {
    "yt-only": _flow_yt,
    "yt-retry-all": lambda i: _flow_yt(None),
    "ig-only": _flow_ig,
    "ig-retry-all": lambda i: _flow_ig(None),
    "tiktok-only": _flow_tiktok,
    "tiktok-retry-all": lambda i: _flow_tiktok(None),
    "refresh-data": lambda i: [sys.executable, "app/genera_app.py"],
    "refresh-youtube-stats": lambda i: [sys.executable, "aggiorna_youtube_stats.py"],
    "check-comments": lambda i: [sys.executable, "rispondi_commenti.py", "--list"],
}


WATCHDOG_STATE_PATH = os.path.join(HERE, "output", "watchdog-last-notified.json")
WATCHDOG_CHECK_SECONDS = 1800  # ogni 30 min
WATCHDOG_RENOTIFY_HOURS = 12   # se un flag resta attivo, ripeti il promemoria al massimo ogni 12h (non ogni 30 min)


def _notify_macos(text, title="Calciovich — controllo pipeline"):
    """AppleScript via osascript, non un shell string grezzo: display notification
    vuole gli argomenti come stringhe AppleScript, quindi passiamo title/text come
    argv separati a un one-liner che li referenzia da 'on run argv' invece di
    interpolarli a mano in una stringa -e (eviterebbe problemi di escaping ogni
    volta che un flag contiene virgolette o caratteri speciali, cosa che i testi
    di stato_pipeline fanno spesso, es. citano id fra «»)."""
    script = 'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"\nend run'
    try:
        done = subprocess.run(["osascript", "-e", script, title, text], capture_output=True, timeout=10)
        return done.returncode == 0
    except Exception:
        return False  # una notifica persa non deve mai far cadere il watchdog, ma non conta come inviata


def _watchdog_step(active, last_notified, now, notify, renotify_hours=None):
    """Decide quali flag notificare e ritorna il nuovo stato {chiave: ora dell'ultimo invio}.
    Un flag entra nello stato solo se la notifica e' PARTITA (notify() -> True) o se e' ancora
    dentro la finestra di silenzio: una notifica fallita viene ritentata al giro dopo invece di
    restare muta per 12h. I flag non piu' attivi spariscono: se tornano, ripartono come nuovi."""
    hours_limit = WATCHDOG_RENOTIFY_HOURS if renotify_hours is None else renotify_hours
    new_state = {}
    for key, flag in active.items():
        text, level = flag["text"], flag.get("level", "warn")
        prev_at = last_notified.get(key)
        due = True
        if prev_at:
            try:
                hours = (now - datetime.datetime.fromisoformat(prev_at)).total_seconds() / 3600
                due = hours >= hours_limit
            except ValueError:
                due = True
        if not due:
            new_state[key] = prev_at
        elif notify(f"{'⚠️' if level == 'warn' else '🛑'} {text}"):
            new_state[key] = now.isoformat(timespec="seconds")
    return new_state


def _freshness_watchdog_loop():
    while True:
        try:
            status = stato_pipeline.compute_status()
            # known_issues (debito di canone ecc.) sono backlog stabile con una propria
            # "since" — gia' visto e deliberatamente rimandato, non "appena scoperto".
            # compute_status() li rimescola dentro flags senza un tag che li distingua,
            # quindi li togliamo qui confrontando il testo: altrimenti il watchdog
            # continuerebbe a far vibrare il telefono ogni 12h per mesi su cose che
            # Matteo ha gia' deciso di rimandare, allenandolo a ignorare le notifiche —
            # esattamente il fallimento che questo watchdog dovrebbe evitare.
            known_issue_texts = {k.get("text", "") for k in status.get("knownIssues", [])}
            # Chiave = stato_pipeline.flag_key (livello + testo senza numeri), non il
            # testo grezzo: "da 31 ore" -> "da 32 ore" faceva sembrare nuovo lo stesso
            # problema a ogni controllo e rinotificava ogni ora invece che ogni 12h.
            active = {stato_pipeline.flag_key(f.get("level", "warn"), f["text"]): f
                      for f in status.get("flags", [])
                      if f.get("level") in ("warn", "error") and f["text"] not in known_issue_texts}

            try:
                last_notified = json.load(open(WATCHDOG_STATE_PATH, encoding="utf-8"))
            except Exception:
                last_notified = {}

            new_state = _watchdog_step(active, last_notified, datetime.datetime.now(), _notify_macos)

            os.makedirs(os.path.dirname(WATCHDOG_STATE_PATH), exist_ok=True)
            with open(WATCHDOG_STATE_PATH, "w", encoding="utf-8") as fh:
                json.dump(new_state, fh, ensure_ascii=False, indent=1)
        except Exception:
            pass  # mai far morire il thread per un errore di un singolo giro
        time.sleep(WATCHDOG_CHECK_SECONDS)


def _enable_yt_autoupload():
    os.makedirs(os.path.dirname(UPLOAD_PLIST_DST), exist_ok=True)
    shutil.copy(UPLOAD_PLIST_SRC, UPLOAD_PLIST_DST)
    subprocess.run(["launchctl", "unload", UPLOAD_PLIST_DST], capture_output=True)
    r = subprocess.run(["launchctl", "load", "-w", UPLOAD_PLIST_DST], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)


class Handler(http.server.SimpleHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = self.path.split("?")[0]
        if route == "/api/pipeline-status":
            try:
                self._json(stato_pipeline.compute_status())
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return
        if route == "/api/briefing":
            try:
                self._json(coach_mod.briefing())
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/coach":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._json({"error": "corpo non valido"}, 400)
                return
            try:
                self._json(coach_mod.ask(body.get("message", "")))
            except Exception as e:
                self._json({"error": str(e)}, 500)
            return

        if self.path != "/api/run":
            self._json({"error": "not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json({"ok": False, "error": "corpo non valido"}, 400)
            return

        flow = body.get("flow")
        item_id = body.get("id") or None
        if item_id is not None and not ID_RE.match(item_id):
            self._json({"ok": False, "error": "id non valido"}, 400)
            return

        if flow == "enable-yt-autoupload":
            try:
                rc, out = _enable_yt_autoupload()
                self._json({"ok": rc == 0, "exitCode": rc, "output": out})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return

        builder = FLOWS.get(flow)
        cmd = builder(item_id) if builder else None
        if not cmd:
            self._json({"ok": False, "error": "flow sconosciuto"}, 400)
            return

        try:
            # BUGFIX 02/09: il timeout era 240s mentre i publisher aspettano
            # legittimamente fino a 300s l'elaborazione lato piattaforma. Uccidere
            # il sottoprocesso a 240s produceva lo stato peggiore possibile:
            # contenuto pubblicato FUORI, non registrato DENTRO. Ora il margine
            # sta sopra il piu' lento dei publisher.
            r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                               timeout=PUBLISH_TIMEOUT)
            out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
            self._json({"ok": r.returncode == 0, "exitCode": r.returncode, "output": out[-6000:]})
        except subprocess.TimeoutExpired:
            self._json({"ok": False, "error": f"timeout ({PUBLISH_TIMEOUT}s) — il publisher potrebbe aver comunque pubblicato: controlla il registro prima di ritentare"}, 504)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, 500)


class ThreadingHTTPServer(ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    threading.Thread(target=_freshness_watchdog_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Calciovich control panel su http://localhost:{PORT}")
    srv.serve_forever()
