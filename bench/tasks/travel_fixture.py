"""The inspector's local stay search: destination search, category, checkbox filter, then a detail page."""

import re
from urllib.parse import urlparse

from . import is_local_page, outcome

URL = "{local}/fixture.html?scenario=travel"
GOAL = "Find a Design stay in Lisbon with Free cancellation and open Casa Flora."
APPLIED_FILTERS = re.compile(
    r"Your filters: (?P<category>.+?) · Free cancellation (?P<free>enabled|off) · Destination (?P<place>.+)"
)


def verify(page, extracted=None):
    filters = APPLIED_FILTERS.search(page.get("text", ""))
    checks = {
        "detail_page": is_local_page(page, "fixture.html") and urlparse(page["url"]).fragment == "casa-flora",
        "property": page.get("title", "").startswith("Casa Flora"),
        "category": bool(filters) and filters["category"] == "Design",
        "free_cancellation": bool(filters) and filters["free"] == "enabled",
        "destination": bool(filters) and filters["place"].strip().lower() == "lisbon",
    }
    return outcome(checks)
