#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CryptoScan — Serveur Local v4.1 (robuste)
"""

import http.server
import socketserver
import urllib.request
import urllib.error
import json, os, sys, threading, webbrowser, socket, time, traceback

PORT     = 7842

# Sémaphore global : 1 seule requête Anthropic à la fois
# Évite d'envoyer plusieurs requêtes en parallèle qui épuisent le quota
_api_lock = threading.Semaphore(1)
CFG_FILE = os.path.join(os.path.expanduser("~"), ".cryptoscan4.json")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def find_html():
    for name in ["cryptoscan.html","cryptoscan_v5.html","cryptoscan_v4.html"]:
        if os.path.exists(os.path.join(BASE_DIR, name)):
            return name
    for f in os.listdir(BASE_DIR):
        if f.endswith(".html"):
            return f
    return None

HTML_FILE = find_html() or "cryptoscan_v5.html"


def cfg_load():
    try:
        if os.path.exists(CFG_FILE):
            return json.load(open(CFG_FILE, "r", encoding="utf-8"))
    except Exception:
        pass
    return {}

def cfg_save(d):
    try:
        json.dump(d, open(CFG_FILE, "w", encoding="utf-8"))
    except Exception:
        pass


class CryptoHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        ts = time.strftime("%H:%M:%S")
        print(f"  [{ts}] {fmt % args}")

    def log_error(self, fmt, *args):
        pass

    # ── ROUTING ──────────────────────────────────────────────
    def do_OPTIONS(self):
        self._send_cors_headers(200)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html", "/cryptoscan_v4.html"):
            self._serve_html()
        elif path == "/api/config":
            self._serve_config()
        elif path == "/ping":
            self._ok(b"pong")
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

    # ── SERVE HTML ───────────────────────────────────────────
    def _serve_html(self):
        fp = os.path.join(BASE_DIR, HTML_FILE)
        if not os.path.exists(fp):
            self._send_cors_headers(404)
            self.end_headers()
            self.wfile.write(b"cryptoscan_v4.html not found next to server.py")
            return
        data = open(fp, "rb").read()
        self._send_cors_headers(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── CONFIG ───────────────────────────────────────────────
    def _serve_config(self):
        cfg = cfg_load()
        key = cfg.get("api_key", "")
        body = json.dumps({
            "has_key": bool(key),
            "key_preview": key[:16] + "..." if key else ""
        }).encode()
        self._ok(body, "application/json")

    # ── SAVE KEY ─────────────────────────────────────────────
    def _save_key(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            key    = body.get("key", "").strip()
            if key.startswith("sk-ant-"):
                cfg = cfg_load()
                cfg["api_key"] = key
                cfg_save(cfg)
                resp = json.dumps({"ok": True}).encode()
            else:
                resp = json.dumps({"ok": False, "error": "Format invalide"}).encode()
            self._ok(resp, "application/json")
        except Exception as e:
            self._json_error(500, str(e))

    # ── PROXY ANTHROPIC ──────────────────────────────────────
    def _proxy(self):
        try:
            length  = int(self.headers.get("Content-Length", 0))
            if length > 512 * 1024:  # 512 KB max
                self._json_error(413, "Payload trop volumineux (max 512 KB)")
                return
            payload = self.rfile.read(length)

            cfg     = cfg_load()
            api_key = cfg.get("api_key", "")

            # accepter aussi la clé depuis le header
            hkey = self.headers.get("x-api-key", "")
            if hkey.startswith("sk-ant-"):
                api_key = hkey
                cfg["api_key"] = hkey
                cfg_save(cfg)

            if not api_key:
                self._json_error(401, "Clé API manquante. Sauvegardez-la dans l'application.")
                return

            # Attendre le verrou : 1 seule requête envoyée à la fois
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

    def _call_anthropic_with_retry(self, payload, api_key, max_retries=10):
        """Appelle l'API Anthropic en réessayant automatiquement sur 429."""
        wait = 5  # secondes d'attente initiale

        for attempt in range(max_retries):
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=payload, method="POST"
            )
            req.add_header("Content-Type",     "application/json")
            req.add_header("x-api-key",        api_key)
            req.add_header("anthropic-version","2023-06-01")
            req.add_header("anthropic-beta",   "web-search-2025-03-05")

            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return r.status, r.read()

            except urllib.error.HTTPError as e:
                if e.code == 429:
                    # Lire le corps pour extraire le retry-after si dispo
                    err_body = e.read()
                    retry_after = e.headers.get("retry-after") or e.headers.get("x-ratelimit-reset-requests")

                    if retry_after:
                        try:
                            wait = int(retry_after) + 1
                        except ValueError:
                            pass  # garder la valeur courante

                    ts = time.strftime("%H:%M:%S")
                    print(f"  [{ts}] Rate limit 429 — attente {wait}s avant retry ({attempt+1}/{max_retries})...")
                    time.sleep(wait)
                    wait = min(wait * 2, 120)  # backoff exponentiel, max 2 min
                    continue
                else:
                    # Autre erreur HTTP (400, 401, 500...) → renvoyer tel quel
                    return e.code, e.read()

        # Tous les retries épuisés
        msg = json.dumps({"error": {"message": f"Rate limit persistant après {max_retries} tentatives."}}).encode()
        return 429, msg

    # ── HELPERS ──────────────────────────────────────────────
    def _send_cors_headers(self, code):
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, x-api-key, anthropic-version, anthropic-beta")

    def _ok(self, body, ctype="application/json"):
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


