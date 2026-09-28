"""Offline run state: typed state mapping, multi-goal plans, restart, stop reasons, headless launch."""

from copy import deepcopy
from unittest.mock import Mock, call

import pytest
from test_agent import act_now, decision, make_runner, page, scripted_choices

from jev_ultrafast import agent as loop
from jev_ultrafast import launch
from jev_ultrafast.browser import fingerprint
from jev_ultrafast.questions import MAX_STEPS

DONE = decision("DONE", "DONE", None)
GO = decision("e3", "CLICK", "2")


@pytest.fixture
def runner():
    return make_runner()


def results_page(number):
    later = deepcopy(page())
    later["url"] = f"https://example.test/results/{number}"
    later["fingerprint"] = fingerprint(later)
    return later


def test_run_state_is_a_mapping_for_existing_consumers():
    state = loop.RunState(browser=object(), goal="Find a book", plan=["Find a book"])
    state["scenario"] = "travel"
    state.update(status="ready", elapsed_ms=12)
    state.update({"history": [{"kind": "note", "action": "x"}]})
    assert state["goal"] == "Find a book" and state.goal == "Find a book"
    assert state["scenario"] == "travel" and state.get("scenario") == "travel"
    assert state.get("missing", 3) == 3 and "status" in state and "missing" not in state
    data = state.to_dict()
    assert "browser" not in data and data["scenario"] == "travel" and data["elapsed_ms"] == 12
    for key in ("status", "reason", "pending", "verification", "extracted", "plan", "plan_index", "goal", "trace_path"):
        assert key in data


def test_snapshot_keeps_existing_keys(runner):
    snapshot = runner.snapshot()
    for key in ("page", "decision", "history", "status", "plan", "plan_index", "decisions", "text_calls",
                "elapsed_ms", "started_at", "record", "goal", "elements"):
        assert key in snapshot


def test_goals_run_sequentially_and_advance_the_plan(monkeypatch):
    runner = make_runner(goals=["Search for books", "Open the first result"], done_threshold=None)
    runner.state.update(status="ready", decision=None)
    runner.state["browser"].observe.side_effect = [results_page(1), results_page(2)]
    calls = scripted_choices(monkeypatch, GO, DONE, GO, DONE)

    snapshots = list(runner.run())

    assert snapshots[-1]["status"] == "done" and snapshots[-1]["plan_index"] == 2
    assert [c["goal"] for c in calls] == ["Search for books"] * 2 + ["Open the first result"] * 2
    assert [c["completed_goals"] for c in calls] == [[], [], ["Search for books"], ["Search for books"]]
    notes = [h["action"] for h in snapshots[-1]["history"] if h["kind"] == "note"]
    assert notes == ["goal 1 complete"]
    after_first_goal = snapshots[1]
    assert after_first_goal["status"] == "ready" and after_first_goal["goal"] == "Open the first result"
    assert after_first_goal["plan_index"] == 1


def test_verifier_rejections_reset_per_goal(monkeypatch):
    runner = make_runner(goals=["First", "Second"], done_threshold=0.5)
    probabilities = iter([0.1, 0.1, 0.3, 0.1])
    monkeypatch.setattr(loop, "verify_done", lambda *_: {"probability": next(probabilities), "latency_ms": 1,
                                                         "usage": {}})
    for _ in range(3):
        runner.state["decision"] = dict(DONE)
        act_now(runner)
    assert runner.state["goal"] == "Second" and runner.state["status"] == "ready"
    assert runner.state["done_rejections"] == 0
    runner.state["decision"] = dict(DONE)
    assert act_now(runner)["status"] == "ready"


def test_restart_starts_new_goals_on_the_same_tab(runner):
    runner.state.update(status="done", reason="model chose DONE", history=[{"kind": "note", "action": "x"}],
                        decisions=[{}], plan_index=1, done_rejections=2)
    runner.pending_text = ("context", "text", {})
    snapshot = runner.restart(["Find a pen", "Add it to the cart"])
    assert snapshot["goal"] == "Find a pen" and snapshot["plan"] == ["Find a pen", "Add it to the cart"]
    assert snapshot["status"] == "ready" and snapshot["reason"] is None and snapshot["plan_index"] == 0
    assert snapshot["history"] == [] and snapshot["decisions"] == [] and snapshot["done_rejections"] == 0
    assert runner.pending_text is None and runner.state["browser"] is not None
    with pytest.raises(ValueError, match="Supply a task"):
        runner.restart(["  "])


def test_repeated_action_blocks_before_a_third_execution(runner):
    for _ in range(3):
        runner.state["decision"] = dict(GO)
        snapshot = act_now(runner)
    assert snapshot["status"] == "blocked" and snapshot["reason"] == "repeated action"
    assert runner.state["browser"].act.call_count == 2


