def test_headless_browser_observes_controls(open_browser):
    browser = open_browser(html="<button>Hi</button><input aria-label=City>")
    page = browser.observe(screenshot=False)
    kinds = {(a["kind"], a["label"]) for a in page["actions"]}
    assert ("click", "Hi") in kinds
    assert ("fill", "City") in kinds
