#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CryptoScan — Serveur Production v2.0
Système de jobs asynchrones pour éviter les timeouts Railway
"""

import http.server
import socketserver
import urllib.request
import urllib.error
import json, os, sys, threading, socket, time, traceback, uuid, hashlib

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
PORT     = int(os.environ.get("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Sémaphore : max N requêtes Anthropic simultanées
_api_lock = threading.Semaphore(int(os.environ.get("MAX_CONCURRENT", 5)))

# ── JOB QUEUE ─────────────────────────────────────────────────────────────────
# Format: { job_id: { "status": "pending"|"running"|"done"|"error",
#                      "result": bytes|None, "http_status": int,
#                      "created": float } }
_jobs = {}
_jobs_lock = threading.Lock()

def _cleanup_old_jobs():
    """Supprime les jobs de plus de 10 minutes."""
    while True:
        time.sleep(60)
        now = time.time()
        with _jobs_lock:
            old = [jid for jid, j in _jobs.items() if now - j["created"] > 600]
            for jid in old:
                del _jobs[jid]

threading.Thread(target=_cleanup_old_jobs, daemon=True).start()

# ── FICHIER HTML ───────────────────────────────────────────────────────────────
def find_html():
    for name in ["cryptoscan.html", "cryptoscan_v5.html", "cryptoscan_v4.html"]:
        if os.path.exists(os.path.join(BASE_DIR, name)):
            return name
    for f in os.listdir(BASE_DIR):
        if f.endswith(".html"):
            return f
    return None

HTML_FILE = find_html() or "cryptoscan.html"

# ── SERVEUR MULTI-THREAD ───────────────────────────────────────────────────────
class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


class CryptoHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        ts = time.strftime("%H:%M:%S")
        print(f"  [{ts}] {fmt % args}", flush=True)

    def log_error(self, fmt, *args):
        pass

    # ── ROUTING ───────────────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self._send_cors_headers(200)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/cryptoscan.html",
                    "/cryptoscan_v4.html", "/cryptoscan_v5.html"):
            self._serve_html()
        elif path == "/api/config":
            self._serve_config()
        elif path.startswith("/api/job/"):
            job_id = path[len("/api/job/"):]
            self._get_job(job_id)
        elif path == "/ping":
            self._ok(b"pong")
        elif path == "/health":
            self._ok(json.dumps({"status": "ok"}).encode(), "application/json")
        else:
            self._send_cors_headers(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/anthropic":
            self._proxy()
        elif path == "/api/save-key":
            self._save_key()
        else:
            self._send_cors_headers(404)
            self.end_headers()

    # ── SERVE HTML ────────────────────────────────────────────────────────────
    def _serve_html(self):
        fp = os.path.join(BASE_DIR, HTML_FILE)
        if not os.path.exists(fp):
            self._send_cors_headers(404)
            self.end_headers()
            self.wfile.write(b"HTML file not found")
            return
        data = open(fp, "rb").read()
        self._send_cors_headers(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control",  "no-cache, no-store")
        self.end_headers()
        self.wfile.write(data)

    # ── CONFIG ────────────────────────────────────────────────────────────────
    def _serve_config(self):
        has_key = bool(ENV_API_KEY)
        body = json.dumps({
            "has_key":     has_key,
            "key_preview": ENV_API_KEY[:16] + "..." if has_key else ""
        }).encode()
        self._ok(body, "application/json")

    # ── SAVE KEY ──────────────────────────────────────────────────────────────
    def _save_key(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            key    = body.get("key", "").strip()
            if key.startswith("sk-ant-"):
                resp = json.dumps({"ok": True}).encode()
            else:
                resp = json.dumps({"ok": False, "error": "Format invalide"}).encode()
            self._ok(resp, "application/json")
        except Exception as e:
            self._json_error(500, str(e))

    # ── PROXY ANTHROPIC (ASYNC JOB) ───────────────────────────────────────────
    def _proxy(self):
        """
        Retourne immédiatement un job_id, puis traite la requête en arrière-plan.
        Le job_id est un hash du payload → idempotent si le client re-poste.
        """
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 1024 * 1024:
                self._json_error(413, "Payload trop volumineux (max 1 MB)")
                return
            payload = self.rfile.read(length)

            api_key = self.headers.get("x-api-key", "").strip()
            if not api_key.startswith("sk-ant-"):
                api_key = ENV_API_KEY

            if not api_key:
                self._json_error(401, "Clé API manquante.")
                return

            # Cap max_tokens à 8000 (limite sûre Claude Sonnet)
            try:
                body = json.loads(payload)
                if body.get("max_tokens", 0) > 8000:
                    body["max_tokens"] = 8000
                    payload = json.dumps(body).encode()
            except Exception:
                pass

            # Job_id déterministe = hash du payload (idempotent si retry)
            job_id = hashlib.md5(payload).hexdigest()

            with _jobs_lock:
                existing = _jobs.get(job_id)

            if existing is None:
                # Nouveau job
                with _jobs_lock:
                    _jobs[job_id] = {
                        "status":      "pending",
                        "result":      None,
                        "http_status": 200,
                        "created":     time.time()
                    }
                t = threading.Thread(
                    target=self._run_job,
                    args=(job_id, payload, api_key),
                    daemon=True
                )
                t.start()
            # else: job déjà en cours, on retourne le même job_id

            resp = json.dumps({"job_id": job_id}).encode()
            try:
                self._ok(resp, "application/json")
            except (BrokenPipeError, ConnectionResetError):
                # Railway a coupé la connexion, le job tourne quand même
                ts = time.strftime("%H:%M:%S")
                print(f"  [{ts}] BrokenPipe sur job {job_id[:8]} — job continue en background", flush=True)

        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            traceback.print_exc()
            try:
                self._json_error(500, str(e))
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _run_job(self, job_id, payload, api_key):
        """Exécute l'appel Anthropic dans un thread séparé."""
        TOTAL_TIMEOUT = 95  # secondes — hard cap indépendant des retries urllib

        with _jobs_lock:
            _jobs[job_id]["status"] = "running"

        _api_lock.acquire()

        result = [None]
        exc    = [None]

        def do_call():
            try:
                result[0] = self._call_anthropic_with_retry(payload, api_key)
            except Exception as e:
                exc[0] = e

        worker = threading.Thread(target=do_call, daemon=True)
        worker.start()
        worker.join(timeout=TOTAL_TIMEOUT)
        _api_lock.release()

        if worker.is_alive():
            # Le thread tourne toujours → timeout total dépassé
            err = json.dumps({"error": {"message":
                f"Timeout API ({TOTAL_TIMEOUT}s) — réessayez dans quelques secondes"}}).encode()
            with _jobs_lock:
                _jobs[job_id]["status"]      = "error"
                _jobs[job_id]["result"]      = err
                _jobs[job_id]["http_status"] = 504
            return

        if exc[0]:
            err = json.dumps({"error": {"message": str(exc[0])}}).encode()
            with _jobs_lock:
                _jobs[job_id]["status"]      = "error"
                _jobs[job_id]["result"]      = err
                _jobs[job_id]["http_status"] = 500
        else:
            status, data = result[0]
            with _jobs_lock:
                _jobs[job_id]["status"]      = "done"
                _jobs[job_id]["result"]      = data
                _jobs[job_id]["http_status"] = status

    def _get_job(self, job_id):
        """Retourne l'état du job."""
        with _jobs_lock:
            job = _jobs.get(job_id)

        if job is None:
            self._json_error(404, "Job introuvable")
            return

        if job["status"] in ("pending", "running"):
            resp = json.dumps({"status": job["status"]}).encode()
            self._ok(resp, "application/json")
            return

        # done ou error — retourner le résultat brut d'Anthropic
        try:
            self._send_cors_headers(job["http_status"])
            self.send_header("Content-Type",   "application/json")
            self.send_header("Content-Length", str(len(job["result"])))
            self.end_headers()
            self.wfile.write(job["result"])
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client déconnecté, résultat perdu mais pas critique

        # Nettoyer le job immédiatement après livraison
        with _jobs_lock:
            _jobs.pop(job_id, None)

    def _call_anthropic_with_retry(self, payload, api_key, max_retries=3):
        wait = 3
        for attempt in range(max_retries):
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload, method="POST"
            )
            req.add_header("Content-Type",      "application/json")
            req.add_header("x-api-key",         api_key)
            req.add_header("anthropic-version", "2023-06-01")
            req.add_header("anthropic-beta",    "web-search-2025-03-05")

            try:
                # timeout=70 : socket inactivity timeout (par opération)
                # Le hard cap total est géré par _run_job (95s)
                with urllib.request.urlopen(req, timeout=70) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry_after = (e.headers.get("retry-after") or
                                   e.headers.get("x-ratelimit-reset-requests"))
                    if retry_after:
                        try:
                            wait = min(int(retry_after) + 1, 20)
                        except ValueError:
                            pass
                    ts = time.strftime("%H:%M:%S")
                    print(f"  [{ts}] Rate limit 429 — attente {wait}s ({attempt+1}/{max_retries})...", flush=True)
                    time.sleep(wait)
                    wait = min(wait * 2, 20)
                    continue
                else:
                    return e.code, e.read()
            except Exception as e:
                ts = time.strftime("%H:%M:%S")
                print(f"  [{ts}] Erreur réseau : {e}", flush=True)
                if attempt < max_retries - 1:
                    time.sleep(wait)
                    wait = min(wait * 2, 10)
                    continue
                raise

        msg = json.dumps({"error": {"message": f"Échec après {max_retries} tentatives."}}).encode()
        return 429, msg

    # ── HELPERS ───────────────────────────────────────────────────────────────
    def _send_cors_headers(self, code):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, x-api-key, anthropic-version, anthropic-beta")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options",        "SAMEORIGIN")
        self.send_header("Referrer-Policy",        "no-referrer")

    def _ok(self, body, ctype="application/octet-stream"):
        self._send_cors_headers(200)
        self.send_header("Content-Type",   ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, code, msg):
        body = json.dumps({"error": {"message": msg}}).encode()
        self._send_cors_headers(code)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    html_path = os.path.join(BASE_DIR, HTML_FILE)
    if not os.path.exists(html_path):
        print(f"[ERREUR] Fichier HTML introuvable : {HTML_FILE}", flush=True)
        sys.exit(1)

    print(f"[OK] CryptoScan Production v2.0 (jobs asynchrones)", flush=True)
    print(f"[OK] HTML : {HTML_FILE}", flush=True)
    print(f"[OK] Port : {PORT}", flush=True)
    print(f"[OK] Clé API env : {'présente' if ENV_API_KEY else 'absente'}", flush=True)

    server = ThreadingTCPServer(("0.0.0.0", PORT), CryptoHandler)
    print(f"[OK] Serveur démarré sur 0.0.0.0:{PORT}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[OK] Serveur arrêté.", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
