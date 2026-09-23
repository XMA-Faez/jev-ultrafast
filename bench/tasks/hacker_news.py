"""Public site navigation: open Hacker News' newest-submissions listing from the front page."""

from urllib.parse import urlparse

from . import outcome

URL = "https://news.ycombinator.com/"
GOAL = "Open the Hacker News page that lists the newest submissions."
MINIMUM_VISIBLE_LINKS = 10


def verify(page, extracted=None):
    parsed = urlparse(page["url"])
    links = [a for a in page.get("actions", []) if a.get("role") == "link"]
    checks = {
        "newest_page": parsed.hostname == "news.ycombinator.com" and parsed.path == "/newest",
        "title": page.get("title", "").startswith("New Links"),
        "listing": len(links) >= MINIMUM_VISIBLE_LINKS,
    }
    return outcome(checks)
