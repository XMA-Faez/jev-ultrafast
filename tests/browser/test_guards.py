"""Local-browser freshness/execution regressions. No model calls or external websites."""

import pytest

from jev_ultrafast.browser import StalePage

GUARD_PAGE = """<!doctype html><title>Guard checks</title>
<style>body{margin:30px}button{width:180px;height:50px}#outside{position:absolute;top:3000px}</style>
<p id="context">Cart total: $10</p>
<button id="target" onclick="window.clicks=(window.clicks||0)+1">Continue</button>
<label>City<input id="field" value="Zurich"></label>
<label><input id="toggle" type="checkbox">Refundable</label>
<select aria-label="Category"><option>All</option><option>Design</option></select>
<p id="outside">Unrelated offscreen text</p>"""

FORM_PAGE = """<!doctype html><title>Form guards</title>
<form><p id="price">Total $10</p>
<button type="button" id="buy">Buy</button>
<label>Search <input id="query" role="combobox" aria-controls="suggestions"></label>
<div role="listbox" id="suggestions"></div>
<label><input id="check" type="checkbox">Enabled</label>
<label><input id="radio" type="radio">Choice</label>
<input id="readonly" aria-label="Read only" readonly>
<input id="secret" type="password" value="never expose this">
<button id="off" disabled>Disabled</button>
<select id="category" aria-label="Category">
  <option>All</option><option>Design</option><option disabled>Unavailable</option>
</select></form><aside id="unrelated">News</aside>"""


def labelled(page, label):
    return next(a for a in page["actions"] if a["label"] == label)


@pytest.fixture
def guard_browser(open_browser):
    return open_browser(html=GUARD_PAGE)


@pytest.fixture
def form_browser(open_browser):
    return open_browser(html=FORM_PAGE)


def test_moving_target_is_clicked_at_its_current_location(guard_browser):
    page = guard_browser.observe(screenshot=False)
    guard_browser.evaluate("document.querySelector('#target').style.transform='translateX(200px)'")
    assert guard_browser.fresh(page), "Movement should use fresh geometry, not another model call"
    guard_browser.act(labelled(page, "Continue"), page)
    assert guard_browser.evaluate("window.clicks") == 1


def test_unrelated_offscreen_text_does_not_invalidate(guard_browser):
    page = guard_browser.observe(screenshot=False)
    guard_browser.evaluate("document.querySelector('#outside').textContent='Updated outside the viewport'")
    assert guard_browser.fresh(page)


@pytest.mark.parametrize("mutation", [
    "document.querySelector('#context').textContent='Cart total: $100'",
    "document.querySelector('#target').setAttribute('aria-label','Delete account')",
    "document.querySelector('#field').value='London'",
    "document.querySelector('#toggle').checked=true",
    "document.querySelector('#target').disabled=true",
    "document.querySelector('#field').readOnly=true",
    "document.querySelector('#target').style.display='none'",
    "document.querySelector('#target').outerHTML=document.querySelector('#target').outerHTML",
    "document.querySelector('select').options[1].text='Coastal'",
], ids=["visible context", "accessible label", "field property", "checkbox property", "disabled target",
        "read-only field", "hidden target", "replaced node", "dropdown option"])
def test_semantic_change_invalidates_page(guard_browser, mutation):
    page = guard_browser.observe(screenshot=False)
    guard_browser.evaluate(mutation)
    assert not guard_browser.fresh(page)


def test_textless_overlay_blocks_input_before_it_is_sent(guard_browser):
    page = guard_browser.observe(screenshot=False)
    guard_browser.evaluate("const cover=document.createElement('div'); "
                           "cover.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; "
                           "document.body.append(cover)")
    assert guard_browser.fresh(page), "A textless overlay does not alter the model's semantic state"
    with pytest.raises((RuntimeError, StalePage)):
        guard_browser.act(labelled(page, "Continue"), page)
    assert guard_browser.evaluate("window.clicks") is None


