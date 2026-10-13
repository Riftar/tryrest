"""Fixtures for tryrest - a tiny local social site + an API, for offline tests.

Everything here is synthetic. No real names, no personal data, no network
beyond 127.0.0.1.

  - fixtures/site/        : static HTML/CSS/JS for "Microblog" (login,
                            composer, feed). Served by fixture_server.py.
  - fixtures/api/         : a small "REST API" the agent should prefer.
  - fixture_server.py     : serves the site (http) and the API on one port.
  - tasks/                : task suite with machine-checkable assertions.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))

# The API's synthetic content: article + feed items used by tasks/tests.
ARTICLE = {
    "id": "art-001",
    "title": "Local-first tools beat cloud-first promises",
    "body": ("Small tools that run without internet keep working when the "
             "network does not. Boring beats clever during outages."),
    "tags": ["local-first", "offline", "tools"],
}

FEED = [
    {"id": "p-100", "author": "ada", "text": "shipped the offline test rig"},
    {"id": "p-101", "author": "bruno", "text": "reviewing the fixture site PR"},
    {"id": "p-102", "author": "citra", "text": "dupe-canary: never posted"},
]

# NOTE: "dupe-canary" marks a feed item that must never be reposted; the
# exactly-once task asserts no second copy of that text ever appears.
