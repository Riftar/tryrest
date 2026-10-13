# PLAN — tryrest

## The real problem

Most sites are half-API: reads work over REST, writes 404 (or the API is
undocumented, moved, or was never public). Integrations treat that 404 as a
dead end and throw away the working 90%. The only universal fallback —
driving the real UI in a browser — normally means pulling Playwright or
Selenium: hundreds of megabytes of dependency for what is, underneath, one
WebSocket conversation with Chromium's DevTools endpoint.

**tryrest's answer:** try REST first; when the response hits a defined
fallback trigger (404/405), spawn headless Chromium and finish the task
through the DOM over raw CDP — using a ~200-line stdlib WebSocket client,
not an SDK.

## Goals — what this can become at its highest

The ceiling is a **dependency-free UI-automation harness**:

- Any site with a partial API becomes scriptable: the fast path uses REST,
  the missing pieces are completed through the rendered page.
- Session persistence via a Chromium profile directory: log in once (human,
  ever), reuse the cookie for every subsequent headless run.
- A perception layer (`dom_map`) that describes pages the way a person
  reads them — labels, roles, names — so planners can be deterministic
  rules today and any LLM tomorrow, with the executor unchanged.
- A regression rig: the bundled fixture site + task suite + drift injection
  prove the agent still works after a "redesign," with zero external
  services.

## Non-goals (deliberate)

- No vision/screenshot tier (Canvas apps, remote desktops). DOM only.
- No anti-bot evasion beyond a real persistent profile — hostile sites stay
  out of scope.
- No scheduler/orchestration. It's a library + CLI; cron belongs elsewhere.

## Checkpoint 1 — Proof of the loop (DONE in this commit)

REST-first call that 404s → headless Chromium (stdlib-launched, CDP over
`ws.py`) → perceive DOM → rules-planner actions → verify postcondition →
report with a step log. Includes the offline fixture ("Microblog": login,
composer, feed; reads via API, writes 404 by design), a CLI, and a 9-test
suite: WebSocket frame round-trips, fixture behavior (404 write API, cookie
gate, DB-level postconditions), the full agent loop with real Chromium
(login → post → exactly-once assertion), and a drift test that scrambles
ids/wraps forms and proves perception + planning survive.

## Checkpoint 2 — Robust execution (NEXT)

- Positional/CSS-fallback selectors when ids are absent (drift-proof
  *execution*, not just perception).
- Adapter freezing: serialize a successful action sequence to JSON, replay
  it deterministically; auto-fallback to agent mode when replay breaks.
- Screenshot receipts (`Page.captureScreenshot`) attached to reports.
- Session profile reuse across runs (login once).

## Checkpoint 3 — Planner swap + task suites

- `llm_plan`: pass the DOM map to any callable that emits the same action
  JSON (local llama.cpp or remote API — the harness doesn't care).
- `tasks/` directory: YAML/JSON task suites with machine-checkable
  assertions; a runner that prints the metrics table (success rate, steps,
  retries) per task.
- Chaos variants of the fixture site (modals, confirm dialogs, expired
  sessions) as failure-path tasks.

## Rollback / recovery

Greenfield library + local fixture: rollback is `git revert`. Runtime
state is confined to a scratch Chromium profile dir and the fixture's
sqlite file (both gitignored, both disposable). Nothing installs, mutates
the host browser, or touches anything outside 127.0.0.1.

## Offline guarantee

Python stdlib + a Chromium binary already on the machine. The fixture site
and API run on localhost; tests never leave the loopback interface. No
package installs, no model downloads, no network.
