"""Offline trust features: second-opinion DONE, human checkpoints, structured extraction. No paid APIs."""

import json
import math
import re
from unittest.mock import Mock

import pytest
from test_agent import act_now, decision, make_runner, page

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage

DONE = decision("DONE", "DONE", None)


def done_runner(**settings):
    runner = make_runner(**{"done_threshold": 0.5, **settings})
    runner.state["decision"] = dict(DONE)
    return runner


def verifier(*probabilities):
    return Mock(side_effect=[{"probability": p, "latency_ms": 7, "usage": {}} for p in probabilities])


def test_verify_done_posts_one_noul_question(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {"answers": {"done": {"type": "noul", "noul": 0.82}}, "usage": {"total_tokens": 9}}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    history = [{"kind": "note", "action": "DONE rejected by verifier (p=0.10)"}]
    result = model.verify_done(page(), "Find a book", history)
    assert result["probability"] == 0.82 and result["usage"] == {"total_tokens": 9}
    assert len(calls) == 1 and list(calls[0]["questions"]) == ["done"]
    question = calls[0]["questions"]["done"]
    assert question["type"] == "noul" and set(question["criteria"]) == {"true", "false"}
    assert question["instructions"]["goal"] == "Find a book"
    assert "EVERY requirement" in question["instructions"]["rules"]
    assert calls[0]["state"]["recent_actions"][0]["action"].startswith("DONE rejected")
    assert calls[0]["state"]["elements"] and calls[0]["state"]["page"]["url"] == "https://example.test/"


@pytest.mark.parametrize("answer", [{"noul": 1.5}, {"noul": -0.1}, {"noul": math.nan}, {"noul": "0.5"}, {}, None])
def test_verify_done_rejects_invalid_noul(monkeypatch, answer):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"answers": {"done": answer}}))
    with pytest.raises(ValueError, match="Invalid TypeSafe response; no action executed."):
        model.verify_done(page(), "Find a book", [])


def test_verifier_accepts_done_at_threshold(monkeypatch):
    runner = done_runner()
    monkeypatch.setattr(loop, "verify_done", verifier(0.5))
    snapshot = act_now(runner)
    assert snapshot["status"] == "done" and snapshot["reason"] == "model chose DONE"
    assert snapshot["verification"] == {"probability": 0.5, "accepted": True, "latency_ms": 7}


def test_verifier_rejection_returns_to_ready_with_a_note(monkeypatch):
    runner = done_runner()
    monkeypatch.setattr(loop, "verify_done", verifier(0.31))
    snapshot = act_now(runner)
    assert snapshot["status"] == "ready" and snapshot["reason"] is None
    assert snapshot["history"][-1] == {"kind": "note", "action": "DONE rejected by verifier (p=0.31)",
                                       "elapsed_ms": snapshot["history"][-1]["elapsed_ms"]}
    assert snapshot["verification"]["accepted"] is False
    runner.state["browser"].act.assert_not_called()


def test_third_rejected_done_is_accepted_and_marked(monkeypatch):
    runner = done_runner()
    monkeypatch.setattr(loop, "verify_done", verifier(0.2, 0.1, 0.05))
    for _ in range(2):
        runner.state["decision"] = dict(DONE)
        assert act_now(runner)["status"] == "ready"
    runner.state["decision"] = dict(DONE)
    snapshot = act_now(runner)
    assert snapshot["status"] == "done"
    assert snapshot["reason"] == "verifier rejected DONE twice; accepted model DONE"
    assert snapshot["verification"] == {"probability": 0.05, "accepted": False, "latency_ms": 7}
    assert loop.verify_done.call_count == 3


def test_disabled_verifier_makes_no_call(monkeypatch):
    runner = done_runner(done_threshold=None)
    monkeypatch.setattr(loop, "verify_done", Mock())
    snapshot = act_now(runner)
    assert snapshot["status"] == "done" and snapshot["verification"] is None
    loop.verify_done.assert_not_called()


def test_stale_done_is_not_verified(monkeypatch):
    runner = done_runner()
    runner.state["browser"].fresh.return_value = False
    monkeypatch.setattr(loop, "verify_done", Mock())
    with pytest.raises(StalePage):
        act_now(runner)
    loop.verify_done.assert_not_called()


def paused_runner(patterns=("go",)):
    runner = make_runner(pause_before=list(patterns))
    runner.state["decision"] = decision("e3", "CLICK", "2")
    snapshot = act_now(runner)
    assert snapshot["status"] == "paused"
    return runner


def test_matching_action_pauses_without_executing():
    runner = paused_runner()
    snapshot = runner.snapshot()
    assert snapshot["pending"] == {"choice": "e3", "label": "Go", "operation": "CLICK", "target": "2"}
    assert snapshot["decision"]["choice"] == "e3"
    runner.state["browser"].act.assert_not_called()
    assert list(runner.run()) == []
    act_now(runner)
    runner.state["browser"].act.assert_not_called()
    with pytest.raises(ValueError, match="paused"):
        runner.command("predict")


