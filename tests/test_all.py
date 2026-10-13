"""Offline test suite for tryrest. Zero network beyond 127.0.0.1.

Layers:
  1. ws.py unit tests (frame encode/decode against a fake server socket)
  2. fixture server behavior (404 write API, login, fallback post path)
  3. full agent loop with real headless Chromium (skipped gracefully if
     chromium is absent)

Run:  python3 tests/test_all.py
"""

import json
import os
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, ROOT)

from ws import WsClient, ws_url_parse  # noqa: E402

FIXTURE_PORT = 8791
BASE = f"http://127.0.0.1:{FIXTURE_PORT}"


def http(method: str, path: str, body: bytes | None = None,
         headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(BASE + path, data=body, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def posts_in_db() -> list[tuple]:
    db = os.path.join(ROOT, "state", "fixture.sqlite")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute("SELECT author, text FROM posts").fetchall()
    conn.close()
    return rows


# --------------------------------------------------------------------------
# Fake WebSocket server for ws.py frame tests
# --------------------------------------------------------------------------

class FakeWsServer(threading.Thread):
    """Accepts one raw TCP connection and speaks just enough WebSocket."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.received: list[bytes] = []
        self.conn = None

    def run(self):
        self.conn, _ = self.sock.accept()
        # handshake: read headers, reply 101
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.conn.recv(4096)
        key = next(l.split(b": ", 1)[1].strip() for l in buf.split(b"\r\n")
                   if l.lower().startswith(b"sec-websocket-key:"))
        import base64
        import hashlib
        accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        self.conn.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n"
        )
        # echo every text frame back, unmasking first
        while True:
            try:
                data = self.conn.recv(4096)
            except OSError:
                return
            if not data:
                return
            self._handle(data)

    def _handle(self, data: bytes):
        # parse frames; for the test we only need single frames
        i = 0
        while i + 2 <= len(data):
            b1, b2 = data[i], data[i + 1]
            ln = b2 & 0x7F
            mask_len = 4 if b2 & 0x80 else 0
            if ln == 126:
                ln = struct.unpack("!H", data[i + 2:i + 4])[0]
                head = 4
            else:
                head = 2
            payload = data[i + head + mask_len: i + head + mask_len + ln]
            mask = data[i + head:i + head + 4] if mask_len else b"\0\0\0\0"
            plain = bytes(b ^ mask[j % 4] for j, b in enumerate(payload))
            opcode = b1 & 0x0F
            if opcode == 0x1:
                self.received.append(plain)
                self._send_text_frame(plain)  # echo back
            i += head + mask_len + ln

    def _send_text_frame(self, payload: bytes):
        ln = len(payload)
        if ln < 126:
            header = struct.pack("!BB", 0x81, ln)
        else:
            header = struct.pack("!BBH", 0x81, 126, ln)
        self.conn.sendall(header + payload)


class WsTests(unittest.TestCase):
    def test_roundtrip(self):
        srv = FakeWsServer()
        srv.start()
        ws = WsClient("127.0.0.1", srv.port, "/")
        for text in ["hello", "x" * 200, json.dumps({"id": 1, "method": "ping"})]:
            ws.send_text(text)
            got = ws.recv_text(timeout=5)
            self.assertEqual(got, text)
        ws.close()

    def test_url_parse(self):
        h, p, r = ws_url_parse("ws://127.0.0.1:9222/devtools/page/ABC")
        self.assertEqual((h, p, r), ("127.0.0.1", 9222, "/devtools/page/ABC"))


# --------------------------------------------------------------------------
# Fixture server behavior
# --------------------------------------------------------------------------

class FixtureServerTests(unittest.TestCase):
    def test_api_reads_work(self):
        status, body = http("GET", "/api/article")
        self.assertEqual(status, 200)
        art = json.loads(body)
        self.assertEqual(art["id"], "art-001")

    def test_write_api_404s(self):
        status, _ = http("POST", "/api/posts", b'{"text":"x"}',
                         {"Content-Type": "application/json"})
        self.assertEqual(status, 404)

    def test_login_api(self):
        status, body = http("POST", "/api/login", b"user=testuser&password=testpass",
                            {"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_fallback_post_route_requires_cookie(self):
        status, _ = http("POST", "/site/api/post", b"text=nope")
        self.assertEqual(status, 401)

    def test_fallback_post_route_inserts(self):
        marker = f"canary-{int(time.time())}"
        # a 303 would make urllib auto-follow to GET / ; disable that
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        req = urllib.request.Request(
            BASE + "/site/api/post",
            data=f"text={marker}".encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Cookie": "sid=sess-fixed-123"},
            method="POST",
        )
        try:
            with opener.open(req, timeout=5) as r:
                status = r.status
        except urllib.error.HTTPError as e:
            status = e.code
        self.assertEqual(status, 303)
        self.assertIn(("testuser", marker), posts_in_db())


# --------------------------------------------------------------------------
# Full agent loop (real Chromium)
# --------------------------------------------------------------------------

def find_chromium():
    for cand in ("chromium", "chromium-browser", "google-chrome"):
        p = subprocess.run(["which", cand], capture_output=True, text=True)
        if p.returncode == 0:
            return cand
    return None


class AgentLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bin = find_chromium()
        if not cls.bin:
            raise unittest.SkipTest("no chromium on PATH")

    def test_post_task_full_loop(self):
        from agent import Browser
        from planner import run_task

        marker = f"agent-post-{int(time.time())}"
        report = run_task(
            "post text to the feed",
            BASE,
            {"text": marker, "user": "testuser", "password": "testpass"},
            browser_factory=lambda: Browser(chromium_bin=self.bin, port=9781 + os.getpid() % 100),
        )
        self.assertTrue(report["success"], report)
        self.assertEqual(report["via"], "browser")
        self.assertIn(("testuser", marker), posts_in_db())
        # exactly-once: only ONE copy of the marker exists
        copies = sum(1 for _, t in posts_in_db() if t == marker)
        self.assertEqual(copies, 1)

    def test_drift_renamed_ids_still_perceive(self):
        """Redesign simulation: ids/classes renamed, extra wrappers added.
        The DOM map must still surface the controls by tag/name/label so
        the planner can produce actions (execution via positional
        selectors is Checkpoint 2)."""
        from agent import Browser
        from planner import rules_plan

        b = Browser(chromium_bin=self.bin, port=9791 + os.getpid() % 90)
        b.start()
        b.attach_page()
        try:
            # state A: logged out -> login controls are the visible surface
            b.goto(BASE)
            dom = b.dom_map()
            self.assertTrue(any(e["name"] == "user" for e in dom), dom)
            self.assertTrue(any(e["type"] == "password" for e in dom), dom)
            self.assertTrue(rules_plan("sign in", dom, {}))

            # state B: logged in, then simulate a redesign (ids scrambled,
            # extra wrapper divs) -> composer must still be perceivable
            b.eval_js("document.cookie = 'sid=sess-fixed-123; path=/'")
            b.goto(BASE)
            b.eval_js("""
              for (const el of document.querySelectorAll('[id]')) {
                el.id = 'x' + Math.floor(Math.random()*1e6);
              }
              const wrap = document.createElement('div');
              const form = document.querySelector('form');
              if (form && form.parentNode) { form.parentNode.insertBefore(wrap, form); wrap.append(form); }
            """)
            dom = b.dom_map()
            self.assertTrue(any(e["tag"] == "textarea" for e in dom), dom)
            self.assertTrue(any("post" in (e["label"] or "").lower()
                                and e["tag"] == "button" for e in dom), dom)
            self.assertTrue(rules_plan("post text to the feed", dom,
                                       {"text": "drift"}))
        finally:
            b.stop()


if __name__ == "__main__":
    # boot the fixture server once for all tests
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "fixture_server.py"),
                             str(FIXTURE_PORT)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 10
        up = False
        while time.monotonic() < deadline:
            try:
                urllib.request.urlopen(BASE + "/api/article", timeout=1)
                up = True
                break
            except Exception:
                time.sleep(0.2)
        if not up:
            raise RuntimeError("fixture server did not come up")
        unittest.main(verbosity=2)
    finally:
        proc.terminate()
