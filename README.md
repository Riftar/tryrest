# tryrest

REST-first agent with automatic browser fallback — no SDK, no Playwright,
no third-party dependencies. When the API 404s, it spawns headless
Chromium and finishes the job through the DOM via raw CDP over a
stdlib-only WebSocket.

## How it works

1. **REST first.** The action is attempted against the API (`urllib`).
2. **Fallback trigger.** 404/405 on the write endpoint means "no API" —
   not "dead end."
3. **Browser fallback.** Headless Chromium starts, driven over the
   DevTools WebSocket (`ws.py`, ~200 lines of stdlib sockets implementing
   just enough RFC 6455).
4. **Perceive → plan → act → verify.** `dom_map()` reads visible controls
   with labels/names/roles; a rules planner (or any LLM callable) picks
   actions; the harness executes them; the postcondition is verified in
   the DOM and the report includes every step.

## Quick start

Terminal 1 (fixture site — reads via API, writes 404 on purpose):

```bash
python3 fixture_server.py 8791
```

Terminal 2 (the agent):

```bash
python3 cli.py --base http://127.0.0.1:8791 --goal post --text "hello world"
```

The report shows the REST 404, the browser fallback, the executed actions,
and `success: true` when the post appears in the feed (exactly once).

## Tests (offline; only needs a Chromium binary on PATH)

```bash
python3 tests/test_all.py
```

- WebSocket frame encode/decode round-trips
- Fixture behavior: write-API 404, login, cookie-gated fallback route
- Full agent loop against real headless Chromium, with DB-level
  exactly-once assertion
- **Drift test:** ids scrambled + wrapper divs injected (simulated
  redesign) — perception and planning must still work

## Layout

- `agent.py` — REST client + raw-CDP `Browser` class
- `planner.py` — rules planner + the `run_task` agent loop
- `cli.py` — one-task CLI
- `ws.py` — minimal RFC 6455 WebSocket client (stdlib)
- `fixture_server.py` + `fixtures/` — the offline "Microblog" site/API
- `tests/test_all.py` — the offline suite
- `PLAN.md` — problem, goals, 3 checkpoints, rollback

## License

MIT