def test_regex_patterns_match_labels():
    paused_runner([re.compile(r"^G.$")])


@pytest.mark.parametrize("chosen", [decision("wait", "WAIT", None), DONE, decision("e3", "CLICK", "2")])
def test_waits_done_and_non_matching_actions_never_pause(chosen, monkeypatch):
    runner = make_runner(pause_before=["wait", "done", "checkout"], done_threshold=None)
    runner.state["decision"] = dict(chosen)
    assert act_now(runner)["status"] in {"ready", "done"}
    assert runner.state["pending"] is None


def test_approve_executes_the_paused_decision_once():
    runner = paused_runner()
    snapshot = runner.approve()
    runner.state["browser"].act.assert_called_once()
    assert runner.state["browser"].act.call_args.args[0]["id"] == "e3"
    assert snapshot["status"] == "ready" and snapshot["pending"] is None and snapshot["decision"] is None
    assert snapshot["history"][-1]["action"] == "Go"
    with pytest.raises(ValueError, match="No paused action"):
        runner.approve()
    runner.state["browser"].act.assert_called_once()


def test_approve_routes_through_command():
    runner = paused_runner()
    runner.command("approve")
    runner.state["browser"].act.assert_called_once()


def test_stale_approval_does_not_execute():
    runner = paused_runner()
    runner.state["browser"].act.side_effect = StalePage("Changed before input")
    snapshot = runner.approve()
    assert snapshot["status"] == "ready" and snapshot["pending"] is None
    assert snapshot["history"][-1]["action"] == "approved action went stale"
    assert not [h for h in snapshot["history"] if h["kind"] != "note"]
    with pytest.raises(ValueError):
        runner.approve()


def test_reject_discards_the_decision_with_a_note():
    runner = paused_runner()
    snapshot = runner.reject()
    runner.state["browser"].act.assert_not_called()
    assert snapshot["status"] == "ready" and snapshot["decision"] is None and snapshot["pending"] is None
    assert snapshot["history"][-1]["kind"] == "note" and snapshot["history"][-1]["action"] == "user rejected: Go"
    with pytest.raises(ValueError):
        runner.approve()


def helper_reply(content):
    return Mock(return_value={"choices": [{"message": {"content": content}}], "usage": {}})


SCHEMA = {"title": "Book title", "price": "Price in dollars", "in_stock": "Available now"}


def test_extract_fields_accepts_exact_scalar_object(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = helper_reply('{"title": "Dune", "price": 9.5, "in_stock": null}')
    monkeypatch.setattr(model, "post_json", post)
    values, meta = model.extract_fields("Find Dune", SCHEMA, page())
    assert values == {"title": "Dune", "price": 9.5, "in_stock": None}
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["fields"] == SCHEMA and sent["goal"] == "Find Dune"
    assert "exactly the keys" in post.call_args.args[2]["messages"][0]["content"]


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1, 2]",
        '{"title": "Dune", "price": 9.5}',
        '{"title": "Dune", "price": 9.5, "in_stock": true, "extra": 1}',
        '{"title": {"nested": 1}, "price": 9.5, "in_stock": true}',
        '{"title": ["a"], "price": 9.5, "in_stock": true}',
        json.dumps({"title": "x" * 5000, "price": 1, "in_stock": True}),
        None,
    ],
)
def test_extract_fields_rejects_invalid_data(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", helper_reply(content))
    with pytest.raises(ValueError, match="Extraction returned invalid data."):
        model.extract_fields("Find Dune", SCHEMA, page())


def test_done_stores_extracted_values(monkeypatch):
    runner = done_runner(extract={"title": "Book title"})
    monkeypatch.setattr(loop, "verify_done", verifier(0.9))
    monkeypatch.setattr(loop, "extract_fields", Mock(return_value=({"title": "Dune"}, {})))
    snapshot = act_now(runner)
    assert snapshot["status"] == "done" and snapshot["extracted"] == {"title": "Dune"}
    assert snapshot["extraction_error"] is None


def test_failed_extraction_keeps_the_run_done(monkeypatch, tmp_path):
    from jev_ultrafast.trace import Trace

    runner = done_runner(extract={"title": "Book title"}, trace=Trace(tmp_path))
    monkeypatch.setattr(loop, "verify_done", verifier(0.9))
    monkeypatch.setattr(loop, "extract_fields", Mock(side_effect=ValueError("Extraction returned invalid data.")))
    snapshot = act_now(runner)
    assert snapshot["status"] == "done" and snapshot["reason"] == "model chose DONE"
    assert snapshot["extracted"] is None and snapshot["extraction_error"] == "Extraction returned invalid data."
    assert [e["event"] for e in runner.trace.events()] == ["verify", "error", "done"]


def test_rejected_done_does_not_extract(monkeypatch):
    runner = done_runner(extract={"title": "Book title"})
    monkeypatch.setattr(loop, "verify_done", verifier(0.1))
    monkeypatch.setattr(loop, "extract_fields", Mock())
    act_now(runner)
    loop.extract_fields.assert_not_called()
