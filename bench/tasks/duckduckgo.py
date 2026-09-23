"""Public web search: submit a query on DuckDuckGo and land on a results page with matching results."""

from urllib.parse import urlparse

from . import collapsed, outcome, query_value

URL = "https://duckduckgo.com/"
GOAL = "Search DuckDuckGo for the Python pathlib module documentation and show the results."
QUERY_TERM = "pathlib"
MINIMUM_MATCHING_RESULT_LINKS = 2


def verify(page, extracted=None):
    matching_links = [
        a["label"]
        for a in page.get("actions", [])
        if a.get("role") == "link" and a.get("kind") == "click" and QUERY_TERM in a.get("label", "").lower()
    ]
    checks = {
        "results_page": urlparse(page["url"]).hostname == "duckduckgo.com" and urlparse(page["url"]).path == "/",
        "query": QUERY_TERM in collapsed(query_value(page, "q")),
        "results": len(matching_links) >= MINIMUM_MATCHING_RESULT_LINKS,
    }
    return outcome(checks, matching_links=matching_links)
