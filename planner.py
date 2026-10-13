"""Planner + task runner: the agent loop that ties REST and browser together.

Two planner modes:
  - "rules"  : deterministic selector heuristics (default; zero LLM needed)
  - "llm"    : pass the DOM map to any JSON-emitting planner callable

The loop for every task: try REST -> on fallback-trigger status, open the
browser, perceive (dom_map), plan (rules or LLM), act, verify, retry up to
N times, then report success/failure with evidence.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from agent import Browser, FALLBACK_TRIGGERS, rest


# --------------------------------------------------------------------------
# Rules planner (deterministic, offline)
# --------------------------------------------------------------------------

def rules_plan(goal: str, dom: list[dict], state: dict) -> list[dict]:
    """Pick actions for a goal using labels/roles, not coordinates.

    Understands the small grammar the fixture site (and most simple sites)
    speaks: login (fill user+pass, submit), post text (fill composer,
    click Post). state carries keys filled so far.
    """
    actions: list[dict] = []
    goal_l = goal.lower()

    def find(pred) -> dict | None:
        return next((e for e in dom if pred(e)), None)

    if "login" in goal_l or "sign in" in goal_l:
        user_el = find(lambda e: e["tag"] == "input" and e["name"] == "user") or \
                  find(lambda e: e["tag"] == "input" and "user" in (e["id"] + e["name"]).lower())
        pass_el = find(lambda e: e["tag"] == "input" and e["type"] == "password")
        form = find(lambda e: e["tag"] == "form" or e["tag"] == "button")
        if user_el and pass_el:
            actions = [
                {"act": "fill", "selector": user_el["selector"], "value": state.get("user", "testuser")},
                {"act": "fill", "selector": pass_el["selector"], "value": state.get("password", "testpass")},
                {"act": "submit", "selector": "#login-form"},
            ]
    elif "post" in goal_l or "publish" in goal_l or "share" in goal_l:
        composer = find(lambda e: e["tag"] == "textarea" or
                        (e["tag"] == "input" and e["type"] == "text" and
                         ("composer" in e["id"].lower() or "post" in (e["label"] or "").lower())))
        post_btn = find(lambda e: e["tag"] == "button" and "post" in (e["label"] or "").lower())
        if composer and post_btn:
            actions = [
                {"act": "fill", "selector": composer["selector"], "value": state["text"]},
                {"act": "click", "selector": post_btn["selector"]},
            ]
        elif composer:  # enter-key style sites
            actions = [
                {"act": "fill", "selector": composer["selector"], "value": state["text"]},
                {"act": "submit", "selector": "#composer"},
            ]
    return actions


# --------------------------------------------------------------------------
# Agent loop
# --------------------------------------------------------------------------

MAX_RETRIES = 3


def run_task(goal: str, base_url: str, state: dict,
             planner: Callable[[str, list[dict], dict], list[dict]] = rules_plan,
             browser_factory: Callable[[], Browser] | None = None,
             rest_call: Callable = rest) -> dict:
    """Execute one task REST-first with browser fallback. Returns a report."""
    report: dict[str, Any] = {
        "goal": goal, "steps": [], "success": False, "via": None, "retries": 0,
    }

    def step(kind: str, detail: str) -> None:
        report["steps"].append({"t": round(time.monotonic(), 3), "kind": kind, "detail": detail})

    # 1) REST first
    r = rest_call("POST", base_url.rstrip("/") + "/api/posts", body={"text": state.get("text", "")})
    step("rest", f"POST /api/posts -> {r.status}")
    if r.ok:
        report["success"] = True
        report["via"] = "rest"
        return report
    if r.status not in FALLBACK_TRIGGERS:
        step("abort", f"non-fallback status {r.status}")
        return report

    # 2) Browser fallback
    b = (browser_factory() if browser_factory else Browser())
    b.start()
    b.attach_page()
    try:
        b.goto(base_url)
        for attempt in range(1, MAX_RETRIES + 1):
            report["retries"] = attempt
            dom = b.dom_map()
            actions = planner(goal, dom, state)
            if not actions:
                step("plan", f"attempt {attempt}: planner found no actions")
                # maybe we need login first
                needs_login = any(e.get("type") == "password" for e in dom)
                if needs_login:
                    login_actions = planner("login", dom, state)
                    step("plan", f"switching to login sub-goal ({len(login_actions)} actions)")
                    actions = login_actions
                if not actions:
                    break
            executed = []
            for a in actions:
                if a["act"] == "fill":
                    b.set_value(a["selector"], a["value"])
                elif a["act"] == "click":
                    b.click(a["selector"])
                elif a["act"] == "submit":
                    b.submit_form(a["selector"])
                executed.append(a["act"])
            step("act", f"attempt {attempt}: executed {executed}")
            time.sleep(0.6)  # allow navigation/post-submit settle
            b.goto(base_url)  # re-perceive post-navigate
            dom = b.dom_map()
            if verify_posted(b, state):
                report["success"] = True
            report["via"] = "browser"
            if report["success"]:
                return report
        return report
    finally:
        b.stop()


def verify_posted(b: Browser, state: dict) -> bool:
    """Postcondition: our exact text now exists in the feed DOM."""
    try:
        needle = state.get("text", "")
        if not needle:
            return False
        found = b.eval_js(
            "Array.from(document.querySelectorAll('.post, #feed li'))"
            ".some(li => li.innerText.includes(" + json.dumps(needle) + "))"
        )
        return bool(found)
    except Exception:
        return False
