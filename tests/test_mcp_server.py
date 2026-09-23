"""Offline tests for the MCP tools and the native DevTools endpoint lookup. No browser, no model calls."""

import ast
import base64
import importlib.util
import socket
import sys
import threading
from pathlib import Path

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import endpoint, mcp_server
from jev_ultrafast.browser import fingerprint

STOPPED = {"done", "blocked", "paused"}
PAUSED_ACTION = {"choice": "e7", "label": "Buy now", "operation": "CLICK", "target": "7"}


class FakeBrowser:
    def __init__(self):
        self.page = {"url": "https://shop.test/", "title": "Shop", "text": "Cart", "actions": [], "fingerprint": "f1"}

    def fresh(self, _page):
        return True

    def observe(self, screenshot=False):
        return {**self.page, "screenshot": base64.b64encode(b"jpeg").decode() if screenshot else None}


class FakeAgent:
    """Follows a scripted list of statuses; run() stops on done, blocked or paused like the real loop."""

    created = []
    script = ["done"]
    run_gate = None

    def __init__(self, url, goals, *, headless=None, pause_before=None, extract=None, **options):
        self.url, self.goals, self.headless = url, goals, headless
        self.pause_before, self.extract, self.options = pause_before, extract, options
        self.browser = FakeBrowser()
        self.upcoming = list(FakeAgent.script)
        self.status, self.reason, self.pending = "ready", None, None
        self.history, self.restarted, self.closed = [], [], False
        FakeAgent.created.append(self)

    def advance(self):
        self.status = self.upcoming.pop(0) if self.upcoming else "blocked"
        if self.status == "paused":
            self.pending = dict(PAUSED_ACTION)
        elif self.status in {"ready", "predicted"}:
            self.history.append({"step": len(self.history) + 1, "kind": "click", "action": "Open cart"})

    def snapshot(self):
        done = self.status == "done"
        return {
            "status": self.status,
            "reason": "goal met" if done else self.reason,
            "pending": self.pending,
            "verification": {"probability": 0.87, "accepted": True, "latency_ms": 90} if done else None,
            "extracted": {key: "42" for key in self.extract} if done and self.extract else None,
            "goal": self.goals,
            "history": list(self.history),
            "decisions": [{}] * len(self.history),
            "elapsed_ms": 1234,
            "page": self.browser.page,
            "trace_path": "/traces/run/events.jsonl",
        }

    def run(self):
        while self.status not in STOPPED:
            if FakeAgent.run_gate:
                FakeAgent.run_gate()
            self.advance()
            yield self.snapshot()

    def approve(self):
        self.history.append({"step": len(self.history) + 1, "kind": "click", "action": self.pending["label"]})
        self.pending, self.status = None, "ready"
        return self.snapshot()

    def reject(self):
        self.history.append({"kind": "note", "action": f"user rejected: {self.pending['label']}"})
        self.pending, self.status = None, "ready"
        return self.snapshot()

    def restart(self, goals, *, pause_before=None, extract=None):
        self.restarted.append(goals)
        self.restart_options = {"pause_before": pause_before, "extract": extract}
        self.goals, self.status, self.pending = goals, "ready", None
        self.upcoming = list(FakeAgent.script)

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fake_agent(monkeypatch):
    FakeAgent.created, FakeAgent.script, FakeAgent.run_gate = [], ["done"], None
    endpoint_calls = []
    monkeypatch.setattr(mcp_server, "Agent", FakeAgent)
    monkeypatch.setattr(mcp_server, "use_native_profile_endpoint", lambda: endpoint_calls.append(True))
    monkeypatch.setattr(mcp_server, "sessions", {})
    monkeypatch.delenv("JEV_HEADLESS", raising=False)
    return endpoint_calls


def test_browser_task_opens_session_and_passes_options(fake_agent):
    result = mcp_server.browser_task(
        ["Open the cart", "Read the total"], url="https://shop.test/", pause_before=["buy"], extract={"total": "Cart"}
    )

    agent = FakeAgent.created[0]
    assert (agent.url, agent.goals, agent.pause_before, agent.extract) == (
        "https://shop.test/",
        ["Open the cart", "Read the total"],
        ["buy"],
        {"total": "Cart"},
    )
    assert result["status"] == "done" and result["reason"] == "goal met"
    assert result["verification"]["probability"] == 0.87
    assert result["extracted"] == {"total": "42"}
    assert result["trace_path"] == "/traces/run/events.jsonl"
    assert result["page"] == {"url": "https://shop.test/", "title": "Shop", "text": "Cart"}
    assert result["session_id"] in mcp_server.sessions
    assert fake_agent == [True]


def test_headless_session_skips_native_endpoint(fake_agent):
    mcp_server.browser_task("Open the cart", headless=True)

    assert FakeAgent.created[0].headless is True
    assert fake_agent == []


