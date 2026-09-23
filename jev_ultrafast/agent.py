"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import contextlib
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from . import launch, settings
from .browser import Browser, StalePage
from .model import action_space, choose, extract_fields, field_context, field_text, verify_done
from .questions import MAX_STEPS
from .trace import Trace

STOPPED = {"done", "blocked"}
UNPAUSABLE_KINDS = {"wait", "scroll"}
NO_PROGRESS_LIMIT = 3
REPEAT_LIMIT = 2
VERIFIER_REJECTION_LIMIT = 2


@dataclass
class RunState:
    """Everything a snapshot reports. Mapping access keeps `agent.state["page"]`-style consumers working."""

    browser: Any = None
    goal: str = ""
    plan: list = field(default_factory=list)
    plan_index: int = 0
    page: dict | None = None
    decision: dict | None = None
    status: str = "ready"
    reason: str | None = None
    pending: dict | None = None
    verification: dict | None = None
    extracted: dict | None = None
    extraction_error: str | None = None
    history: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    text_calls: list = field(default_factory=list)
    elapsed_ms: int = 0
    started_at: float | None = None
    record: bool = False
    trace_path: str | None = None
    done_rejections: int = 0
    extras: dict = field(default_factory=dict)

    @classmethod
    def field_names(cls):
        return {f.name for f in fields(cls)} - {"extras"}

    def __getitem__(self, key):
        if key in self.field_names():
            return getattr(self, key)
        return self.extras[key]

    def __setitem__(self, key, value):
        if key in self.field_names():
            setattr(self, key, value)
        else:
            self.extras[key] = value

    def __contains__(self, key):
        return key in self.field_names() or key in self.extras

    def get(self, key, default=None):
        return self[key] if key in self else default

    def update(self, values=(), **changes):
        for key, value in {**dict(values), **changes}.items():
            self[key] = value

    def to_dict(self):
        own = {name: getattr(self, name) for name in self.field_names() - {"browser"}}
        return {**own, **self.extras}


def goal_list(goals):
    plan = [goals] if isinstance(goals, str) else list(goals or [])
    plan = [goal.strip() for goal in plan if isinstance(goal, str) and goal.strip()]
    if not plan:
        raise ValueError("Supply a task")
    return plan


def label_matches(pattern, label):
    if hasattr(pattern, "search"):
        return bool(pattern.search(label))
    return str(pattern).lower() in label.lower()


def is_executed(entry):
    return entry.get("kind") != "note"


