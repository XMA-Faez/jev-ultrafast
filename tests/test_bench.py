"""Offline benchmark checks: task contracts, verifiers, runner bookkeeping, scorecard rendering. No model calls."""

import json
import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from urllib.request import urlopen

import pytest

from bench import report
from bench import run as bench_run
from bench.server import LocalPages
from bench.tasks import TASK_NAMES, load_task
from bench.tasks import flights as flights_task

LOCAL = "http://127.0.0.1:40123"
PAGES = Path(__file__).resolve().parents[1] / "bench" / "pages"


def action(label, value="", role="button", kind="click"):
    return {"label": label, "value": value, "role": role, "kind": kind}


def flights_page(departure):
    return {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": f"Track prices from Zürich to London departing {departure.isoformat()}",
        "actions": [
            action("Change ticket type. One way", "One way"),
            action("Where from?", "Zürich"),
            action("Where to?", "London"),
            action("Departure", f"{departure:%a, %b} {departure.day}"),
            action(f"Nonstop flight on {departure:%A, %B} {departure.day}. Select flight"),
        ],
    }


PASSING = {
    "flights": flights_page(flights_task.DEPARTURE),
    "travel_fixture": {
        "url": f"{LOCAL}/fixture.html?scenario=travel#casa-flora",
        "title": "Casa Flora · Forma",
        "text": "Casa Flora\nYour filters: Design · Free cancellation enabled · Destination Lisbon",
        "actions": [action("← All stays")],
    },
    "research_fixture": {
        "url": f"{LOCAL}/fixture.html?scenario=research#choices",
        "title": "A browser is a choice, not a conversation · Forma",
        "text": "A browser is a choice, not a conversation\nFreshness is part of correctness. Bind every decision.",
        "actions": [],
    },
    "wikipedia": {
        "url": "https://en.wikipedia.org/wiki/G%C3%B6del%27s_incompleteness_theorems",
        "title": "Gödel's incompleteness theorems - Wikipedia",
        "text": "Gödel's incompleteness theorems",
        "actions": [],
    },
    "duckduckgo": {
        "url": "https://duckduckgo.com/?q=python+pathlib+documentation&ia=web",
        "title": "python pathlib documentation at DuckDuckGo",
        "text": "pathlib — Object-oriented filesystem paths",
        "actions": [
            action("Search", "python pathlib documentation", role="searchbox", kind="fill"),
            action("pathlib — Object-oriented filesystem paths — Python 3 documentation", role="link"),
            action("Python's pathlib Module: Taming the File System", role="link"),
        ],
    },
    "python_docs": {
        "url": "https://docs.python.org/3/library/pathlib.html",
        "title": "pathlib — Object-oriented filesystem paths — Python 3.14.7 documentation",
        "text": "pathlib — Object-oriented filesystem paths",
        "actions": [],
    },
    "hacker_news": {
        "url": "https://news.ycombinator.com/newest",
        "title": "New Links | Hacker News",
        "text": "Hacker News new | past | comments",
        "actions": [action(f"Story {n}", role="link") for n in range(12)],
    },
    "keyboard_search": {
        "url": f"{LOCAL}/keyboard_search.html?q=tide%20pools",
        "title": "Field notes archive",
        "text": "2 results for “tide pools”\nTide pools at low water\nCounting anemones in the tide pools",
        "actions": [action("Search field notes", "tide pools", role="searchbox", kind="fill")],
    },
    "shadow_settings": {
        "url": f"{LOCAL}/shadow_settings.html?digest=weekly&weekends=muted&topic=browser+agents",
        "title": "Alert settings",
        "text": "Settings saved: weekly digest · weekend alerts muted · topic browser agents",
        "actions": [],
    },
    "iframe_booking": {
        "url": f"{LOCAL}/iframe_booking.html?date=2026-10-14&guests=4",
        "title": "Harbor Hall · Visits",
        "text": "Harbor Hall\nBooking requested for 4 guests on 2026-10-14",
        "actions": [],
    },
    "popup_docs": {
        "url": f"{LOCAL}/popup_pricing.html",
        "title": "Pricing · Lumen CLI",
        "text": "Pricing\nTeam\n$24 per month when billed monthly.\n$240 per year when billed annually.",
        "actions": [],
    },
    "checkpoint": {
        "url": f"{LOCAL}/checkpoint.html",
        "title": "Projects · Workbench",
        "text": "Projects\nAtlas 12 files\nBeacon 4 files",
        "actions": [action("Delete project Atlas"), action("Delete project Beacon")],
    },
}
EXTRACTED = {"popup_docs": {"monthly_price": "$24"}}


