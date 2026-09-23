"""Offline tests for the inspector's command routing and validation. No browser, no model calls."""

import pytest

from jev_ultrafast import demo


class FakeAgent:
    def __init__(self, url, goals, **options):
        self.url, self.goals, self.options = url, goals, options
        self.calls = []

    def snapshot(self):
        return {"status": "paused", "pending": {"label": "Search"}, "verification": None, "history": []}

    def command(self, name, body):
        self.calls.append((name, body))

    def approve(self):
        self.calls.append(("approve", None))

    def reject(self):
        self.calls.append(("reject", None))

    def close(self):
        self.calls.append(("close", None))


@pytest.fixture(autouse=True)
def fake_agent(monkeypatch):
    monkeypatch.setattr(demo, "Agent", FakeAgent)
    monkeypatch.setattr(demo, "AGENT", None)
    monkeypatch.setattr(demo, "SCENARIO", None)


def start(**body):
    demo.command("reset", {"scenario": "travel", "goal": "Find a stay", **body})
    return demo.AGENT


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (None, None),
        ("", None),
        (" , ", None),
        ("Search, Book now ,", ["Search", "Book now"]),
        (["search", " book "], ["search", "book"]),
    ],
)
def test_pause_before_parsing(given, expected):
    agent = start(pause_before=given)

    assert agent.options["pause_before"] == expected


@pytest.mark.parametrize(
    "given",
    [",".join(f"word{i}" for i in range(11)), ["x" * 101], ["search", 3], {"search": True}],
)
def test_pause_before_rejects_invalid_values(given):
    with pytest.raises(ValueError):
        start(pause_before=given)
    assert demo.AGENT is None


def test_reset_validates_scenario_and_goal():
    with pytest.raises(ValueError, match="scenario"):
        demo.command("reset", {"scenario": "shop", "goal": "Find a stay"})
    with pytest.raises(ValueError, match="characters"):
        demo.command("reset", {"scenario": "travel", "goal": "   "})
    with pytest.raises(ValueError, match="characters"):
        demo.command("reset", {"scenario": "travel", "goal": ["not", "text"]})


def test_reset_opens_fixture_with_screenshots_and_reports_scenario():
    agent = start()

    assert agent.url == f"{demo.ORIGIN}/fixture.html?scenario=travel"
    assert agent.goals == "Find a stay"
    assert agent.options["screenshots"] is True
    state = demo.response_state()
    assert state["scenario"] == "travel"
    assert state["pending"] == {"label": "Search"}


def test_commands_need_a_started_demo():
    for name in ("approve", "reject", "tick"):
        with pytest.raises(ValueError, match="Start a demo first"):
            demo.command(name, {})


def test_approve_reject_and_loop_commands_are_routed():
    agent = start()

    demo.command("approve", {})
    demo.command("reject", {})
    demo.command("tick", {"fingerprint": "f1"})

    assert agent.calls == [("approve", None), ("reject", None), ("tick", {"fingerprint": "f1"})]


def test_reset_closes_the_previous_agent():
    first = start()
    start()

    assert first.calls == [("close", None)]


def test_idle_state_carries_new_snapshot_keys():
    state = demo.response_state()

    assert state["status"] == "idle"
    assert {"pending", "verification", "extracted", "reason"} <= state.keys()
