"""Snapshot vocabulary in a real browser: key controls, native value inputs and open shadow roots."""

import pytest

from jev_ultrafast.browser import StalePage


def action_ids(page):
    return [a["id"] for a in page["actions"]]


def labelled(page, label, kind=None):
    return next(a for a in page["actions"] if a["label"] == label and kind in (None, a["kind"]))


def test_enter_and_escape_are_always_offered_before_wait(open_browser):
    page = open_browser(page="keyboard.html").observe(screenshot=False)
    assert action_ids(page)[-3:] == ["press_enter", "press_escape", "wait"]
    controls = {a["id"]: a for a in page["actions"] if a["kind"] == "key"}
    assert controls["press_enter"]["key"] == "Enter"
    assert controls["press_enter"]["label"] == "Press Enter in the focused field"
    assert controls["press_escape"]["key"] == "Escape"
    assert controls["press_escape"]["label"] == "Press Escape to close a menu or dialog"
    assert "arrow_down" not in controls and "arrow_up" not in controls


@pytest.mark.parametrize("focused", ["#airport", "#menu"], ids=["combobox", "expanded"])
def test_arrow_keys_are_offered_only_while_a_list_control_is_focused(open_browser, focused):
    browser = open_browser(page="keyboard.html")
    browser.evaluate("document.querySelector('#query').focus()")
    assert "arrow_down" not in action_ids(browser.observe(screenshot=False))
    browser.evaluate(f"document.querySelector('{focused}').focus()")
    page = browser.observe(screenshot=False)
    assert action_ids(page)[-5:] == ["press_enter", "press_escape", "arrow_down", "arrow_up", "wait"]
    arrows = {a["id"]: (a["key"], a["label"]) for a in page["actions"] if a["id"].startswith("arrow_")}
    assert arrows == {"arrow_down": ("ArrowDown", "Press Arrow Down"), "arrow_up": ("ArrowUp", "Press Arrow Up")}


def test_focus_change_invalidates_the_page_marker(open_browser):
    browser = open_browser(page="keyboard.html")
    page = browser.observe(screenshot=False)
    assert browser.fresh(page)
    browser.evaluate("document.querySelector('#query').focus()")
    assert not browser.fresh(page)


def test_native_value_inputs_are_fills_with_a_format_hint(open_browser):
    actions = open_browser(page="native_inputs.html").observe(screenshot=False)["actions"]
    fills = {a["label"]: a for a in actions if a["kind"] == "fill"}
    assert {label: (a["role"], a["format"], a["native_value"]) for label, a in fills.items()} == {
        "Departure": ("textbox", "YYYY-MM-DD", True),
        "Alarm": ("textbox", "HH:MM", True),
        "Volume": ("slider", "number 0..100 step 5", True),
        "Accent": ("textbox", "#rrggbb", True),
    }
    clicks = {a["label"] for a in actions if a["kind"] == "click"}
    assert {"Open Departure", "Open Alarm"} <= clicks
    assert not {"Open Volume", "Open Accent"} & clicks


@pytest.mark.parametrize("label,element,value", [
    ("Departure", "departure", "2026-10-01"),
    ("Alarm", "alarm", "07:30"),
    ("Volume", "volume", "35"),
    ("Accent", "accent", "#336699"),
])
def test_native_value_fill_sets_the_value_and_fires_change(open_browser, label, element, value):
    browser = open_browser(page="native_inputs.html")
    page = browser.observe(screenshot=False)
    assert browser.act(labelled(page, label, "fill"), page, text=value) == {"executed": labelled(page, label)["id"]}
    assert browser.evaluate(f"document.querySelector('#{element}').value") == value
    assert browser.evaluate("window.changes") == [element]


@pytest.mark.parametrize("label,value", [("Departure", "next friday"), ("Volume", "37")])
def test_native_value_fill_rejected_by_the_browser_is_not_stale(open_browser, label, value):
    browser = open_browser(page="native_inputs.html")
    page = browser.observe(screenshot=False)
    with pytest.raises(RuntimeError, match="Field rejected the value") as raised:
        browser.act(labelled(page, label, "fill"), page, text=value)
    assert not isinstance(raised.value, StalePage)


def test_open_shadow_roots_are_observed_in_composed_order(open_browser):
    page = open_browser(page="shadow.html").observe(screenshot=False)
    labels = [a["label"] for a in page["actions"] if a.get("node") is not None and not a["label"].startswith("Open ")]
    assert labels == ["Before", "Coupon", "Gift message", "Apply coupon", "Nested action", "After"]
    assert "Shadow total: $42" in page["text"]
    assert "Nested action" in page["text"]


def test_shadow_button_click_executes(open_browser):
    browser = open_browser(page="shadow.html")
    page = browser.observe(screenshot=False)
    browser.act(labelled(page, "Apply coupon"), page)
    assert browser.evaluate("window.applied") == 1


def test_shadow_field_fill_types_text(open_browser):
    browser = open_browser(page="shadow.html")
    page = browser.observe(screenshot=False)
    browser.act(labelled(page, "Coupon", "fill"), page, text="SAVE10")
    assert browser.evaluate(
        "document.querySelector('checkout-panel').shadowRoot.querySelector('#coupon').value"
    ) == "SAVE10"
    assert browser.observe(screenshot=False)["page_key"] != page["page_key"]


def test_overlay_inside_a_shadow_root_blocks_the_click(open_browser):
    browser = open_browser(page="shadow.html")
    page = browser.observe(screenshot=False)
    browser.evaluate("const cover=document.createElement('div'); "
                     "cover.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; "
                     "document.querySelector('checkout-panel').shadowRoot.append(cover)")
    with pytest.raises(StalePage):
        browser.act(labelled(page, "Apply coupon"), page)
    assert browser.evaluate("window.applied") is None