def replace_url(new_url):
    return lambda page: page.update(url=new_url)


def replace_text(old, new):
    return lambda page: page.update(text=page["text"].replace(old, new))


def set_value(label, value):
    return lambda page: next(a for a in page["actions"] if a["label"] == label).update(value=value)


MUTATIONS = [
    ("flights", "wrong origin", set_value("Where from?", "Basel")),
    ("travel_fixture", "other property", replace_url(f"{LOCAL}/fixture.html?scenario=travel#the-glasshouse")),
    ("travel_fixture", "filter off", replace_text("Free cancellation enabled", "Free cancellation off")),
    ("travel_fixture", "no destination", replace_text("Destination Lisbon", "Destination anywhere")),
    ("research_fixture", "other article", replace_url(f"{LOCAL}/fixture.html?scenario=research#latency")),
    ("wikipedia", "other article", replace_url("https://en.wikipedia.org/wiki/Kurt_G%C3%B6del")),
    ("wikipedia", "other language", replace_url("https://de.wikipedia.org/wiki/G%C3%B6del%27s_incompleteness_theorems")),
    ("duckduckgo", "homepage", replace_url("https://duckduckgo.com/")),
    ("duckduckgo", "no results", lambda page: page.update(actions=page["actions"][:1])),
    ("python_docs", "other module", replace_url("https://docs.python.org/3/library/os.path.html")),
    ("hacker_news", "front page", replace_url("https://news.ycombinator.com/news")),
    ("keyboard_search", "not submitted", replace_url(f"{LOCAL}/keyboard_search.html")),
    ("keyboard_search", "no matches", replace_text("2 results", "0 results")),
    ("shadow_settings", "daily digest", replace_url(f"{LOCAL}/shadow_settings.html?digest=daily&weekends=muted")),
    ("shadow_settings", "not saved", lambda page: page.update(text="")),
    ("iframe_booking", "wrong date", replace_url(f"{LOCAL}/iframe_booking.html?date=2026-10-15&guests=4")),
    ("iframe_booking", "wrong guests", replace_url(f"{LOCAL}/iframe_booking.html?date=2026-10-14&guests=2")),
    ("popup_docs", "stayed on docs", replace_url(f"{LOCAL}/popup_docs.html")),
    ("checkpoint", "deleted", replace_url(f"{LOCAL}/checkpoint.html?deleted=atlas")),
    ("checkpoint", "row gone", lambda page: page.update(actions=page["actions"][1:])),
]


@pytest.mark.parametrize("name", TASK_NAMES)
def test_task_module_contract(name):
    task = load_task(name)
    assert isinstance(task.URL, str) and re.match(r"^(https://|\{local\}/)", task.URL)
    assert isinstance(task.GOAL, str) and task.GOAL.strip() and "\n" not in task.GOAL
    assert callable(task.verify)
    assert set(getattr(task, "EXTRACT", None) or {}) <= {"monthly_price"}
    if task.URL.startswith("{local}/") and "fixture.html" not in task.URL:
        assert (PAGES / task.URL.removeprefix("{local}/").split("?")[0]).is_file()


def test_every_task_has_a_passing_page_and_a_failing_mutation():
    assert set(PASSING) == set(TASK_NAMES)
    assert {name for name, _, _ in MUTATIONS} == set(TASK_NAMES)


@pytest.mark.parametrize("name", TASK_NAMES)
def test_verifier_accepts_the_hand_built_passing_page(name):
    outcome = load_task(name).verify(deepcopy(PASSING[name]), deepcopy(EXTRACTED.get(name)))
    assert outcome["passed"], outcome["checks"]


@pytest.mark.parametrize(("name", "_description", "mutate"), MUTATIONS, ids=[f"{n}-{d}" for n, d, _ in MUTATIONS])
def test_verifier_rejects_a_wrong_final_page(name, _description, mutate):
    page = deepcopy(PASSING[name])
    mutate(page)
    assert not load_task(name).verify(page, deepcopy(EXTRACTED.get(name)))["passed"]


