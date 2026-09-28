"""Public httpbin form: text, tel, email, radio, checkboxes, time and textarea, verified from the echoed POST."""

import json
import re
from urllib.parse import urlparse

from . import collapsed, outcome

URL = "https://httpbin.org/forms/post"
GOAL = (
    "Order a large pizza with bacon and extra cheese for Alex Doe (phone 555-0100, email alex@example.com), "
    "delivery at 19:30, with the instruction 'Ring twice'. Submit the order."
)


def echoed_form(page):
    match = re.search(r"\{.*\}", page.get("text", ""), re.S)
    try:
        return json.loads(match.group(0)).get("form", {}) if match else {}
    except ValueError:
        return {}


def verify(page, extracted=None):
    form = echoed_form(page)
    toppings = form.get("topping", [])
    toppings = sorted(toppings if isinstance(toppings, list) else [toppings])
    checks = {
        "submitted": urlparse(page.get("url", "")).path == "/post",
        "name": collapsed(form.get("custname")) == "alex doe",
        "size": form.get("size") == "large",
        "toppings": toppings == ["bacon", "cheese"],
        "delivery": form.get("delivery") == "19:30",
        "comments": "ring twice" in collapsed(form.get("comments")),
    }
    return outcome(checks)
