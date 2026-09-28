"""Keyboard execution against a private headless Chrome. No model calls or external websites."""

import time

import pytest

from jev_ultrafast import browser as browser_module
from jev_ultrafast.browser import StalePage


def action(page, **fields):
    return next(a for a in page["actions"] if all(a.get(k) == v for k, v in fields.items()))


def test_enter_submits_a_form_without_a_submit_control(open_browser):
    browser = open_browser(page="submit_on_enter.html")
    page = browser.observe(screenshot=False)
    browser.act(action(page, kind="fill", label="Search"), page, text="zurich hotels")
    page = browser.observe(screenshot=False)
    result = browser.act(action(page, id="press_enter"), page)
    assert result == {"executed": "press_enter"}
    page = browser.observe(screenshot=False)
    assert "Searched for zurich hotels" in page["text"]


def test_escape_closes_an_open_dialog(open_browser):
    browser = open_browser(page="submit_on_enter.html")
    page = browser.observe(screenshot=False)
    browser.act(action(page, kind="click", label="Open dialog"), page)
    page = browser.observe(screenshot=False)
    assert any(a["label"] == "Inside dialog" for a in page["actions"])
    browser.act(action(page, id="press_escape"), page)
    page = browser.observe(screenshot=False)
    assert browser.evaluate("document.getElementById('dialog').open") is False
    assert not any(a["label"] == "Inside dialog" for a in page["actions"])


def test_key_actions_use_the_full_page_guard(open_browser):
    browser = open_browser(page="submit_on_enter.html")
    page = browser.observe(screenshot=False)
    browser.evaluate("document.getElementById('query').value='changed elsewhere'")
    with pytest.raises(StalePage):
        browser.act(action(page, id="press_enter"), page)
    assert "Searched for" not in browser.observe(screenshot=False)["text"]


def test_headless_tab_is_foreground_and_screenshots_after_idle(open_browser):
    browser = open_browser(html="<h1>Idle page</h1>")
    assert browser.background_tab is False
    time.sleep(4.5)
    started = time.monotonic()
    page = browser.observe(screenshot=True)
    assert page["screenshot"] and time.monotonic() - started < 3


def test_background_tab_screenshot_wakes_the_compositor_after_idle(open_browser):
    open_browser(html="<p>keeps the daemon running</p>")
    target = browser_module.cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
    try:
        session = browser_module.attach_tab(target)
        browser_module.cdp("Page.navigate", session_id=session, url="data:text/html,<h1>Background</h1>")
        time.sleep(4.5)
        started = time.monotonic()
        assert browser_module.capture_screenshot(session, background_tab=True)
        assert time.monotonic() - started < 3
    finally:
        browser_module.cdp("Target.closeTarget", targetId=target)


def test_wait_returns_once_content_appears(open_browser):
    browser = open_browser(html="<button>Start</button><p id='status'>Loading</p>")
    page = browser.observe(screenshot=False)
    browser.evaluate("setTimeout(()=>document.getElementById('status').textContent='Hello World!',1000)")
    started = time.monotonic()
    browser.act(action(page, id="wait"), page)
    waited = time.monotonic() - started
    assert 0.8 < waited < 2
    assert "Hello World!" in browser.observe(screenshot=False)["text"]


def test_wait_stops_at_its_cap_when_nothing_changes(open_browser):
    browser = open_browser(html="<p>Nothing will change</p>")
    page = browser.observe(screenshot=False)
    started = time.monotonic()
    assert browser.act(action(page, id="wait"), page) == {"executed": "wait"}
    assert browser_module.WAIT_CAP_SECONDS <= time.monotonic() - started < browser_module.WAIT_CAP_SECONDS + 1


def open_dialog_with(browser, label):
    page = browser.observe(screenshot=False)
    button = action(page, label=label)
    started = time.monotonic()
    outcome = browser.act(button, page)
    assert time.monotonic() - started < browser_module.INPUT_RESPONSE_SECONDS + 1
    assert outcome["executed"] == button["id"]
    return outcome, browser.observe(screenshot=False)


@pytest.mark.parametrize(
    ("label", "choose", "result"),
    [("Show confirm", "accept_dialog", "Confirmed"), ("Show confirm", "dismiss_dialog", "Cancelled"),
     ("Show alert", "accept_dialog", "Alert closed"), ("Show prompt", "dismiss_dialog", "No answer")],
)
def test_dialog_opened_by_a_click_is_observed_and_answered(open_browser, label, choose, result):
    browser = open_browser(page="dialog_buttons.html")
    outcome, page = open_dialog_with(browser, label)
    dialog_type = label.split()[-1]
    assert outcome["dialog"]["type"] == dialog_type
    assert page["dialog"]["type"] == dialog_type
    assert page["text"].startswith(f"A {dialog_type} dialog is open: ")
    controls = {a["id"] for a in page["actions"] if a["kind"] == "dialog"}
    assert controls == ({"accept_dialog"} if dialog_type == "alert" else {"accept_dialog", "dismiss_dialog"})
    assert browser.fresh(page)
    browser.act(action(page, id=choose), page)
    assert not browser.fresh(page)
    page = browser.observe(screenshot=False)
    assert "dialog" not in page
    assert result in page["text"]


def test_prompt_answer_is_typed_into_the_dialog(open_browser):
    browser = open_browser(page="dialog_buttons.html")
    _, page = open_dialog_with(browser, "Show prompt")
    answer = action(page, kind="fill")
    assert answer["dialog_prompt"] is True and answer["value"] == "Guest" and type(answer["node"]) is int
    browser.act(answer, page, text="Ada")
    assert "Hello Ada" in browser.observe(screenshot=False)["text"]


def test_dialog_opened_later_invalidates_the_page_and_is_observed(open_browser):
    browser = open_browser(page="dialog_buttons.html")
    page = browser.observe(screenshot=False)
    assert "dialog" not in browser.act(action(page, label="Alert later"), page)
    page = browser.observe(screenshot=False)
    deadline = time.monotonic() + 2
    while browser.open_dialog() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not browser.fresh(page)
    page = browser.observe(screenshot=False)
    assert page["dialog"]["message"] == "Later alert"
    browser.act(action(page, id="accept_dialog"), page)
    assert "dialog" not in browser.observe(screenshot=False)


def test_answering_a_dialog_that_already_closed_is_stale(open_browser):
    browser = open_browser(page="dialog_buttons.html")
    _, page = open_dialog_with(browser, "Show confirm")
    browser.call("Page.handleJavaScriptDialog", accept=False)
    assert not browser.fresh(page)
    with pytest.raises(StalePage):
        browser.act(action(page, id="accept_dialog"), page)
    assert "Cancelled" in browser.observe(screenshot=False)["text"]


def test_short_text_is_typed_as_key_presses(open_browser):
    browser = open_browser(page="keys_listener.html")
    page = browser.observe(screenshot=False)
    browser.act(action(page, kind="fill", label="Letter"), page, text="Qé")
    assert browser.evaluate("document.getElementById('letter').value") == "Qé"
    assert browser.evaluate("document.getElementById('keys').textContent").endswith(" Q é")
    assert browser.evaluate("document.getElementById('codes').textContent").endswith(" 81 0")


def test_multi_line_text_is_inserted_whole(open_browser):
    browser = open_browser(page="keys_listener.html")
    page = browser.observe(screenshot=False)
    browser.act(action(page, kind="fill", label="Notes"), page, text="first\nsecond")
    assert browser.evaluate("document.getElementById('notes').value") == "first\nsecond"