@pytest.mark.parametrize("extracted", [None, {}, {"monthly_price": None}, {"monthly_price": "$240"}])
def test_popup_verifier_needs_the_extracted_monthly_price(extracted):
    assert not load_task("popup_docs").verify(deepcopy(PASSING["popup_docs"]), extracted)["passed"]


def test_flights_example_keeps_the_recorded_trip_while_the_benchmark_rolls_forward():
    from examples import flights

    assert "September 20, 2026" in flights.GOALS
    assert flights.verify(flights_page(flights_task.RECORDED_DEPARTURE))["passed"]
    assert flights_task.DEPARTURE > date.today()
    assert f"{flights_task.DEPARTURE.day}, {flights_task.DEPARTURE.year}" in flights_task.GOAL
    assert not flights_task.verify(flights_page(flights_task.RECORDED_DEPARTURE))["passed"]


@pytest.mark.parametrize("page", sorted(PAGES.glob("*.html")), ids=lambda path: path.name)
def test_local_pages_make_no_external_requests(page):
    assert not re.search(r"""(src|href|action)\s*=\s*["']?(https?:)?//""", page.read_text())
    assert "fetch(" not in page.read_text() and "import(" not in page.read_text()


def test_local_server_serves_bench_pages_and_the_inspector_fixture_only():
    with LocalPages() as pages:
        assert "Field notes archive" in urlopen(pages.url("{local}/keyboard_search.html")).read().decode()
        assert "Casa Flora" in urlopen(pages.url("{local}/fixture.html?scenario=travel")).read().decode()
        for forbidden in ("/../pyproject.toml", "/%2e%2e/pyproject.toml", "/missing.html", "/"):
            with pytest.raises(Exception, match="404"):
                urlopen(pages.origin + forbidden)


class FakeBrowser:
    def __init__(self, final_page):
        self.final_page = final_page

    def observe(self, screenshot=True):
        return deepcopy(self.final_page)


class FakeAgent:
    """Replays statuses; approve/reject record the answer. Stands in for the paid Agent loop."""

    instances = []

    def __init__(self, url, goals, **options):
        self.url, self.goals, self.options = url, goals, options
        self.statuses = list(FakeAgent.scripted_statuses)
        self.browser = FakeBrowser(FakeAgent.final_page)
        self.answers, self.closed = [], False
        self.state = dict(status="ready", decisions=[], text_calls=[], history=[], elapsed_ms=0, reason=None)
        FakeAgent.instances.append(self)

    def snapshot(self):
        return deepcopy(self.state)

    def run(self):
        while self.state["status"] not in {"done", "blocked", "paused"} and self.statuses:
            self.state["status"] = self.statuses.pop(0)
            self.state["decisions"].append({"usage": {"input_tokens": 100, "output_tokens": 7}})
            self.state["elapsed_ms"] += 250
            yield self.snapshot()

    def reject(self):
        self.answers.append("reject")
        self.state["status"] = "ready"
        self.state["history"].append({"kind": "note", "action": "user rejected"})
        return self.snapshot()

    def approve(self):
        self.answers.append("approve")
        self.state["status"] = "ready"
        return self.snapshot()

    def close(self):
        self.closed = True


@pytest.fixture
def fake_agent(monkeypatch, tmp_path):
    FakeAgent.instances = []
    monkeypatch.setattr(bench_run, "Agent", FakeAgent)
    return bench_run.parse_arguments(["--tasks", "checkpoint,popup_docs", "--out", str(tmp_path / "bench")])


@pytest.fixture
def local_pages():
    with LocalPages() as pages:
        yield pages


def run_task(name, arguments, pages):
    return bench_run.run_record(name, 1, load_task(name), pages, arguments, None)


def test_runner_rejects_checkpoint_pauses_and_verifies_a_fresh_observation(fake_agent, local_pages):
    FakeAgent.scripted_statuses = ["predicted", "paused", "paused", "blocked"]
    FakeAgent.final_page = PASSING["checkpoint"]
    record = run_task("checkpoint", fake_agent, local_pages)
    agent = FakeAgent.instances[0]
    assert agent.answers == ["reject", "reject"] and agent.closed
    assert agent.options["pause_before"] == ["delete"] and agent.url.startswith("http://127.0.0.1:")
    assert record["passed"] and record["checks"]["paused"] and record["pauses"] == 2
    assert record["jev_requests"] == 3 and record["input_tokens"] == 300 and record["output_tokens"] == 21
    assert record["actions"] == 0


