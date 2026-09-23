"""The inspector's local reading room: open the article that matches a description, not its title."""

from urllib.parse import urlparse

from . import is_local_page, outcome

URL = "{local}/fixture.html?scenario=research"
GOAL = "Open the article about using finite choices to control browser agents."
ARTICLE_TITLE = "A browser is a choice, not a conversation"


def verify(page, extracted=None):
    text = page.get("text", "")
    checks = {
        "article_page": is_local_page(page, "fixture.html") and urlparse(page["url"]).fragment == "choices",
        "title": page.get("title", "") == f"{ARTICLE_TITLE} · Forma",
        "article_body": ARTICLE_TITLE in text and "Freshness is part of correctness." in text,
    }
    return outcome(checks)