class Agent:
    trace = None
    launched_chrome = None
    record_dir = None
    screenshots = False
    pending_text = None
    pause_before = ()
    extract = None
    done_threshold = None

    def __init__(
        self,
        url,
        goals,
        *,
        record_dir=None,
        screenshots=False,
        trace_dir=None,
        headless=None,
        pause_before=None,
        extract=None,
        done_threshold=0.5,
    ):
        plan = goal_list(goals)
        if extract is not None and (not isinstance(extract, dict) or not extract):
            raise ValueError("extract must map field names to descriptions")
        self.pause_before = [pause_before] if isinstance(pause_before, str) else list(pause_before or [])
        self.extract = extract
        self.done_threshold = done_threshold
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        trace_dir = settings.trace_dir() if trace_dir is None else trace_dir
        self.trace = Trace(trace_dir) if trace_dir else None
        self.state = RunState(
            goal=plan[0],
            plan=plan,
            record=bool(self.record_dir),
            trace_path=str(self.trace.path) if self.trace else None,
        )
        headless = settings.headless() if headless is None else headless
        self.launched_chrome = launch.launch_chrome(profile_dir=settings.profile_dir()) if headless else None
        try:
            self.browser = Browser(url)
            self.state.browser = self.browser
            self.state.page = self.observe_page()
        except Exception:
            self.close_browser()
            raise
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(self.state.page["screenshot"]))

    def emit(self, event, **details):
        if self.trace:
            self.trace.emit(event, **details)

    def elapsed(self):
        started = self.state["started_at"]
        return round((time.perf_counter() - started) * 1000) if started is not None else 0

    def snapshot(self):
        return {**self.state.to_dict(), "elements": action_space(self.state["page"]["actions"])[0]}

    def observe_page(self):
        page = self.state["browser"].observe(screenshot=self.screenshots)
        elapsed = self.elapsed()
        self.emit(
            "observe",
            url=page.get("url"),
            title=page.get("title"),
            fingerprint=page.get("fingerprint"),
            elements=len(page.get("actions", [])),
            run_elapsed_ms=elapsed,
        )
        if self.trace and self.screenshots and page.get("screenshot"):
            self.trace.save_screenshot(f"{elapsed:06d}", page["screenshot"])
        return page

    def note(self, text):
        self.state["history"].append({"kind": "note", "action": text, "elapsed_ms": self.elapsed()})
        self.emit("note", text=text)

    def finish(self, status, reason):
        state = self.state
        state["status"], state["reason"] = status, reason
        state["elapsed_ms"] = self.elapsed()
        self.emit(status, reason=reason)
        if self.trace:
            self.trace.write_state(self.snapshot())

    def command(self, name, body=None):
        handlers = {
            "tick": lambda: self.tick(),
            "predict": lambda: self.predict(),
            "act": lambda: self.act(body or {}),
            "approve": lambda: self.approve(),
            "reject": lambda: self.reject(),
        }
        if name not in handlers:
            raise ValueError("Unknown command")
        try:
            return handlers[name]()
        except StalePage as exc:
            self.emit("stale", message=str(exc))
            raise
        except Exception as exc:
            self.emit("error", message=str(exc))
            raise

    def tick(self):
        state = self.state
        try:
            self.predict()
            return self.act({"fingerprint": state["page"]["fingerprint"]})
        except StalePage as exc:
            self.emit("stale", message=str(exc))
            state["decision"] = None
            state["status"] = "ready"
            state["page"] = self.observe_page()
            state["elapsed_ms"] = self.elapsed()
            return self.snapshot()

    def predict(self):
        state = self.state
        if not state["browser"]:
            raise ValueError("Start a demo first")
        if state["status"] in STOPPED:
            raise ValueError("This run has stopped. Start a fresh demo.")
        if state["status"] == "paused":
            raise ValueError("Approve or reject the paused action first.")
        if state["started_at"] is None:
            state["started_at"] = time.perf_counter()
        if not state["browser"].fresh(state["page"]):
            state["page"] = self.observe_page()
        state["decision"] = None
        if len(state["decisions"]) >= MAX_STEPS * 2:
            raise ValueError("Reached the demo's model-call budget")
        completed = state["plan"][: state["plan_index"]]
        decision = choose(state["page"], state["goal"], state["history"], completed)
        state["decision"] = decision
        state["decisions"].append(
            {**decision, "fingerprint": state["page"]["fingerprint"], "elapsed_ms": self.elapsed()}
        )
        self.emit(
            "decide",
            choice=decision["choice"],
            operation=decision["operation"],
            target=decision["target"],
            confidence=decision["confidence"],
            probabilities=decision["probabilities"],
            latency_ms=decision["latency_ms"],
            usage=decision["usage"],
            fingerprint=state["page"]["fingerprint"],
        )
        state["status"] = "predicted"
        return self.snapshot()

    def checkpoint(self, decision, page):
        """The pending summary when the chosen action matches a pause_before pattern, else None."""
        if not self.pause_before or decision["choice"] in {"DONE", "BLOCKED"}:
            return None
        action = next((a for a in page["actions"] if a["id"] == decision["choice"]), None)
        if action is None or action["kind"] in UNPAUSABLE_KINDS:
            return None
        if not any(label_matches(pattern, action["label"]) for pattern in self.pause_before):
            return None
        return {
            "choice": decision["choice"],
            "label": action["label"],
            "operation": decision["operation"],
            "target": decision["target"],
        }

    def act(self, body):
        state = self.state
        decision, page = state["decision"], state["page"]
        if not decision or body.get("fingerprint") != page["fingerprint"]:
            raise ValueError("Observe and choose before acting")
        if state["status"] == "paused":
            return self.snapshot()
        if pending := self.checkpoint(decision, page):
            state["status"], state["pending"] = "paused", pending
            state["elapsed_ms"] = self.elapsed()
            self.emit("pause", **pending)
            if self.trace:
                self.trace.write_state(self.snapshot())
            return self.snapshot()
        # Consume once, before any mutation or model call. A retry cannot double-click.
        state["decision"] = None
        return self.execute(decision, page)

    def approve(self):
        state = self.state
        if state["status"] != "paused" or not state["decision"]:
            raise ValueError("No paused action to approve")
        decision, page, pending = state["decision"], state["page"], state["pending"]
        state["decision"], state["pending"], state["status"] = None, None, "predicted"
        self.emit("approve", **pending)
        executed_before = sum(map(is_executed, state["history"]))
        try:
            return self.execute(decision, page)
        except StalePage as exc:
            self.emit("stale", message=str(exc))
            if sum(map(is_executed, state["history"])) == executed_before:
                self.note("approved action went stale")
            state["status"] = "ready"
            state["page"] = self.observe_page()
            state["elapsed_ms"] = self.elapsed()
            return self.snapshot()

    def reject(self):
        state = self.state
        if state["status"] != "paused":
            raise ValueError("No paused action to reject")
        pending = state["pending"]
        state["decision"], state["pending"], state["status"] = None, None, "ready"
        self.emit("reject", **pending)
        self.note(f"user rejected: {pending['label']}")
        return self.snapshot()

    def restart(self, goals, *, pause_before=None, extract=None):
        """Start new goal(s) on the same tab with a clean run history; given options replace the current ones."""
        plan = goal_list(goals)
        if extract is not None:
            if not isinstance(extract, dict) or not extract:
                raise ValueError("extract must map field names to descriptions")
            self.extract = extract
        if pause_before is not None:
            self.pause_before = [pause_before] if isinstance(pause_before, str) else list(pause_before)
        self.state.update(
            goal=plan[0],
            plan=plan,
            plan_index=0,
            status="ready",
            reason=None,
            decision=None,
            pending=None,
            verification=None,
            extracted=None,
            extraction_error=None,
            history=[],
            decisions=[],
            text_calls=[],
            started_at=None,
            elapsed_ms=0,
            done_rejections=0,
        )
        self.pending_text = None
        self.emit("note", text="restart", plan=plan)
        return self.snapshot()

    def execute(self, decision, page):
        state = self.state
        selected = decision["choice"]
        if selected in {"DONE", "BLOCKED"}:
            if not state["browser"].fresh(page):
                state["status"] = "ready"
                raise StalePage("Page changed since the decision. Choose again.")
            if selected == "BLOCKED":
                self.finish("blocked", "model chose BLOCKED")
            else:
                self.complete_goal(page)
            return self.snapshot()
        action = next(a for a in page["actions"] if a["id"] == selected)
        executed = [h for h in state["history"] if is_executed(h)]
        if len(executed) >= MAX_STEPS:
            self.finish("blocked", "action budget")
            raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
        text, helper = None, None
        if action["kind"] == "fill":
            if not state["browser"].fresh(page):
                raise StalePage("Page changed before text generation. Choose again.")
            context = field_context(state["goal"], action, page, state["history"])
            if self.pending_text and self.pending_text[0] == context:
                _, text, helper = self.pending_text
            else:
                text, helper = field_text(context)
                self.pending_text = (context, text, helper)
                state["text_calls"].append({**helper, "field": action["label"], "value": text})
                self.emit("text", field=action["label"], value=text, model=helper["model"],
                          latency_ms=helper["latency_ms"])
        repeats = [
            h for h in executed
            if h.get("fingerprint") == page["fingerprint"] and h["choice"] == selected and h["text"] == text
        ]
        if action["kind"] != "wait" and len(repeats) >= REPEAT_LIMIT:
            self.pending_text = None
            self.finish("blocked", "repeated action")
            return self.snapshot()
        # Browser.act checks freshness immediately before input, including after text generation.
        outcome = state["browser"].act(action, page, text=text)
        self.pending_text = None
        state["elapsed_ms"] = self.elapsed()
        # Record execution before observing. A stale post-action observation must not erase the action.
        entry = {
            "step": len(executed) + 1,
            "action": action["label"],
            "kind": action["kind"],
            "choice": selected,
            "probability": decision["probabilities"][selected],
            "confidence": decision["confidence"],
            "latency_ms": decision["latency_ms"],
            "text": text,
            "text_helper": helper["model"] if helper else None,
            "text_latency_ms": helper["latency_ms"] if helper else 0,
            "operation": decision["operation"],
            "target": decision["target"],
            "page_changed": None,
            "fingerprint": page["fingerprint"],
            "url": page["url"],
            "usage": decision["usage"],
            "executed_ms": state["elapsed_ms"],
            "elapsed_ms": state["elapsed_ms"],
        }
        state["history"].append(entry)
        self.emit("execute", label=action["label"], kind=action["kind"], choice=selected, text=text,
                  executed_ms=entry["executed_ms"])
        new_tab = outcome.get("new_tab") if isinstance(outcome, dict) else None
        if new_tab:
            self.note(f"switched to new tab {new_tab.get('url', '')}")
        state["page"] = self.observe_page()
        if late_tab := state["page"].pop("new_tab", None):
            self.note(f"switched to new tab {late_tab.get('url', '')}")
        state["elapsed_ms"] = self.elapsed()
        entry.update(
            page_changed=state["page"]["fingerprint"] != page["fingerprint"],
            url=state["page"]["url"],
            elapsed_ms=state["elapsed_ms"],
        )
        if state["record"]:
            (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                base64.b64decode(state["page"]["screenshot"])
            )
        recent = [h for h in state["history"] if is_executed(h)][-NO_PROGRESS_LIMIT:]
        if len(recent) == NO_PROGRESS_LIMIT and all(h["page_changed"] is False and h["kind"] != "wait" for h in recent):
            self.finish("blocked", f"no page change after {NO_PROGRESS_LIMIT} actions")
        else:
            state["status"] = "ready"
        return self.snapshot()

    def complete_goal(self, page):
        """DONE on a fresh page: optional second opinion, then advance the plan or finish the run."""
        state = self.state
        reason = "model chose DONE"
        if self.done_threshold is not None:
            check = verify_done(page, state["goal"], state["history"])
            accepted = check["probability"] >= self.done_threshold
            state["verification"] = {
                "probability": check["probability"],
                "accepted": accepted,
                "latency_ms": check["latency_ms"],
            }
            self.emit("verify", **state["verification"], usage=check["usage"])
            if not accepted:
                if state["done_rejections"] < VERIFIER_REJECTION_LIMIT:
                    state["done_rejections"] += 1
                    self.note(f"DONE rejected by verifier (p={check['probability']:.2f})")
                    state["status"] = "ready"
                    state["elapsed_ms"] = self.elapsed()
                    return
                reason = "verifier rejected DONE twice; accepted model DONE"
        state["plan_index"] += 1
        if state["plan_index"] < len(state["plan"]):
            self.note(f"goal {state['plan_index']} complete")
            state["goal"] = state["plan"][state["plan_index"]]
            state["done_rejections"] = 0
            state["status"] = "ready"
            state["elapsed_ms"] = self.elapsed()
            return
        if self.extract:
            self.extract_on_done(page)
        self.finish("done", reason)

    def extract_on_done(self, page):
        state = self.state
        try:
            state["extracted"], _ = extract_fields("\n".join(state["plan"]), self.extract, page)
            state["extraction_error"] = None
        except Exception as exc:
            state["extracted"], state["extraction_error"] = None, str(exc)
            self.emit("error", message=str(exc), stage="extract")

    def run(self):
        while self.state["status"] not in STOPPED | {"paused"}:
            yield self.command("tick")

    def close_browser(self):
        try:
            if getattr(self, "browser", None):
                self.browser.close()
        finally:
            if self.launched_chrome:
                self.launched_chrome.close()
                self.launched_chrome = None

    def close(self):
        if self.trace:
            with contextlib.suppress(Exception):
                self.trace.write_state(self.snapshot())
        self.close_browser()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
