"""Offline fixture: one HTTP server, two faces - a "website" and a "REST API".

Serves the static Microblog site and a JSON API on 127.0.0.1 only. The API
is intentionally partial: read endpoints exist, the write endpoint 404s on
purpose (that is the trigger that forces the browser-fallback path).

Run:  python3 fixture_server.py [port]     (default 8791)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.join(HERE, "fixtures", "site")
sys.path.insert(0, HERE)

from fixtures import ARTICLE, FEED  # noqa: E402

DB_PATH: str = ""  # set at startup; posts land in sqlite for assertions


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


class Handler(BaseHTTPRequestHandler):
    # silence default stderr logging; tests assert on state, not logs
    def log_message(self, fmt, *args):  # noqa: A002
        pass

    # ---- helpers ---------------------------------------------------------

    def _json(self, obj, status: int = 200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text: str, status: int = 200):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name: str, ctype: str):
        path = os.path.join(SITE_DIR, name)
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- API face ---------------------------------------------------------

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/api/article":
            return self._json(ARTICLE)
        if parsed.path == "/api/feed":
            conn = db()
            rows = conn.execute(
                "SELECT id, author, text FROM posts ORDER BY rowid DESC LIMIT 50"
            ).fetchall()
            conn.close()
            return self._json({"posts": [dict(r) for r in rows]})

        if parsed.path.startswith("/site/") or parsed.path == "/favicon.ico":
            name = parsed.path.removeprefix("/site/")
            if name == "favicon.ico":
                return self._file("favicon.ico", "image/x-icon")
            safe = os.path.normpath(name).lstrip("/")
            if safe.startswith(".."):
                return self._json({"error": "bad path"}, 400)
            target = os.path.join(SITE_DIR, safe)
            if os.path.isfile(target):
                ctype = "text/css" if target.endswith(".css") else (
                    "application/javascript" if target.endswith(".js") else "text/html"
                )
                return self._file(os.path.relpath(target, SITE_DIR), ctype)
            return self._json({"error": "not found"}, 404)

        # SPA-ish static site: unknown paths get index.html (the app shell)
        return self._file("index.html", "text/html")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        if parsed.path == "/api/login":
            form = urllib.parse.parse_qs(raw.decode())
            user = (form.get("user") or [""])[0]
            password = (form.get("password") or [""])[0]
            if user == "testuser" and password == "testpass":
                resp = {"ok": True, "session": "sess-fixed-123"}
            else:
                resp = {"ok": False}
            return self._json(resp)

        if parsed.path == "/site/api/login":
            # browser form post -> redirect with cookie (session fixation path)
            form = urllib.parse.parse_qs(raw.decode())
            user = (form.get("user") or [""])[0]
            password = ( form.get("password") or [""])[0]
            if user == "testuser" and password == "testpass":
                self.send_response(303)
                self.send_header("Set-Cookie", "sid=sess-fixed-123; Path=/")
                self.send_header("Location", "/")
                self.end_headers()
                return
            return self._html("<h1>bad credentials</h1>", 401)

        if parsed.path == "/site/api/post":
            # browser-fallback write path: requires the sid cookie set by
            # login; inserts the post; redirects back to the feed
            cookie = self.headers.get("Cookie", "")
            m = re.search(r"sid=([A-Za-z0-9-]+)", cookie)
            if not m:
                return self._html("<h1>login required</h1>", 401)
            form = urllib.parse.parse_qs(raw.decode())
            text = (form.get("text") or [""])[0].strip()
            if not text:
                return self._html("<h1>empty post</h1>", 400)
            conn = db()
            conn.execute(
                "INSERT INTO posts (author, text) VALUES ('testuser', ?)", (text,)
            )
            conn.commit()
            conn.close()
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()
            return

        # THE 404: no public write API. This is the fallback trigger.
        if parsed.path == "/api/posts":
            return self._json({"error": "no write api"}, 404)

        return self._json({"error": "not found"}, 404)


def main() -> int:
    global DB_PATH
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
    db_dir = os.path.join(HERE, "state")
    os.makedirs(db_dir, exist_ok=True)
    DB_PATH = os.path.join(db_dir, "fixture.sqlite")
    if os.environ.get("RESET_DB", "1") == "1":
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY,
            author TEXT NOT NULL,
            text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """
    )
    for item in FEED:
        exists = conn.execute(
            "SELECT 1 FROM posts WHERE author=? AND text=?", (item["author"], item["text"])
        ).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO posts (author, text) VALUES (?, ?)",
                (item["author"], item["text"]),
            )
    conn.commit()
    conn.close()

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"fixture server on http://127.0.0.1:{port} (db: {DB_PATH})", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
