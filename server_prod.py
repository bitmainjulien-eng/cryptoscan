#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CryptoScan — Serveur Production v1.0
Pour déploiement cloud (Railway, Render, Fly.io, VPS, etc.)
"""

import http.server
import socketserver
import urllib.request
import urllib.error
import json, os, sys, threading, socket, time, traceback

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
PORT     = int(os.environ.get("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# La clé API vient UNIQUEMENT de la variable d'environnement (pas de fichier local en prod)
# Ou du header x-api-key envoyé par le client (clé saisie dans l'interface)
ENV_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Sémaphore : max N requêtes Anthropic simultanées
_api_lock = threading.Semaphore(int(os.environ.get("MAX_CONCURRENT", 5)))

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
        pass  # Suppress noisy default errors

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

    # ── SAVE KEY (session only — no file storage in prod) ─────────────────────
    def _save_key(self):
        """
        En production, on ne sauvegarde pas la clé dans un fichier.
        La clé est mémorisée par le client (localStorage dans le navigateur).
        Ce endpoint valide juste le format.
        """
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

    # ── PROXY ANTHROPIC ───────────────────────────────────────────────────────
    def _proxy(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 512 * 1024:  # 512 KB max
                self._json_error(413, "Payload trop volumineux (max 512 KB)")
                return
            payload = self.rfile.read(length)

            # Priorité : header x-api-key > variable d'environnement
            api_key = self.headers.get("x-api-key", "").strip()
            if not api_key.startswith("sk-ant-"):
                api_key = ENV_API_KEY

            if not api_key:
                self._json_error(401, "Clé API manquante. Entrez-la dans l'application.")
                return

            _api_lock.acquire()
            try:
                status, data = self._call_anthropic_with_retry(payload, api_key)
            finally:
                _api_lock.release()

            self._send_cors_headers(status)
            self.send_header("Content-Type",   "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        except Exception as e:
            traceback.print_exc()
            self._json_error(500, str(e))

    def _call_anthropic_with_retry(self, payload, api_key, max_retries=8):
        wait = 5
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
                with urllib.request.urlopen(req, timeout=120) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    err_body = e.read()
                    retry_after = (e.headers.get("retry-after") or
                                   e.headers.get("x-ratelimit-reset-requests"))
                    if retry_after:
                        try:
                            wait = int(retry_after) + 1
                        except ValueError:
                            pass
                    ts = time.strftime("%H:%M:%S")
                    print(f"  [{ts}] Rate limit 429 — attente {wait}s ({attempt+1}/{max_retries})...", flush=True)
                    time.sleep(wait)
                    wait = min(wait * 2, 120)
                    continue
                else:
                    return e.code, e.read()

        msg = json.dumps({"error": {"message": f"Rate limit persistant après {max_retries} tentatives."}}).encode()
        return 429, msg

    # ── HELPERS ───────────────────────────────────────────────────────────────
    def _send_cors_headers(self, code):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, x-api-key, anthropic-version, anthropic-beta")
        # Security headers
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

    print(f"[OK] CryptoScan Production", flush=True)
    print(f"[OK] HTML : {HTML_FILE}", flush=True)
    print(f"[OK] Port : {PORT}", flush=True)
    print(f"[OK] Clé API env : {'présente' if ENV_API_KEY else 'absente (les clients fourniront la leur)'}", flush=True)

    server = ThreadingTCPServer(("0.0.0.0", PORT), CryptoHandler)
    print(f"[OK] Serveur démarré sur 0.0.0.0:{PORT}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[OK] Serveur arrêté.", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
