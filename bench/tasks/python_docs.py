"""Public documentation navigation: open one standard-library module page."""

import re
from urllib.parse import urlparse

from . import outcome

URL = "https://docs.python.org/3/"
GOAL = "Open the Python standard library documentation page for the pathlib module."
MODULE_PAGE = re.compile(r"/3(\.\d+)?/library/pathlib\.html")


def verify(page, extracted=None):
    parsed = urlparse(page["url"])
    checks = {
        "host": parsed.hostname == "docs.python.org",
        "module_page": bool(MODULE_PAGE.fullmatch(parsed.path)),
        "title": page.get("title", "").startswith("pathlib"),
    }
    return outcome(checks)
