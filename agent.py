"""tryrest - REST-first agent with automatic browser fallback.

Every action is attempted against the REST API first; on a defined trigger
(404/405) the agent spawns headless Chromium and completes the action
through the DOM via raw CDP over a stdlib WebSocket. No SDK, no third-party
dependencies, fully offline against the bundled fixture server.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from ws import recv_json, send_json, ws_url_parse, WsClient

FALLBACK_TRIGGERS = {404, 405}


class RestResult:
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except json.JSONDecodeError:
            return None


def rest(method: str, url: str, body: dict | None = None,
         headers: dict[str, str] | None = None, timeout: float = 10) -> RestResult:
    """Plain-stdlib HTTP call. Returns status + text; never raises on HTTPStatus."""
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Content-Type": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return RestResult(resp.status, resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return RestResult(e.code, e.read().decode("utf-8", "replace"))
    except urllib.error.URLError as e:
        return RestResult(0, str(e.reason))


# --------------------------------------------------------------------------
# Browser fallback via raw CDP
# --------------------------------------------------------------------------

class Browser:
    """Headless Chromium driven over the DevTools WebSocket, stdlib only."""

    def __init__(self, chromium_bin: str = "chromium",
                 port: int = 9777, profile_dir: str = ".cdp-profile"):
        self.bin = chromium_bin
        self.port = port
        self.profile = profile_dir
        self.proc = None
        self.ws: WsClient | None = None
        self._msg_id = 0

    # -- lifecycle ----------------------------------------------------------

    def start(self, extra_args: list[str] | None = None) -> None:
        import subprocess
        import time

        args = [
            self.bin,
            "--headless=new",
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--disable-gpu",
            "--remote-allow-origins=*",
        ] + (extra_args or [])
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        # poll /json/version until the endpoint answers
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            r = rest("GET", f"http://127.0.0.1:{self.port}/json/version")
            if r.ok:
                break
            time.sleep(0.2)
        else:
            self.stop()
            raise RuntimeError("chromium debug endpoint never came up")

    def stop(self) -> None:
        if self.ws:
            self.ws.close()
            self.ws = None
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
            self.proc = None

    def __enter__(self) -> "Browser":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- CDP plumbing ---------------------------------------------------------

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def attach_page(self) -> None:
        """Connect the WebSocket to the first browser tab."""
        r = rest("GET", f"http://127.0.0.1:{self.port}/json")
        targets = r.json() or []
        page = next((t for t in targets if t.get("type") == "page"), None)
        if not page:
            raise RuntimeError("no page target in chromium")
        host, port, path = ws_url_parse(page["webSocketDebuggerUrl"])
        self.ws = WsClient(host, port, path)

    def cdp(self, method: str, params: dict | None = None, timeout: float = 30) -> dict:
        """Send one CDP command, return its matching response object."""
        mid = self._next_id()
        send_json(self.ws, {"id": mid, "method": method, "params": params or {}})
        while True:
            msg = recv_json(self.ws, timeout)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP error in {method}: {msg['error']}")
                return msg.get("result", {})
            # events and other responses are ignored here

    # -- page actions -----------------------------------------------------

    def goto(self, url: str) -> None:
        self.cdp("Page.enable")
        self.cdp("Page.navigate", {"url": url})
        self._wait_load()

    def _wait_load(self, timeout: float = 15) -> None:
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ready = self.eval_js("document.readyState")
            if ready == "complete":
                return
            time.sleep(0.1)

    def eval_js(self, expr: str) -> Any:
        res = self.cdp("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
        })
        if "exceptionDetails" in res:
            raise RuntimeError(f"JS error: {res['exceptionDetails'].get('text')}")
        return res.get("result", {}).get("value")

    def dom_map(self) -> list[dict]:
        """One cleaned DOM perception: visible interactive elements + labels.

        This is the 'eyes' of the agent: what a person sees as buttons and
        inputs, delivered as a compact list the planner (LLM or rules) can
        reason over.
        """
        return self.eval_js("""
        (() => {
          const out = [];
          const sels = 'a[href], button, input, textarea, select, [role=button], [onclick]';
          for (const el of document.querySelectorAll(sels)) {
            const r = el.getBoundingClientRect();
            if (r.width === 0 && r.height === 0) continue;
            const id = el.id ? '#' + el.id : '';
            const name = el.getAttribute('name') || '';
            const label =
              (el.labels && el.labels[0] ? el.labels[0].innerText.trim() : '') ||
              el.innerText.trim().slice(0, 40) ||
              el.getAttribute('placeholder') || el.getAttribute('aria-label') || '';
            out.push({
              tag: el.tagName.toLowerCase(),
              id, name,
              label: String(label).slice(0, 60),
              type: el.getAttribute('type') || '',
              selector: id ? id : (name ? `${el.tagName.toLowerCase()}[name="${name}"]` : ''),
            });
          }
          return out;
        })()
        """)

    def set_value(self, selector: str, value: str) -> None:
        safe = selector.replace("'", "\\'")
        self.eval_js(
            f"document.querySelector('{safe}').value = {json.dumps(value)}"
        )

    def click(self, selector: str) -> None:
        safe = selector.replace("'", "\\'")
        self.eval_js(f"document.querySelector('{safe}').click()")

    def submit_form(self, selector: str) -> None:
        safe = selector.replace("'", "\\'")
        self.eval_js(
            f"(document.querySelector('{safe}')||document.forms[0]).submit()"
        )

    def cookies(self) -> list[dict]:
        return self.cdp("Network.getCookies").get("cookies", [])