def test_paused_result_then_approve_resumes_to_done():
    FakeAgent.script = ["ready", "paused", "done"]

    paused = mcp_server.browser_task("Buy the item", pause_before=["buy"])

    assert paused["status"] == "paused"
    assert paused["pending"] == PAUSED_ACTION
    assert "browser_approve" in paused["next"]
    finished = mcp_server.browser_approve(paused["session_id"])
    assert finished["status"] == "done"
    assert [step["action"] for step in finished["steps"]] == ["Open cart", "Buy now"]
    assert finished["pending"] is None


def test_reject_records_note_in_steps_and_resumes():
    FakeAgent.script = ["paused", "done"]

    paused = mcp_server.browser_task("Buy the item", pause_before=["buy"])
    finished = mcp_server.browser_reject(paused["session_id"])

    assert finished["status"] == "done"
    assert finished["steps"] == [{"kind": "note", "action": "user rejected: Buy now"}]


def test_approve_requires_a_paused_session():
    opened = mcp_server.browser_task("Open the cart")

    result = mcp_server.browser_approve(opened["session_id"])

    assert result["status"] == "error" and "not paused" in result["error"]


def test_existing_session_restarts_with_new_goal():
    opened = mcp_server.browser_task("Open the cart")

    again = mcp_server.browser_task("Read the total", session_id=opened["session_id"])

    assert FakeAgent.created[0].restarted == ["Read the total"]
    assert len(FakeAgent.created) == 1
    assert again["status"] == "done" and again["goal"] == "Read the total"


def test_unknown_session_returns_error():
    result = mcp_server.browser_task("Open the cart", session_id="missing")

    assert result["status"] == "error" and "No open session" in result["error"]


def test_busy_session_rejects_concurrent_calls_immediately():
    FakeAgent.script = ["ready", "done"]
    entered, release = threading.Event(), threading.Event()

    def hold_first_step():
        entered.set()
        assert release.wait(5)

    FakeAgent.run_gate = hold_first_step
    results = []
    worker = threading.Thread(target=lambda: results.append(mcp_server.browser_task("Open the cart")))
    worker.start()
    assert entered.wait(5)
    (session_id,) = mcp_server.sessions

    busy = f"Session {session_id} is busy with another call"
    assert mcp_server.browser_task("Other goal", session_id=session_id) == {
        "session_id": session_id,
        "status": "error",
        "error": busy,
    }
    assert mcp_server.browser_approve(session_id)["error"] == busy
    assert mcp_server.browser_sessions() == [{"session_id": session_id, "status": "running"}]
    with pytest.raises(mcp_server.SessionBusy):
        mcp_server.browser_close(session_id)

    release.set()
    worker.join(5)
    assert results[0]["status"] == "done"
    assert mcp_server.browser_sessions()[0]["status"] == "done"


def test_time_budget_and_loop_errors_are_reported():
    FakeAgent.script = ["ready", "ready", "done"]
    assert mcp_server.browser_task("Open the cart", time_budget_seconds=-1)["status"] == "timeout"

    def failing_step():
        raise RuntimeError("Page changed since the decision")

    FakeAgent.run_gate = failing_step
    result = mcp_server.browser_task("Open the cart")
    assert (result["status"], result["error"]) == ("error", "Page changed since the decision")


def test_read_screenshot_and_close():
    session_id = mcp_server.browser_task("Open the cart")["session_id"]

    assert mcp_server.browser_read(session_id)["page"]["title"] == "Shop"
    assert mcp_server.browser_screenshot(session_id).data == b"jpeg"
    assert mcp_server.browser_close(session_id) == f"Closed {session_id}"
    assert FakeAgent.created[0].closed and not mcp_server.sessions


class LoopBrowser:
    """A page with a Buy button; clicking it shows the receipt."""

    def __init__(self, url):
        self.url, self.bought, self.clicked = url, False, []

    def page(self):
        page = {
            "url": self.url,
            "title": "Receipt" if self.bought else "Shop",
            "text": "Total 42" if self.bought else "Cart",
            "scroll": {"y": 0},
            "actions": []
            if self.bought
            else [{"id": "e1", "kind": "click", "label": "Buy now", "role": "button", "node": 1}],
        }
        page["fingerprint"] = fingerprint(page)
        return page

    def observe(self, screenshot=False):
        return self.page()

    def fresh(self, page):
        return page["fingerprint"] == self.page()["fingerprint"]

    def act(self, action, _page, text=None):
        self.clicked.append(action["label"])
        self.bought = True
        return {"executed": action["id"]}

    def close(self):
        pass


def loop_decision(choice, operation):
    return {
        "choice": choice,
        "operation": operation,
        "target": "1" if choice == "e1" else None,
        "confidence": 1.0,
        "probabilities": {choice: 1.0},
        "latency_ms": 5,
        "usage": {},
    }