def test_three_unchanged_actions_block_with_a_reason(runner):
    for chosen in (GO, decision("e2", "CLICK", "1"), GO):
        runner.state["decision"] = dict(chosen)
        snapshot = act_now(runner)
    assert snapshot["status"] == "blocked" and snapshot["reason"] == "no page change after 3 actions"


def test_model_blocked_and_budget_reasons(runner):
    runner.state["decision"] = decision("BLOCKED", "BLOCKED", None)
    assert act_now(runner)["reason"] == "model chose BLOCKED"
    budget = make_runner()
    budget.state["history"] = [{"kind": "click", "choice": f"x{i}", "text": None} for i in range(MAX_STEPS)]
    budget.state["decision"] = dict(GO)
    with pytest.raises(ValueError, match="budget"):
        act_now(budget)
    assert budget.state["status"] == "blocked" and budget.state["reason"] == "action budget"


def test_new_tab_is_noted_after_the_execution(runner):
    popup = {"url": "https://popup.test/", "title": "P"}
    runner.state["browser"].act.return_value = {"executed": "e3", "new_tab": popup}
    runner.state["browser"].observe.return_value = results_page(1)
    runner.state["decision"] = dict(GO)
    history = act_now(runner)["history"]
    assert history[0]["action"] == "Go" and history[0]["page_changed"] is True
    assert history[1] == {"kind": "note", "action": "switched to new tab https://popup.test/",
                          "elapsed_ms": history[1]["elapsed_ms"]}


@pytest.mark.parametrize("headless, env, launches", [(True, "", True), (None, "1", True), (None, "", False),
                                                      (False, "1", False)])
def test_headless_launches_private_chrome_first(monkeypatch, tmp_path, headless, env, launches):
    order = Mock()
    chrome = order.chrome
    monkeypatch.setenv("JEV_HEADLESS", env)
    monkeypatch.setenv("JEV_PROFILE_DIR", str(tmp_path))
    monkeypatch.setattr(launch, "launch_chrome", Mock(side_effect=lambda **_: (order.launch(), chrome)[1]))
    browser = order.browser
    browser.observe.return_value = page()
    monkeypatch.setattr(loop, "Browser", Mock(side_effect=lambda url: (order.open(url), browser)[1]))

    agent = loop.Agent("https://example.test/", "Find a book", headless=headless, trace_dir=False)
    agent.close()

    if launches:
        launch.launch_chrome.assert_called_once_with(profile_dir=tmp_path)
        assert order.mock_calls[:2] == [call.launch(), call.open("https://example.test/")]
        assert order.mock_calls[-2:] == [call.browser.close(), call.chrome.close()]
    else:
        launch.launch_chrome.assert_not_called()
        chrome.close.assert_not_called()


def test_failed_first_observation_closes_launched_chrome(monkeypatch):
    chrome = Mock()
    monkeypatch.setattr(launch, "launch_chrome", Mock(return_value=chrome))
    browser = Mock(observe=Mock(side_effect=RuntimeError("no tab")))
    monkeypatch.setattr(loop, "Browser", Mock(return_value=browser))
    with pytest.raises(RuntimeError):
        loop.Agent("https://example.test/", "Find a book", headless=True, trace_dir=False)
    browser.close.assert_called_once()
    chrome.close.assert_called_once()


def test_constructor_validates_goals_and_extract(monkeypatch):
    monkeypatch.setattr(loop, "Browser", Mock(return_value=Mock(observe=Mock(return_value=page()))))
    with pytest.raises(ValueError, match="Supply a task"):
        loop.Agent("https://example.test/", [" ", ""], headless=False)
    with pytest.raises(ValueError, match="extract"):
        loop.Agent("https://example.test/", "Find", headless=False, extract={})
    agent = loop.Agent("https://example.test/", ["First", "Second"], headless=False, trace_dir=False,
                       pause_before="checkout")
    assert agent.state["plan"] == ["First", "Second"] and agent.state["goal"] == "First"
    assert agent.pause_before == ["checkout"] and agent.done_threshold == 0.5
    agent.close()


def test_restart_replaces_checkpoints_and_extraction_only_when_given():
    from jev_ultrafast import agent as loop

    runner = loop.Agent.__new__(loop.Agent)
    runner.state = loop.RunState(goal="first", plan=["first"], page={"actions": []})
    runner.trace = None
    runner.pending_text = None
    runner.pause_before, runner.extract = ["delete"], {"price": "monthly price"}
    runner.restart("second")
    assert runner.pause_before == ["delete"] and runner.extract == {"price": "monthly price"}
    runner.restart("third", pause_before=["pay"], extract={"total": "order total"})
    assert runner.pause_before == ["pay"] and runner.extract == {"total": "order total"}
    assert runner.state["plan"] == ["third"]
