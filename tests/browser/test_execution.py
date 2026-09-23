"""Keyboard execution against a private headless Chrome. No model calls or external websites."""

import pytest

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