def test_runner_fails_a_checkpoint_run_that_never_paused(fake_agent, local_pages):
    FakeAgent.scripted_statuses = ["predicted", "done"]
    FakeAgent.final_page = PASSING["checkpoint"]
    record = run_task("checkpoint", fake_agent, local_pages)
    assert not record["passed"] and record["checks"]["paused"] is False and record["status"] == "done"


def test_runner_trusts_the_verifier_not_done(fake_agent, local_pages):
    FakeAgent.scripted_statuses = ["done"]
    FakeAgent.final_page = PASSING["popup_docs"]
    record = run_task("popup_docs", fake_agent, local_pages)
    assert record["status"] == "done" and not record["passed"] and not record["checks"]["extracted_price"]
    assert FakeAgent.instances[0].options["extract"] == load_task("popup_docs").EXTRACT


def test_runner_records_a_time_budget_error_without_retrying(fake_agent, local_pages):
    FakeAgent.scripted_statuses = ["predicted"] * 5 + ["done"]
    FakeAgent.final_page = PASSING["checkpoint"]
    fake_agent.time_budget = -1
    record = run_task("checkpoint", fake_agent, local_pages)
    assert record["error"].startswith("TimeBudgetExceeded") and not record["passed"]
    assert len(FakeAgent.instances) == 1 and FakeAgent.instances[0].closed


def test_report_renders_pass_rates_and_medians_from_results(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    results = {
        "started_at": "2026-09-23T10:00:00+00:00",
        "commit": "abc1234",
        "uncommitted_changes": True,
        "models": {"decision_model": "jev-test", "text_model": "text-test"},
        "headless": True,
        "repeat": 2,
        "time_budget_seconds": 120,
        "platform": "Linux",
        "runs": [
            {"task": "wikipedia", "goal": "Open it.", "passed": True, "checks": {"host": True},
             "error": None, "elapsed_ms": 2000, "jev_requests": 4, "text_calls": 1},
            {"task": "wikipedia", "goal": "Open it.", "passed": False, "checks": {"article_path": False},
             "error": None, "elapsed_ms": 4000, "jev_requests": 8, "text_calls": 1},
            {"task": "checkpoint", "goal": "Delete it.", "passed": False, "checks": {},
             "error": "TimeBudgetExceeded: Time budget exceeded", "elapsed_ms": None, "jev_requests": 2},
        ],
    }
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "results.json").write_text(json.dumps(results))
    output = tmp_path / "benchmark.md"
    report.main([str(folder), "--output", str(output)])
    rendered = output.read_text()
    assert "abc1234` (with uncommitted changes)" in rendered and "2026-09-23T10:00:00+00:00" in rendered
    assert "`jev-test`" in rendered and "`text-test`" in rendered
    assert "**Overall: 1/3 runs passed (33%).**" in rendered
    assert "| wikipedia | 1/2 | 50% | 3.00 s | 6 | 1 | article_path ×1 |" in rendered
    assert "| checkpoint | 0/1 | 0% | – | 2 | – | TimeBudgetExceeded ×1 |" in rendered


def test_runner_writes_results_that_the_report_renders(fake_agent, tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    FakeAgent.scripted_statuses = ["predicted", "paused", "blocked"]
    FakeAgent.final_page = PASSING["checkpoint"]
    folder = tmp_path / "bench"
    bench_run.main(["--tasks", "checkpoint", "--repeat", "2", "--out", str(folder)])
    results = json.loads((folder / "results.json").read_text())
    assert [run["run"] for run in results["runs"]] == [1, 2] and all(run["passed"] for run in results["runs"])
    assert results["tasks"] == ["checkpoint"] and "commit" in results and "models" in results
    output = tmp_path / "benchmark.md"
    report.main([str(folder / "results.json"), "--output", str(output)])
    assert "| checkpoint | 2/2 | 100% |" in output.read_text()
    assert "2/2 runs passed" in capsys.readouterr().out
