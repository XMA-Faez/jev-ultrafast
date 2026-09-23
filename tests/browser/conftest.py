"""Real-browser tests against a private headless Chrome. No model calls, no external websites."""

import os
from pathlib import Path
from urllib.parse import quote

import pytest

from jev_ultrafast import settings

PAGES = Path(__file__).with_name("pages")


@pytest.fixture(scope="session")
def chrome(request):
    if os.environ.get("JEV_SKIP_BROWSER_TESTS") == "1":
        pytest.skip("JEV_SKIP_BROWSER_TESTS=1")
    if request.config.getoption("--real-browser"):
        yield None
        return
    if not settings.chrome_path():
        pytest.skip("No Chrome or Chromium found; set JEV_CHROME_PATH")
    from jev_ultrafast.launch import launch_chrome

    launched = launch_chrome()
    yield launched
    launched.close()


@pytest.fixture
def open_browser(chrome):
    """Open a Browser on inline HTML or a page from tests/browser/pages; closes its tab afterwards."""
    from jev_ultrafast.browser import Browser

    opened = []

    def open_page(html=None, page=None, url=None):
        if page:
            url = (PAGES / page).resolve().as_uri()
        elif html is not None:
            url = "data:text/html," + quote(html)
        browser = Browser(url)
        opened.append(browser)
        return browser

    yield open_page
    for browser in opened:
        browser.close()
