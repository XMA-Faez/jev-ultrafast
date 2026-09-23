"""Live Google Flights search. Never selects or books a flight.

The benchmark departs 30 days after the run so the date is always searchable; the README's recorded
demo used September 20, 2026 (RECORDED_DEPARTURE), which examples/flights.py keeps for reproduction.
"""

import base64
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

URL = "https://www.google.com/travel/flights?hl=en"
RECORDED_DEPARTURE = date(2026, 9, 20)
DAYS_AHEAD = 30


def goal_for(departure):
    return (
        f"Find one-way flights from Zurich to London on {departure:%B} {departure.day}, {departure.year}, "
        "for one adult in economy. Stop when matching flight options are visible. Do not select or book a flight."
    )


def verifier_for(departure):
    iso_date = departure.isoformat()
    short_label = f"{departure:%a, %b} {departure.day}"
    long_label = f"{departure:%A, %B} {departure.day}"

    def verify(page, extracted=None):
        """Independent checks on the resulting page, not the model's DONE answer."""
        parsed = urlparse(page["url"])
        encoded = parse_qs(parsed.query).get("tfs", [""])[0]
        try:
            date_in_url = iso_date.encode() in base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except ValueError:
            date_in_url = False
        actions = page["actions"]
        values = {a["label"].strip(): a.get("value") for a in actions}
        flights = [a["label"] for a in actions if "Select flight" in a["label"]]
        checks = {
            "search_page": parsed.hostname == "www.google.com" and parsed.path == "/travel/flights/search",
            "one_way": values.get("Change ticket type. One way") == "One way",
            "origin": values.get("Where from?") == "Zürich",
            "destination": values.get("Where to?") == "London",
            "date": values.get("Departure") == short_label,
            "year": date_in_url or f"departing {iso_date}" in page["text"],
            "results": bool(flights) and all(long_label in f for f in flights),
        }
        return {"passed": all(checks.values()), "checks": checks, "visible_flights": flights}

    return verify


DEPARTURE = date.today() + timedelta(days=DAYS_AHEAD)
GOAL = goal_for(DEPARTURE)
verify = verifier_for(DEPARTURE)
