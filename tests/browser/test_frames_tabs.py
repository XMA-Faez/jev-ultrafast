"""Navigation, new tabs and same-origin frames against a private headless Chrome and a local HTTP server."""

import functools
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from jev_ultrafast import browser as browser_module
from jev_ultrafast.browser import StalePage
from tests.browser.conftest import PAGES


class DelayedPagesHandler(SimpleHTTPRequestHandler):
    """Serves tests/browser/pages; ?delay=<ms> holds the response back like a slow server."""

    def do_GET(self):
        delay = parse_qs(urlparse(self.path).query).get("delay", ["0"])[0]
        time.sleep(int(delay) / 1000)
        super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def served():
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(DelayedPagesHandler, directory=str(PAGES)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


def action(page, **fields):
    return next(a for a in page["actions"] if all(a.get(k) == v for k, v in fields.items()))


def live_page_targets():
    targets = browser_module.cdp("Target.getTargets")["targetInfos"]
    return {info["targetId"] for info in targets if info["type"] == "page"}


@pytest.mark.parametrize("link", ["Next page", "Slow page"])
def test_click_that_navigates_is_observed_on_the_new_document(open_browser, served, link):
    browser = open_browser(url=served + "navigate.html")
    page = browser.observe(screenshot=False)
    assert browser.act(action(page, label=link), page) == {"executed": action(page, label=link)["id"]}
    page = browser.observe(screenshot=False)
    assert page["title"] == "Popup target"
    assert "Arrived at the target page" in page["text"]
    assert browser.fresh(page)


@pytest.mark.parametrize("suffix", ["?from=navigate", "?delay=400"])
def test_navigate_loads_a_new_url_in_the_same_tab(open_browser, served, suffix):
    browser = open_browser(url=served + "navigate.html")
    tab, tabs_before = browser.target, live_page_targets()

    browser.navigate(served + "popup_target.html" + suffix)

    assert browser.evaluate("document.title") == "Popup target"
    assert browser.target == tab
    assert live_page_targets() == tabs_before
    page = browser.observe(screenshot=False)
    assert "Arrived at the target page" in page["text"]


@pytest.mark.parametrize(
    ("control", "suffix"),
    [("Open in new tab", "?via=link"), ("Open popup window", "?via=open"), ("Open popup later", "?via=late")],
)
def test_new_tab_is_adopted_and_close_closes_every_owned_tab(open_browser, served, control, suffix):
    browser = open_browser(url=served + "popup.html")
    opener = browser.target
    page = browser.observe(screenshot=False)
    adopted = browser.act(action(page, label=control), page).get("new_tab")
    deadline = time.monotonic() + 2
    while True:
        page = browser.observe(screenshot=False)
        adopted = adopted or page.get("new_tab")
        if adopted or time.monotonic() > deadline:
            break
        time.sleep(0.02)
    assert adopted == {"url": served + "popup_target.html" + suffix, "title": "Popup target"}
    assert browser.target != opener
    assert browser.tabs[-1][0] == opener
    assert page["url"].endswith(suffix)
    browser.act(action(page, label="Target button"), page)
    assert "Target clicked" in browser.observe(screenshot=False)["text"]
    owned = {opener, browser.target}
    assert owned <= live_page_targets()
    browser.close()
    deadline = time.monotonic() + 2
    while owned & live_page_targets() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not owned & live_page_targets()


def test_popup_that_closes_itself_returns_to_its_opener(open_browser, served):
    browser = open_browser(url=served + "popup.html")
    opener = browser.target
    page = browser.observe(screenshot=False)
    browser.act(action(page, label="Open popup window"), page)
    page = browser.observe(screenshot=False)
    browser.act(action(page, label="Close this window"), page)
    page = browser.observe(screenshot=False)
    assert browser.target == opener
    assert page["title"] == "Popup opener"


def test_same_origin_iframe_controls_are_observed_and_executed(open_browser, served):
    browser = open_browser(url=served + "iframe_parent.html")
    page = browser.observe(screenshot=False)
    name = action(page, kind="fill", label="Name")
    send = action(page, kind="click", label="Send greeting")
    top = action(page, kind="click", label="Top button")
    assert "frame" not in top
    assert name["frame"] == send["frame"] == page["frames"][0]["id"]
    element_ids = [a["id"] for a in page["actions"] if "node" in a]
    assert element_ids == [f"e{i}" for i in range(1, len(element_ids) + 1)]
    assert "Waiting" in page["text"] and "Parent document" in page["text"]
    assert page["skipped_frames"] == 0

    rect = name["rect"]
    center = rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2
    assert browser.evaluate("document.elementFromPoint(%f, %f).id" % center) == "child"

    browser.act(name, page, text="Ada")
    assert browser.evaluate("document.getElementById('name').value", frame=name["frame"]) == "Ada"
    page = browser.observe(screenshot=False)
    browser.act(action(page, kind="click", label="Send greeting"), page)
    page = browser.observe(screenshot=False)
    assert "Hello Ada" in page["text"]
    assert browser.fresh(page)


def test_iframe_guard_invalidates_when_its_field_changes(open_browser, served):
    browser = open_browser(url=served + "iframe_parent.html")
    page = browser.observe(screenshot=False)
    send = action(page, kind="click", label="Send greeting")
    assert browser.fresh(page, send)
    browser.evaluate("document.getElementById('name').value='changed elsewhere'", frame=send["frame"])
    assert not browser.fresh(page, send)
    assert not browser.fresh(page)
    with pytest.raises(StalePage):
        browser.act(send, page)
    assert "Hello" not in browser.observe(screenshot=False)["text"]


def test_iframe_navigation_renews_its_isolated_world(open_browser, served):
    browser = open_browser(url=served + "iframe_parent.html")
    page = browser.observe(screenshot=False)
    browser.evaluate("document.getElementById('child').src='iframe_child.html?again'")
    browser.wait_until("document.getElementById('child').contentDocument?.readyState==='complete' && "
                       "document.getElementById('child').contentWindow.location.search==='?again'", 3)
    assert not browser.fresh(page)
    page = browser.observe(screenshot=False)
    assert action(page, kind="fill", label="Name")["frame"] == page["frames"][0]["id"]


def test_cross_origin_iframe_is_skipped_and_counted(open_browser, served):
    browser = open_browser(html=f'<button>Outer</button><iframe src="{served}iframe_child.html"></iframe>')
    page = browser.observe(screenshot=False)
    assert page["skipped_frames"] == 1
    assert "frames" not in page
    assert not any(a.get("frame") for a in page["actions"])
    assert browser.fresh(page)


def test_go_back_is_offered_only_with_history_and_returns(open_browser, served):
    browser = open_browser(url=served + "back_start.html")
    page = browser.observe(screenshot=False)
    assert not any(a["kind"] == "back" for a in page["actions"])
    browser.act(action(page, label="Next page"), page)
    page = browser.observe(screenshot=False)
    back = action(page, id="go_back")
    assert back["label"] == "Go back to the previous page (Back start)"
    assert [a["id"] for a in page["actions"]][-2:] == ["go_back", "wait"]
    assert browser.act(back, page) == {"executed": "go_back"}
    page = browser.observe(screenshot=False)
    assert page["title"] == "Back start" and "Start page" in page["text"]


def test_go_back_undoes_a_same_document_entry(open_browser, served):
    browser = open_browser(url=served + "back_start.html")
    page = browser.observe(screenshot=False)
    browser.act(action(page, label="Show details"), page)
    page = browser.observe(screenshot=False)
    assert page["url"].endswith("#details")
    browser.act(action(page, id="go_back"), page)
    assert browser.observe(screenshot=False)["url"] == served + "back_start.html"


def test_go_back_is_stale_when_history_changed_since_observation(open_browser, served):
    browser = open_browser(url=served + "back_start.html")
    page = browser.observe(screenshot=False)
    browser.act(action(page, label="Next page"), page)
    page = browser.observe(screenshot=False)
    back = action(page, id="go_back")
    browser.call("Page.navigate", url=served + "back_start.html?again")
    browser.wait_until("document.readyState==='complete'", 3)
    with pytest.raises(StalePage):
        browser.act(back, page)
    assert browser.evaluate("location.search") == "?again"


def test_go_back_history_guard_refuses_a_changed_entry_even_with_an_unchanged_page(open_browser, served):
    browser = open_browser(url=served + "back_start.html")
    page = browser.observe(screenshot=False)
    browser.act(action(page, label="Next page"), page)
    page = browser.observe(screenshot=False)
    moved = {**action(page, id="go_back"), "entry": -1}
    with pytest.raises(StalePage, match="history changed"):
        browser.act(moved, page)
    assert browser.observe(screenshot=False)["title"] == "Back next"
