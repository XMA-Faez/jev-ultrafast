"""Public demo shop: log in, add an item, fill checkout details, and pause before the order is finished."""

from . import collapsed, outcome

URL = "https://www.saucedemo.com/"
GOAL = (
    "Log in as standard_user with password secret_sauce, add the Sauce Labs Backpack to the cart, check out "
    "with first name Ana, last name Diaz, postal code 10001, and finish the order."
)
PAUSE_BEFORE = ["finish"]
ON_PAUSE = "stop"


def verify(page, extracted=None):
    text = collapsed(page.get("text"))
    checks = {
        "overview": page.get("url", "").endswith("/checkout-step-two.html"),
        "backpack": "sauce labs backpack" in text,
        "not_ordered": "thank you for your order" not in text,
    }
    return outcome(checks)