def test_click_guard_accepts_unrelated_updates_but_terminal_guard_rejects_them(form_browser):
    page = form_browser.observe(screenshot=False)
    buy = labelled(page, "Buy")
    form_browser.evaluate("document.querySelector('#unrelated').textContent='New unrelated news'")
    assert form_browser.fresh(page, buy)
    assert not form_browser.fresh(page)


@pytest.mark.parametrize("mutation", [
    "document.querySelector('#price').textContent='Total $100'",
    "document.querySelector('#query').value='changed'",
    "document.querySelector('#check').checked=true",
    "document.querySelector('#buy').outerHTML=document.querySelector('#buy').outerHTML",
], ids=["nearby price", "form value", "form toggle", "target replacement"])
def test_nearby_change_invalidates_action_specific_guard(form_browser, mutation):
    page = form_browser.observe(screenshot=False)
    buy = labelled(page, "Buy")
    form_browser.evaluate(mutation)
    assert not form_browser.fresh(page, buy)


def test_native_controls_expose_only_supported_operations_and_safe_values(form_browser):
    actions = form_browser.observe(screenshot=False)["actions"]
    for role in ("checkbox", "radio"):
        assert {a["kind"] for a in actions if a.get("role") == role} == {"click"}
    assert {a["kind"] for a in actions if a["label"] == "Read only"} == {"click"}
    assert not any(a["label"] == "Disabled" or a.get("value") == "never expose this" for a in actions)
    assert [a["value"] for a in actions if a["kind"] == "select"] == ["Design"]


def test_native_dropdown_selects_an_observed_option(form_browser):
    page = form_browser.observe(screenshot=False)
    form_browser.act(next(a for a in page["actions"] if a["kind"] == "select"), page)
    assert form_browser.evaluate("document.querySelector('#category').value") == "Design"


def test_real_text_input_waits_for_asynchronous_combobox_suggestions(form_browser):
    form_browser.evaluate("document.querySelector('#query').addEventListener('input',()=>setTimeout(()=>{"
                          "document.querySelector('#suggestions').innerHTML='<div role=option>Generated</div>'"
                          "},60))")
    page = form_browser.observe(screenshot=False)
    form_browser.act(next(a for a in page["actions"] if a["kind"] == "fill"), page, text="Generated")
    page = form_browser.observe(screenshot=False)
    assert form_browser.evaluate("document.querySelector('#query').value") == "Generated"
    assert any(a.get("role") == "option" for a in page["actions"])


def test_navigation_invalidates_the_old_document(form_browser):
    page = form_browser.observe(screenshot=False)
    field = next(a for a in page["actions"] if a["kind"] == "fill")
    form_browser.call("Page.navigate", url="about:blank")
    assert not form_browser.fresh(page, field)


COVERED_SELECT_PAGE = """<!doctype html><title>Sort</title>
<style>.dropdown{position:relative;display:inline-block}
select{width:200px;height:30px}
.dropdown span{position:absolute;inset:0;background:#eee;pointer-events:auto}</style>
<label>Sort by <span class="dropdown"><select id="sort" aria-label="Sort by">
<option value="featured">Featured</option><option value="price">Price: Low to High</option>
</select><span>Featured</span></span></label>"""


def test_native_dropdown_covered_by_a_styled_overlay_is_still_selected(open_browser):
    browser = open_browser(html=COVERED_SELECT_PAGE)
    browser.evaluate("document.getElementById('sort').addEventListener('change', e => window.sorted=e.target.value)")
    page = browser.observe(screenshot=False)
    sort = next(a for a in page["actions"] if a["kind"] == "select" and a["value"] == "price")
    covered = browser.evaluate("document.elementFromPoint(%f,%f).tagName" % (
        sort["rect"]["x"] + sort["rect"]["w"] / 2, sort["rect"]["y"] + sort["rect"]["h"] / 2))
    assert covered == "SPAN"
    browser.act(sort, page)
    assert browser.evaluate("window.sorted") == "price"
