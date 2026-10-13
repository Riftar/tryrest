"""tryrest CLI - run one task against a base URL.

  python3 cli.py --base http://127.0.0.1:8791 --goal post --text "hello world"

Prints the JSON report (steps, via, success). Exits 0 on success.
"""

import argparse
import json
import sys

from planner import run_task

GOALS = {
    "post": "post text to the feed",
    "login": "sign in",
}


def main() -> int:
    ap = argparse.ArgumentParser(prog="tryrest")
    ap.add_argument("--base", required=True, help="base URL of the target site")
    ap.add_argument("--goal", choices=sorted(GOALS), default="post")
    ap.add_argument("--text", default="hello from tryrest")
    ap.add_argument("--user", default="testuser")
    ap.add_argument("--password", default="testpass")
    ap.add_argument("--chromium", default="chromium")
    args = ap.parse_args()

    state = {"text": args.text, "user": args.user, "password": args.password}
    report = run_task(GOALS[args.goal], args.base, state)
    print(json.dumps(report, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
