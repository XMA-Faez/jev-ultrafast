"""A settings form inside an open shadow root: native select, checkbox, text field, submit button."""

from . import collapsed, is_local_page, outcome, query_value

URL = "{local}/shadow_settings.html"
GOAL = (
    "In the alert settings, switch to a weekly digest, mute alerts on weekends, "
    "follow the topic browser agents, and save the settings."
)


def verify(page, extracted=None):
    checks = {
        "page": is_local_page(page, "shadow_settings.html"),
        "digest": query_value(page, "digest") == "weekly",
        "weekends_muted": query_value(page, "weekends") == "muted",
        "topic": collapsed(query_value(page, "topic")).rstrip("s") == "browser agent",
        "saved_notice": "Settings saved: weekly digest · weekend alerts muted" in page.get("text", ""),
    }
    return outcome(checks)