def test_real_agent_pauses_rejects_approves_and_extracts(monkeypatch):
    monkeypatch.setattr(mcp_server, "Agent", loop.Agent)
    monkeypatch.setattr(loop, "Browser", LoopBrowser)
    monkeypatch.delenv("JEV_TRACE_DIR", raising=False)
    monkeypatch.setattr(
        loop,
        "choose",
        lambda page, *_: loop_decision("DONE", "DONE") if not page["actions"] else loop_decision("e1", "CLICK"),
    )
    monkeypatch.setattr(loop, "verify_done", lambda *_: {"probability": 0.91, "latency_ms": 7, "usage": {}})
    monkeypatch.setattr(loop, "extract_fields", lambda goal, schema, page: ({"total": page["text"][-2:]}, {}))

    paused = mcp_server.browser_task(
        "Buy the item", url="https://shop.test/", pause_before=["buy"], extract={"total": "Order total"}
    )
    assert paused["status"] == "paused" and paused["pending"]["label"] == "Buy now"
    session_id = paused["session_id"]

    skipped = mcp_server.browser_reject(session_id)
    assert skipped["status"] == "paused"
    assert {"kind": "note", "action": "user rejected: Buy now"} in skipped["steps"]

    finished = mcp_server.browser_approve(session_id)
    assert finished["status"] == "done"
    assert finished["verification"] == {"probability": 0.91, "accepted": True, "latency_ms": 7}
    assert finished["extracted"] == {"total": "42"}
    assert finished["page"]["title"] == "Receipt"
    assert [step["action"] for step in finished["steps"] if step["kind"] != "note"] == ["Buy now"]


def listening_port():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    return listener


def closed_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def write_active_port(home: Path, relative: str, port: int, ws_path="/devtools/browser/abc"):
    profile = home / relative
    profile.mkdir(parents=True)
    (profile / "DevToolsActivePort").write_text(f"{port}\n{ws_path}\n")
    return profile


@pytest.fixture
def clean_endpoint_env(monkeypatch):
    monkeypatch.setenv("BU_CDP_WS", "")
    monkeypatch.setenv("BU_CDP_URL", "")


def test_native_brave_endpoint_is_found(tmp_path, clean_endpoint_env):
    with listening_port() as listener:
        port = listener.getsockname()[1]
        write_active_port(tmp_path, ".config/BraveSoftware/Brave-Browser", port)

        assert endpoint.devtools_endpoint(tmp_path, "Linux") == f"ws://127.0.0.1:{port}/devtools/browser/abc"
        assert endpoint.devtools_endpoint(tmp_path, "Darwin") is None


def test_stale_native_port_file_is_ignored(tmp_path, clean_endpoint_env):
    write_active_port(tmp_path, ".config/BraveSoftware/Brave-Browser", closed_port())

    assert endpoint.devtools_endpoint(tmp_path, "Linux") is None


def test_live_scanned_profile_leaves_discovery_to_harness(tmp_path, clean_endpoint_env):
    with listening_port() as listener:
        port = listener.getsockname()[1]
        write_active_port(tmp_path, ".config/BraveSoftware/Brave-Browser", port)
        write_active_port(tmp_path, ".config/google-chrome", port)

        assert endpoint.devtools_endpoint(tmp_path, "Linux") is None


def test_flatpak_alias_of_native_profile_is_left_to_harness(tmp_path, clean_endpoint_env):
    native = write_active_port(tmp_path, ".config/BraveSoftware/Brave-Browser", closed_port())
    alias = tmp_path / ".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(native)
    with listening_port() as listener:
        (native / "DevToolsActivePort").write_text(f"{listener.getsockname()[1]}\n/devtools/browser/abc\n")

        assert endpoint.devtools_endpoint(tmp_path, "Linux") is None


def test_configured_endpoint_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("BU_CDP_URL", "http://127.0.0.1:9333")
    with listening_port() as listener:
        write_active_port(tmp_path, ".config/BraveSoftware/Brave-Browser", listener.getsockname()[1])

        assert endpoint.devtools_endpoint(tmp_path, "Linux") is None


def test_use_native_profile_endpoint_sets_harness_env_before_import(tmp_path, monkeypatch, clean_endpoint_env):
    monkeypatch.delitem(sys.modules, "browser_harness", raising=False)
    with listening_port() as listener:
        port = listener.getsockname()[1]
        write_active_port(tmp_path, ".config/google-chrome-beta", port)

        assert endpoint.use_native_profile_endpoint(tmp_path, "Linux") == f"ws://127.0.0.1:{port}/devtools/browser/abc"
        assert endpoint.os.environ["BU_CDP_WS"] == f"ws://127.0.0.1:{port}/devtools/browser/abc"

        monkeypatch.setenv("BU_CDP_WS", "")
        monkeypatch.setitem(sys.modules, "browser_harness", object())
        assert endpoint.use_native_profile_endpoint(tmp_path, "Linux") is None
        assert endpoint.os.environ["BU_CDP_WS"] == ""


def test_scan_list_mirrors_installed_harness():
    spec = importlib.util.find_spec("browser_harness")
    daemon_source = Path(spec.submodule_search_locations[0], "daemon.py").read_text()
    assignment = next(
        node
        for node in ast.parse(daemon_source).body
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "_LINUX_PROFILES"
    )
    harness_profiles = ast.literal_eval(assignment.value)

    assert endpoint.HARNESS_SCANNED_LINUX_PROFILES == harness_profiles
    assert not set(endpoint.UNSCANNED_NATIVE_LINUX_PROFILES) & set(harness_profiles)
