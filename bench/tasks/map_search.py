"""OpenStreetMap: asynchronous search results in a side panel next to a canvas map."""

import re

from . import outcome

URL = "https://www.openstreetmap.org/"
GOAL = "Search OpenStreetMap for 'Eiffel Tower' and open the details of the result in Paris."
DETAILS_PATH = re.compile(r"openstreetmap\.org/(way|node|relation)/\d+")


def verify(page, extracted=None):
    checks = {
        "details": bool(DETAILS_PATH.search(page.get("url", ""))),
        "paris": "paris" in page.get("text", "").lower(),
    }
    return outcome(checks)
