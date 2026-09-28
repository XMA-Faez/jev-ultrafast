"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model, questions
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1", operation="TYPE_TEXT", target="1"):
    return {
        "choice": action,
        "operation": operation,
        "target": target,
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


def make_runner(goals=("Find a book",), current=None, **agent_settings):
    """An Agent without a real browser: a Mock browser that keeps returning `current` (default: page())."""
    a = loop.Agent.__new__(loop.Agent)
    p = current or page()
    plan = list(goals)
    a.state = loop.RunState(
        browser=Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p), act=Mock(return_value={})),
        page=p,
        decision=decision(),
        goal=plan[0],
        plan=plan,
        status="predicted",
        started_at=time.perf_counter(),
    )
    for name, value in agent_settings.items():
        setattr(a, name, value)
    if a.trace:
        a.state.trace_path = str(a.trace.path)
    return a


def scripted_choices(monkeypatch, *decisions):
    """Replace the TypeSafe call with a fixed sequence of decisions; returns the recorded choose() arguments."""
    queue, calls = list(decisions), []

    def fake_choose(state, goal, history, completed_goals=()):
        calls.append({"goal": goal, "completed_goals": list(completed_goals), "history": list(history)})
        return queue.pop(0)

    monkeypatch.setattr(loop, "choose", fake_choose)
    return calls


def act_now(agent):
    return agent.command("act", {"fingerprint": agent.state["page"]["fingerprint"]})


@pytest.fixture
def runner():
    return make_runner()


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(loop.UNCHANGED_WAIT_LIMIT - 1):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == loop.UNCHANGED_WAIT_LIMIT - 1 and runner.state["status"] == "ready"


