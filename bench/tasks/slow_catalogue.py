"""Local search whose results arrive after 2.5 s, with pagination below the fold: WAIT, then scroll."""

from . import is_local_page, outcome, query_value

URL = "{local}/slow_catalogue.html"
GOAL = "Search the catalogue for lamp and open page 2 of the results."


def verify(page, extracted=None):
    checks = {
        "page": is_local_page(page, "slow_catalogue.html"),
        "query": query_value(page, "q").strip().lower() == "lamp",
        "page_two": query_value(page, "page") == "2",
        "results_loaded": "page 2 of 2" in page.get("text", ""),
    }
    return outcome(checks)
