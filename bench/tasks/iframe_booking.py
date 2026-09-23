"""A booking form inside a same-origin iframe with a native date input and a range slider."""

from . import is_local_page, outcome, query_value

URL = "{local}/iframe_booking.html"
GOAL = "Request a visit to Harbor Hall for 4 guests on October 14, 2026."
EXPECTED_DATE = "2026-10-14"
EXPECTED_GUESTS = "4"


def verify(page, extracted=None):
    checks = {
        "page": is_local_page(page, "iframe_booking.html"),
        "date": query_value(page, "date") == EXPECTED_DATE,
        "guests": query_value(page, "guests") == EXPECTED_GUESTS,
        "confirmation": f"Booking requested for {EXPECTED_GUESTS} guests on {EXPECTED_DATE}" in page.get("text", ""),
    }
    return outcome(checks)
