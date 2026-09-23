"""Offline trace files: event order, state.json without screenshots, env-driven trace directories."""

import json
from copy import deepcopy
from unittest.mock import Mock

import pytest
from test_agent import decision, make_runner, page, scripted_choices

from jev_ultrafast import agent as loop
from jev_ultrafast.browser import StalePage, fingerprint
from jev_ultrafast.trace import Trace


def changed_page():
    later = deepcopy(page())
    later["url"] = "https://example.test/results"
    later["text"] = "Results for books"
    later["fingerprint"] = fingerprint(later)
    return later


def test_run_writes_events_in_loop_order_and_final_state(tmp_path, monkeypatch):
    runner = make_runner(trace=Trace(tmp_path), done_threshold=None)
    runner.state.update(status="ready", decision=None)
    results = changed_page()
    results["screenshot"] = "c2NyZWVu"
    runner.state["browser"].observe.return_value = results
    monkeypatch.setattr(loop, "field_text", Mock(return_value=("book", {"model": "helper", "latency_ms": 5})))
    scripted_choices(monkeypatch, decision("e1"), decision("DONE", "DONE", None))

    snapshots = list(runner.run())

    assert snapshots[-1]["status"] == "done" and snapshots[-1]["reason"] == "model chose DONE"
    events = [e["event"] for e in runner.trace.events()]
    assert events == ["decide", "text", "execute", "observe", "decide", "done"]
    assert all("elapsed_ms" in e for e in runner.trace.events())
    execute = next(e for e in runner.trace.events() if e["event"] == "execute")
    assert execute["label"] == "Search" and execute["text"] == "book"
    state = json.loads((runner.trace.path / "state.json").read_text())
    assert state["status"] == "done" and state["trace_path"] == str(runner.trace.path)
    assert "screenshot" not in state["page"]
    assert snapshots[-1]["trace_path"] == str(runner.trace.path)


def test_stale_prediction_is_traced(tmp_path):
    runner = make_runner(trace=Trace(tmp_path))
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert [e["event"] for e in runner.trace.events()] == ["stale", "observe"]


def test_failed_command_is_traced_as_error(tmp_path):
    runner = make_runner(trace=Trace(tmp_path))
    with pytest.raises(ValueError):
        runner.command("act", {"fingerprint": "wrong"})
    assert runner.trace.events()[-1]["event"] == "error"


def test_trace_rejects_unknown_events_and_saves_screenshots(tmp_path):
    trace = Trace(tmp_path)
    with pytest.raises(ValueError):
        trace.emit("guess")
    assert trace.save_screenshot("000010", "aGVsbG8=").read_bytes() == b"hello"
    assert trace.path.parent == tmp_path and trace.path.name.endswith("Z")


@pytest.mark.parametrize("configured", [True, False])
def test_trace_dir_defaults_to_settings(tmp_path, monkeypatch, configured):
    if configured:
        monkeypatch.setenv("JEV_TRACE_DIR", str(tmp_path))
    else:
        monkeypatch.delenv("JEV_TRACE_DIR", raising=False)
    monkeypatch.setattr(loop, "Browser", Mock(return_value=Mock(observe=Mock(return_value=page()))))
    agent = loop.Agent("https://example.test/", "Find a book", headless=False)
    snapshot = agent.snapshot()
    agent.close()
    if configured:
        assert snapshot["trace_path"] and snapshot["trace_path"].startswith(str(tmp_path))
        assert agent.trace.events()[0]["event"] == "observe"
        assert (agent.trace.path / "state.json").exists()
    else:
        assert snapshot["trace_path"] is None and agent.trace is None
