"""Local search box without a submit button: only Enter runs the search."""

import re

from . import collapsed, is_local_page, outcome, query_value

URL = "{local}/keyboard_search.html"
GOAL = "Search the field notes for tide pools and show the matching notes."
RESULT_COUNT = re.compile(r"(\d+) results for “(.+?)”")
EXPECTED_NOTE = "Tide pools at low water"


def verify(page, extracted=None):
    text = page.get("text", "")
    counted = RESULT_COUNT.search(text)
    checks = {
        "page": is_local_page(page, "keyboard_search.html"),
        "submitted_query": "tide pool" in collapsed(query_value(page, "q")),
        "results_listed": bool(counted) and int(counted[1]) > 0,
        "matching_note_visible": EXPECTED_NOTE in text,
    }
    return outcome(checks)