def find_free_port(start=7842):
    for p in range(start, start + 30):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", p))
                return p
        except OSError:
            continue
    return start


def wait_for_server(port, timeout=10):
    """Attend que le serveur soit prêt avant d'ouvrir le navigateur."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.2)
    return False


def main():
    print()
    print("  ╔══════════════════════════════════════════════════╗")
    print("  ║   CRYPTOSCAN v4.1  —  Deep Intelligence          ║")
    print("  ║   Serveur local — proxy API — résout CORS        ║")
    print("  ╚══════════════════════════════════════════════════╝")
    print()

    # Vérifier le fichier HTML
    html_path = os.path.join(BASE_DIR, HTML_FILE)
    if not os.path.exists(html_path):
        # Try auto-detect one more time
        HTML_FILE_NEW = find_html()
        if HTML_FILE_NEW and os.path.exists(os.path.join(BASE_DIR, HTML_FILE_NEW)):
            html_path = os.path.join(BASE_DIR, HTML_FILE_NEW)
            print(f"  [OK] Fichier HTML trouvé : {HTML_FILE_NEW}")
        else:
            print(f"  [ERREUR] Aucun fichier HTML trouvé dans ce dossier !")
            print(f"  Dossier : {BASE_DIR}")
            print(f"  Fichiers présents : {os.listdir(BASE_DIR)}")
            input("\n  Appuyez sur Entrée pour quitter...")
            sys.exit(1)

    port = find_free_port(PORT)
    url  = f"http://localhost:{port}/"

    cfg = cfg_load()
    if cfg.get("api_key"):
        print(f"  [OK] Clé API trouvée (sauvegardée)")
    else:
        print(f"  [INFO] Aucune clé API — entrez-la dans l'interface")

    # Créer le serveur avec allow_reuse_address
    class ReusableTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    print(f"  [OK] Fichier HTML : {HTML_FILE}")
    print(f"  [OK] Démarrage du serveur sur le port {port}...")

    try:
        server = ReusableTCPServer(("127.0.0.1", port), CryptoHandler)
    except OSError as e:
        print(f"  [ERREUR] Impossible d'ouvrir le port {port}: {e}")
        input("\n  Appuyez sur Entrée pour quitter...")
        sys.exit(1)

    print(f"  [OK] Serveur actif sur : {url}")

    # Ouvrir le navigateur APRÈS que le serveur est prêt
    def open_browser():
        print(f"  [OK] Attente démarrage du serveur HTTP...")
        if wait_for_server(port, timeout=15):
            time.sleep(0.5)   # sécurité : laisser le serveur accepter les requêtes
            print(f"  [OK] Serveur prêt — ouverture du navigateur : {url}")
            webbrowser.open(url)
        else:
            print(f"  [WARN] Timeout atteint, ouverture du navigateur quand même...")
            webbrowser.open(url)

    threading.Thread(target=open_browser, daemon=True).start()

    print()
    print("  ══════════════════════════════════════════════════")
    print(f"  L'app est accessible sur : {url}")
    print("  Ne fermez pas cette fenêtre !")
    print("  Ctrl+C pour arrêter le serveur.")
    print("  ══════════════════════════════════════════════════")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  [OK] Serveur arrêté. À bientôt !")
        server.shutdown()


if __name__ == "__main__":
    main()
