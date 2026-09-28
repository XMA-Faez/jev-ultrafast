"""Each task module exports URL, GOAL and verify(page, extracted=None) -> {"passed", "checks"}.

URL may contain "{local}", which the runner replaces with the loopback origin of bench.server.
Optional: EXTRACT ({key: description}), PAUSE_BEFORE (label substrings), ON_PAUSE ("reject" | "approve" | "stop").
Verifiers read the final page (url, title, visible text, actions) and never trust the model's DONE.
"""

import importlib
from urllib.parse import parse_qs, urlparse

TASK_NAMES = (
    "flights",
    "travel_fixture",
    "research_fixture",
    "wikipedia",
    "duckduckgo",
    "python_docs",
    "hacker_news",
    "keyboard_search",
    "shadow_settings",
    "iframe_booking",
    "popup_docs",
    "checkpoint",
    "login_confirm",
    "slow_catalogue",
    "web_form",
    "shop_checkout",
    "map_search",
)


def load_task(name):
    if name not in TASK_NAMES:
        raise ValueError(f"Unknown task {name!r}; choose from {', '.join(TASK_NAMES)}")
    return importlib.import_module(f"{__name__}.{name}")


def outcome(checks, **details):
    return {"passed": all(checks.values()), "checks": checks, **details}


def collapsed(text):
    return " ".join(str(text or "").lower().split())


def query_value(page, key):
    return parse_qs(urlparse(page.get("url", "")).query).get(key, [""])[0]


def is_local_page(page, filename):
    parsed = urlparse(page.get("url", ""))
    return parsed.hostname == "127.0.0.1" and parsed.path == f"/{filename}"
