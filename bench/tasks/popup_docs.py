"""A target=_blank link opens pricing in a new tab; the answer is returned through structured extraction."""

import re

from . import is_local_page, outcome

URL = "{local}/popup_docs.html"
GOAL = "Open the Lumen pricing page and report the monthly price of the Team plan."
EXTRACT = {"monthly_price": "The Team plan's monthly price as shown on the pricing page"}
MONTHLY_PRICE = re.compile(r"(?<![\d.])24(\.00)?(?![\d.])")


def verify(page, extracted=None):
    price = (extracted or {}).get("monthly_price")
    checks = {
        "pricing_tab": is_local_page(page, "popup_pricing.html"),
        "pricing_visible": "$24 per month" in page.get("text", ""),
        "extracted_price": price is not None and bool(MONTHLY_PRICE.search(str(price))),
    }
    return outcome(checks, extracted_price=price)
