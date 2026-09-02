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
"""
import os, sys, json, subprocess, shutil, re
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
    cmd += ["--only", item_id] if item_id else ["--all"]
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
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Calciovich control panel su http://localhost:{PORT}")
    srv.serve_forever()
