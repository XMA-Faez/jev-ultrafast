"""Public Wikipedia navigation: reach one exact article from the main page."""

from urllib.parse import unquote, urlparse

from . import outcome

URL = "https://en.wikipedia.org/wiki/Main_Page"
GOAL = "Find and open the Wikipedia article about Gödel’s incompleteness theorems."
ARTICLE_PATH = "/wiki/Gödel's_incompleteness_theorems"


def verify(page, extracted=None):
    parsed = urlparse(page["url"])
    checks = {
        "host": parsed.hostname == "en.wikipedia.org",
        "article_path": unquote(parsed.path) == ARTICLE_PATH,
        "title": page.get("title", "").startswith("Gödel's incompleteness theorems"),
    }
    return outcome(checks)