def test_waiting_on_a_page_that_never_changes_stops(runner):
    for _ in range(loop.UNCHANGED_WAIT_LIMIT):
        runner.state["decision"] = decision("wait")
        act_now(runner)
    assert runner.state["status"] == "blocked"
    assert runner.state["reason"] == f"page did not change during {loop.UNCHANGED_WAIT_LIMIT} waits in a row"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(model.NoFieldText, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def test_key_controls_become_operations_and_execute(runner, monkeypatch):
    p = page()
    enter = {"id": "press_enter", "kind": "key", "key": "Enter", "label": "Press Enter in the focused field"}
    p["actions"].append(enter)
    p["actions"].append({"id": "press_escape", "kind": "key", "key": "Escape", "label": "Press Escape"})
    _, _, controls = model.action_space(p["actions"])
    assert controls["PRESS_ENTER"]["id"] == "press_enter" and "PRESS_ESCAPE" in controls

    def post(_url, _key, body):
        criteria = body["questions"]["operation"]["criteria"]
        assert criteria["PRESS_ENTER"] == "Press Enter in the focused field"
        return {"answers": {"operation": choice(criteria, "PRESS_ENTER")}}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    chosen = model.choose(p, "Search for books", [])
    assert chosen["choice"] == "press_enter" and chosen["operation"] == "PRESS_ENTER" and chosen["target"] is None

    runner.state.update(page=p, decision={**decision("press_enter", "PRESS_ENTER", None), "probabilities": {
        "press_enter": 1.0}})
    act_now(runner)
    executed = runner.state["browser"].act.call_args.args[0]
    assert executed["kind"] == "key" and executed["key"] == "Enter"
    assert runner.state["history"][-1]["operation"] == "PRESS_ENTER"


def test_field_context_forwards_native_format():
    action = {"id": "e9", "kind": "fill", "label": "Depart", "role": "textbox", "value": "",
              "native_value": True, "format": "YYYY-MM-DD"}
    context = model.field_context("Leave on May 3 2027", action, page(), [])
    assert context["field"]["format"] == "YYYY-MM-DD" and context["field"]["native_value"] is True
    assert "format" not in model.field_context("x", page()["actions"][0], page(), [])["field"]
    assert "format" in questions.TEXT_VALUE and "exactly" in questions.TEXT_VALUE


def test_next_action_rules_are_generic_and_mention_keys():
    rules = questions.NEXT_ACTION
    assert "date picker" not in rules.lower() and "calendar" not in rules.lower()
    assert "PRESS_ENTER submits the focused field when no submit control is offered" in rules
    assert "PRESS_ESCAPE closes an open" in rules and "arrow keys move within an open list" in rules


def test_completed_goals_ride_in_the_same_single_request(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {"answers": {"operation": choice(body["questions"]["operation"]["criteria"], "WAIT")}}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    model.choose(page(), "Open the second result", [], ["Search for books"])
    model.choose(page(), "Search for books", [])
    assert len(calls) == 2
    assert all(q["instructions"]["completed_goals"] == ["Search for books"] for q in calls[0]["questions"].values())
    assert all("completed_goals" not in q["instructions"] for q in calls[1]["questions"].values())


def test_frame_elements_with_colliding_node_ids_get_separate_indices():
    actions = [
        {"id": "e1", "kind": "click", "label": "Top button", "role": "button", "value": "", "node": 5},
        {"id": "e2", "kind": "click", "label": "Frame button", "role": "button", "value": "", "node": 5, "frame": "F1"},
    ]
    elements, targets, _ = model.action_space(actions)
    assert [e["label"] for e in elements] == ["Top button", "Frame button"]
    assert targets["CLICK"]["2"]["id"] == "e2"


def test_tab_adopted_during_observation_is_noted(runner):
    runner.state["decision"] = decision("e3")
    late_page = {**page(), "new_tab": {"url": "https://example.test/popup", "title": "Popup"}}
    runner.state["browser"].observe.return_value = late_page
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "switched to new tab https://example.test/popup"
    assert "new_tab" not in runner.state["page"]


def test_text_helper_without_a_value_is_noted_and_the_model_chooses_again(runner, monkeypatch):
    no_value = model.NoFieldText("Text helper found no value for this field in the goal; nothing typed.")
    helper = Mock(side_effect=no_value)
    monkeypatch.setattr(loop, "field_text", helper)
    act_now(runner)
    runner.state["browser"].act.assert_not_called()
    assert runner.state["status"] == "ready"
    assert runner.state["history"][-1]["kind"] == "note"
    assert runner.state["history"][-1]["action"].startswith("no text for Search: Text helper found no value")
    for _ in range(2):
        runner.state.update(decision=decision(), status="predicted")
        act_now(runner)
    runner.state["browser"].act.assert_not_called()
    assert runner.state["status"] == "blocked"
    assert runner.state["reason"] == "text helper found no value"


def test_text_helper_transport_failure_still_stops_the_run(runner, monkeypatch):
    monkeypatch.setattr(loop, "field_text", Mock(side_effect=RuntimeError("Model provider returned HTTP 503")))
    with pytest.raises(RuntimeError, match="HTTP 503"):
        act_now(runner)
    assert runner.state["history"] == []


def test_missing_text_credential_is_not_mistaken_for_a_missing_value(runner, monkeypatch):
    monkeypatch.setattr(loop, "field_text", Mock(side_effect=ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY")))
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        act_now(runner)


def test_text_failures_reset_for_the_next_goal(monkeypatch):
    agent = make_runner(goals=("First", "Second"), done_threshold=None)
    agent.state["text_failures"] = 2
    agent.state["decision"] = decision("DONE", "DONE", None)
    act_now(agent)
    assert agent.state["goal"] == "Second" and agent.state["text_failures"] == 0


def secret_page():
    state = page()
    state["actions"][0] = {**state["actions"][0], "label": "Password", "secret": True, "value": ""}
    state["fingerprint"] = fingerprint(state)
    return state


def test_secret_field_text_reaches_only_the_browser(tmp_path, monkeypatch):
    from jev_ultrafast.trace import Trace

    secret = "hunter2-correct-horse"
    monkeypatch.setattr(loop, "field_text", Mock(return_value=(secret, {"model": "test", "latency_ms": 5})))
    agent = make_runner(current=secret_page(), trace=Trace(tmp_path))
    act_now(agent)
    assert agent.state["browser"].act.call_args.kwargs["text"] == secret
    assert agent.state["history"][-1]["text"] == loop.SECRET_MASK
    assert agent.state["text_calls"][-1]["value"] == loop.SECRET_MASK
    agent.close()
    written = "".join(path.read_text() for path in agent.trace.path.iterdir() if path.suffix in {".jsonl", ".json"})
    assert secret not in written
    events = {event["event"]: event for event in agent.trace.events()}
    assert events["text"]["value"] == events["execute"]["text"] == loop.SECRET_MASK


def test_masked_secret_repeats_are_still_detected(monkeypatch):
    monkeypatch.setattr(loop, "field_text", Mock(return_value=("s3cret", {"model": "test", "latency_ms": 5})))
    agent = make_runner(current=secret_page())
    for _ in range(3):
        agent.state.update(decision=decision(), status="predicted")
        act_now(agent)
    assert agent.state["browser"].act.call_count == 2
    assert agent.state["status"] == "blocked" and agent.state["reason"] == "repeated action"


def test_dialog_opened_by_an_action_is_noted(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].act.return_value = {"executed": "e3", "dialog": {"type": "confirm",
                                                                             "message": "Delete this item?"}}
    act_now(runner)
    assert runner.state["history"][-2]["action"] == "Go"
    assert runner.state["history"][-1]["action"] == "a confirm dialog opened: Delete this item?"


def dialog_page_with_back():
    state = page()
    state["actions"] = [
        {"id": "accept_dialog", "kind": "dialog", "accept": True, "label": "Accept the confirm dialog (OK): Delete?"},
        {"id": "dismiss_dialog", "kind": "dialog", "accept": False, "label": "Dismiss the confirm dialog (Cancel)"},
        {"id": "go_back", "kind": "back", "label": "Go back to the previous page (Search)", "entry": 3,
         "from_entry": 4},
    ]
    state["fingerprint"] = fingerprint(state)
    return state


@pytest.mark.parametrize(("choice_id", "pattern"), [("accept_dialog", "delete"), ("go_back", "go back")])
def test_dialog_and_back_controls_can_be_paused(choice_id, pattern):
    agent = make_runner(current=dialog_page_with_back(), pause_before=[pattern])
    agent.state["decision"] = {**decision(choice_id, choice_id.upper(), None), "probabilities": {choice_id: 1.0}}
    act_now(agent)
    assert agent.state["status"] == "paused" and agent.state["pending"]["choice"] == choice_id
    agent.state["browser"].act.assert_not_called()


def test_dialog_and_back_controls_are_model_operations():
    _, _, controls = model.action_space(dialog_page_with_back()["actions"])
    assert {"ACCEPT_DIALOG", "DISMISS_DIALOG", "GO_BACK"} <= set(controls)


def fake_browser(monkeypatch, dialog, probe_blocks=False):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.target, b.session, b.after_input = "TARGET", "SESSION", None

    def cdp(method, **params):
        if method == "Runtime.evaluate":
            if probe_blocks:
                raise TimeoutError("blocked")
            return {"result": {"value": 1}}
        if method == "Target.getTargetInfo":
            return {"targetInfo": {"url": "https://example.test/", "title": "Shop"}}
        raise AssertionError(f"unexpected {method}")

    monkeypatch.setattr(browser, "harness_meta", Mock(return_value={"dialog": dialog}))
    monkeypatch.setattr(browser, "cdp", cdp)
    return b


CONFIRM = {"type": "confirm", "message": "Delete?", "url": "https://example.test/", "frameId": "TARGET",
           "defaultPrompt": ""}


def test_our_open_dialog_is_observed_without_reading_the_blocked_page(monkeypatch):
    b = fake_browser(monkeypatch, CONFIRM)
    observed = b.observe(screenshot=False)
    assert observed["dialog"]["message"] == "Delete?" and observed["title"] == "Shop"
    assert [a["id"] for a in observed["actions"]] == ["accept_dialog", "dismiss_dialog"]
    assert b.fresh(observed)
    assert observed["fingerprint"] == fingerprint(observed)


@pytest.mark.parametrize(("frame", "probe_blocks", "ours"), [
    ("OTHER_TAB", False, False), ("CHILD_FRAME", True, True),
])
def test_dialog_ownership(monkeypatch, frame, probe_blocks, ours):
    b = fake_browser(monkeypatch, {**CONFIRM, "frameId": frame}, probe_blocks)
    assert (b.open_dialog() is not None) is ours


def test_normal_page_is_not_fresh_while_our_dialog_is_open(monkeypatch):
    b = fake_browser(monkeypatch, CONFIRM)
    assert not b.fresh(page())


def test_a_long_run_of_one_action_that_keeps_changing_the_page_is_stopped(runner):
    pages = (dict(page(), text=f"Display {count}", fingerprint=f"display-{count}") for count in range(100))
    runner.state["browser"].observe.side_effect = lambda **_: next(pages)
    for _ in range(loop.SAME_ACTION_RUN_LIMIT):
        runner.state["decision"] = decision("e3")
        act_now(runner)
    assert runner.state["status"] == "blocked"
    assert runner.state["reason"] == f"repeated 'Go' {loop.SAME_ACTION_RUN_LIMIT} times in a row"


def test_a_run_shorter_than_the_limit_continues(runner):
    pages = (dict(page(), text=f"Display {count}", fingerprint=f"display-{count}") for count in range(100))
    runner.state["browser"].observe.side_effect = lambda **_: next(pages)
    for _ in range(loop.SAME_ACTION_RUN_LIMIT - 1):
        runner.state["decision"] = decision("e3")
        act_now(runner)
    assert runner.state["status"] == "ready"
