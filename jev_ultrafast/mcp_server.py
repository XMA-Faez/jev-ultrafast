"""Jev Ultrafast as an MCP server: Claude hands over a browser goal, Jev drives the tab."""

import base64
import contextlib
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field

os.environ.setdefault("BH_TELEMETRY", "0")

try:
    from mcp.server.mcpserver import Image, MCPServer
except ModuleNotFoundError as missing:
    raise ModuleNotFoundError("jev-mcp needs the mcp extra: pip install 'jev-ultrafast[mcp]'") from missing

from . import settings
from .agent import Agent
from .endpoint import use_native_profile_endpoint
from .model import action_space

BROWSER_SETUP_HINT = (
    "Start Chrome or Brave and turn on remote debugging at chrome://inspect/#remote-debugging "
    "(brave://inspect/#remote-debugging), then approve the connection prompt. "
    "Or pass headless=true to use a private headless Chrome."
)
DEFAULT_URL = "https://www.google.com"
PAGE_TEXT_LIMIT = 6000
DEFAULT_TIME_BUDGET_SECONDS = 120
STOPPED_STATUSES = {"done", "blocked", "paused"}
STEP_FIELDS = ("step", "kind", "action", "text", "page_changed", "url")
IDLE_CHECK_SECONDS = 30

server = MCPServer(
    "jev-browser",
    log_level="WARNING",
    instructions=(
        "A fast browser agent driving the user's real Chrome profile in its own background tab "
        "(or a private headless Chrome with headless=true). Give browser_task one natural-language goal, "
        "or a list of goals to run in order on the same tab; Jev picks every click, field and dropdown. "
        "A 'done' status is Jev's own claim, cross-checked by a verifier whose probability that the goal is "
        "complete comes back as verification.probability. Still verify the returned page text (or take a "
        "screenshot) before telling the user a goal was achieved. "
        "For a human checkpoint, pass pause_before with words from risky action labels (for example "
        "'buy', 'send', 'delete'): the task returns status 'paused' with the pending action. Ask the user, "
        "then call browser_approve to execute it or browser_reject to skip it and let Jev choose again. "
        "Pass extract={key: description} to get named values read from the final page in 'extracted'. "
        "Reuse session_id for follow-up work on the same tab, even on another site: pass url with session_id "
        "to load it there instead of opening a new tab. A session handles one call at a time. "
        "Call browser_close when you no longer need a tab. Tabs unused for a while close on their own, "
        "and opening a tab beyond the limit closes the least recently used idle one. "
        "Supported: clicks, typing, native selects, date and range inputs, Enter/Escape/arrow keys, scrolling, "
        "same-origin iframes, open shadow DOM, pop-up tabs. Not supported: cross-origin iframes, "
        "file uploads, canvas."
    ),
)


@dataclass
class Session:
    agent: Agent
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_used: float = field(default_factory=time.monotonic)


class SessionBusy(RuntimeError):
    pass


sessions: dict[str, Session] = {}
closed_session_reasons: dict[str, str] = {}
sessions_lock = threading.Lock()


@contextlib.contextmanager
def stdout_kept_for_protocol():
    """The stdio transport owns stdout; anything the browser layer prints goes to stderr."""
    with contextlib.redirect_stdout(sys.stderr):
        yield


@contextlib.contextmanager
def claimed_session(session_id: str):
    """Hold a session's own lock for one call; a second concurrent call fails fast instead of racing."""
    with sessions_lock:
        session = sessions.get(session_id)
        open_ids = sorted(sessions)
        closed_reason = closed_session_reasons.get(session_id)
    if session is None:
        if closed_reason:
            raise ValueError(f"Session {session_id} was closed ({closed_reason}). Start a new task without it.")
        raise ValueError(f"No open session {session_id!r}. Open sessions: {open_ids or 'none'}")
    if not session.lock.acquire(blocking=False):
        raise SessionBusy(f"Session {session_id} is busy with another call")
    try:
        with sessions_lock:
            still_open = sessions.get(session_id) is session
        if not still_open:
            raise ValueError(f"Session {session_id} was closed")
        yield session.agent
    finally:
        release_after_use(session)


def release_after_use(session: Session):
    session.last_used = time.monotonic()
    session.lock.release()


def close_claimed_session(session_id: str, session: Session, reason: str) -> bool:
    """Close a session whose lock the caller holds; False when it was already closed."""
    with sessions_lock:
        if sessions.get(session_id) is not session:
            return False
        sessions.pop(session_id)
        closed_session_reasons[session_id] = reason
    with stdout_kept_for_protocol(), contextlib.suppress(Exception):
        session.agent.close()
    return True


def close_if_unused(session_id: str, session: Session, reason: str, unused_for_seconds: float = 0) -> bool:
    """Close a session no call is using and none has used for `unused_for_seconds`."""
    if not session.lock.acquire(blocking=False):
        return False
    try:
        if time.monotonic() - session.last_used < unused_for_seconds:
            return False
        return close_claimed_session(session_id, session, reason)
    finally:
        session.lock.release()


def close_idle_sessions():
    idle_seconds = settings.mcp_idle_seconds()
    now = time.monotonic()
    with sessions_lock:
        idle = [(session_id, session) for session_id, session in sessions.items()
                if now - session.last_used >= idle_seconds]
    for session_id, session in idle:
        close_if_unused(session_id, session, f"unused for {idle_seconds / 60:g} minutes", idle_seconds)


def make_room_for_one_more_session():
    """Close least recently used idle sessions until one more fits; busy sessions are never closed."""
    limit = settings.mcp_max_sessions()
    with sessions_lock:
        least_recent_first = sorted(sessions.items(), key=lambda item: item[1].last_used)
    surplus = len(least_recent_first) - limit + 1
    for session_id, session in least_recent_first:
        if surplus <= 0:
            return
        if close_if_unused(session_id, session, f"closed to keep at most {limit} tabs open"):
            surplus -= 1


def close_idle_sessions_until(stopped: threading.Event):
    while not stopped.wait(IDLE_CHECK_SECONDS):
        close_idle_sessions()


def page_summary(page: dict) -> dict:
    return {
        "url": page.get("url"),
        "title": page.get("title"),
        "text": (page.get("text") or "")[:PAGE_TEXT_LIMIT],
    }


def compact_steps(history: list[dict]) -> list[dict]:
    return [{key: step.get(key) for key in STEP_FIELDS if step.get(key) is not None} for step in history]


def current_page(agent: Agent, snapshot: dict) -> dict:
    page = snapshot["page"]
    with contextlib.suppress(Exception):
        if not agent.browser.fresh(page):
            page = agent.browser.observe(screenshot=False)
    return page


def drive(agent: Agent, time_budget_seconds: int, snapshot: dict | None = None) -> tuple[dict, str, str | None]:
    """Run the loop until it stops on done/blocked/paused, the time budget runs out, or it raises."""
    deadline = time.monotonic() + time_budget_seconds
    snapshot = snapshot or agent.snapshot()
    if snapshot["status"] in STOPPED_STATUSES:
        return snapshot, snapshot["status"], None
    try:
        for snapshot in agent.run():
            if snapshot["status"] in STOPPED_STATUSES:
                break
            if time.monotonic() > deadline:
                return snapshot, "timeout", None
    except Exception as exc:  # the loop refuses rather than guesses; report its reason as-is
        with contextlib.suppress(Exception):
            snapshot = agent.snapshot()
        return snapshot, "error", str(exc)
    return snapshot, snapshot["status"], None


def task_result(session_id: str, agent: Agent, snapshot: dict, status: str, error: str | None) -> dict:
    result = {
        "session_id": session_id,
        "status": status,
        "reason": snapshot.get("reason"),
        "error": error,
        "goal": snapshot.get("goal"),
        "pending": snapshot.get("pending") if status == "paused" else None,
        "verification": snapshot.get("verification"),
        "extracted": snapshot.get("extracted"),
        "extraction_error": snapshot.get("extraction_error"),
        "elapsed_ms": snapshot.get("elapsed_ms"),
        "decisions": len(snapshot.get("decisions") or []),
        "steps": compact_steps(snapshot.get("history") or []),
        "page": page_summary(current_page(agent, snapshot)),
        "trace_path": snapshot.get("trace_path"),
    }
    if status == "paused":
        result["next"] = "Ask the user, then call browser_approve or browser_reject with this session_id."
    return result


def error_result(message: str, session_id: str | None = None) -> dict:
    return {"session_id": session_id, "status": "error", "error": message}


def open_session(goal, url, headless, pause_before, extract) -> tuple[str, Session]:
    close_idle_sessions()
    make_room_for_one_more_session()
    wants_headless = settings.headless() if headless is None else headless
    if not wants_headless:
        use_native_profile_endpoint()
    agent = Agent(url or DEFAULT_URL, goal, headless=headless, pause_before=pause_before, extract=extract)
    session = Session(agent)
    session.lock.acquire()
    with sessions_lock:
        session_id = uuid.uuid4().hex[:8]
        sessions[session_id] = session
    return session_id, session


@server.tool()
def browser_task(
    goal: str | list[str],
    url: str | None = None,
    session_id: str | None = None,
    time_budget_seconds: int = DEFAULT_TIME_BUDGET_SECONDS,
    pause_before: list[str] | None = None,
    extract: dict[str, str] | None = None,
    headless: bool | None = None,
) -> dict:
    """Run a browser goal (or a list of goals, in order) to completion with Jev choosing every action.

    Start a new tab with `url`, or continue an existing tab with `session_id`. Prefer continuing: with
    `session_id`, a given `url` loads in that same tab, `headless` is ignored, and `pause_before` and
    `extract`, when given, replace the session's previous values.
    `pause_before`: case-insensitive words; an action whose label contains one pauses the run for approval.
    `extract`: {key: description} of values to read from the final page into `extracted`.
    The tab stays open afterwards so you can continue, read or screenshot it; close it with browser_close
    when done. Unused tabs close on their own after a while, and only a few stay open at once.
    Returns status (done | blocked | paused | error | timeout), reason, pending (when paused), verification,
    extracted, the executed steps (notes included), the final page's visible text and trace_path.
    """
    with stdout_kept_for_protocol():
        if not session_id:
            try:
                session_id, session = open_session(goal, url, headless, pause_before, extract)
            except Exception as exc:
                return error_result(f"Could not open a browser tab: {exc}. {BROWSER_SETUP_HINT}")
            try:
                snapshot, status, error = drive(session.agent, time_budget_seconds)
                return task_result(session_id, session.agent, snapshot, status, error)
            finally:
                release_after_use(session)
        try:
            with claimed_session(session_id) as agent:
                try:
                    agent.restart(goal, url=url, pause_before=pause_before, extract=extract)
                except Exception as exc:
                    return error_result(f"Could not start the new goal: {exc}", session_id)
                snapshot, status, error = drive(agent, time_budget_seconds)
                return task_result(session_id, agent, snapshot, status, error)
        except (ValueError, SessionBusy) as exc:
            return error_result(str(exc), session_id)


def resume(session_id: str, time_budget_seconds: int, decide) -> dict:
    with stdout_kept_for_protocol():
        try:
            with claimed_session(session_id) as agent:
                if agent.snapshot()["status"] != "paused":
                    return error_result(f"Session {session_id} is not paused", session_id)
                try:
                    snapshot = decide(agent)
                except Exception as exc:
                    return task_result(session_id, agent, agent.snapshot(), "error", str(exc))
                snapshot, status, error = drive(agent, time_budget_seconds, snapshot)
                return task_result(session_id, agent, snapshot, status, error)
        except (ValueError, SessionBusy) as exc:
            return error_result(str(exc), session_id)


@server.tool()
def browser_approve(session_id: str, time_budget_seconds: int = DEFAULT_TIME_BUDGET_SECONDS) -> dict:
    """Execute the action a paused session is waiting on, then keep running the goal.

    Only call this after the user approved the pending action. Returns the same shape as browser_task.
    """
    return resume(session_id, time_budget_seconds, lambda agent: agent.approve())


@server.tool()
def browser_reject(session_id: str, time_budget_seconds: int = DEFAULT_TIME_BUDGET_SECONDS) -> dict:
    """Skip the action a paused session is waiting on; Jev notes the rejection and chooses again.

    Returns the same shape as browser_task.
    """
    return resume(session_id, time_budget_seconds, lambda agent: agent.reject())


@server.tool()
def browser_read(session_id: str) -> dict:
    """Re-read an open tab: its URL, title, visible text and the indexed interactive elements. No model call."""
    with stdout_kept_for_protocol(), claimed_session(session_id) as agent:
        page = agent.browser.observe(screenshot=False)
        elements = action_space(page["actions"])[0]
        return {"session_id": session_id, "page": page_summary(page), "elements": elements}


@server.tool()
def browser_screenshot(session_id: str) -> Image:
    """Screenshot an open tab, to verify an outcome visually."""
    with stdout_kept_for_protocol(), claimed_session(session_id) as agent:
        page = agent.browser.observe(screenshot=True)
        return Image(data=base64.b64decode(page["screenshot"]), format="jpeg")


def session_listing(session_id: str, session: Session) -> dict:
    if not session.lock.acquire(blocking=False):
        return {"session_id": session_id, "status": "running"}
    try:
        snapshot = session.agent.snapshot()
        return {
            "session_id": session_id,
            "status": snapshot["status"],
            "url": snapshot["page"].get("url"),
            "goal": snapshot.get("goal"),
        }
    finally:
        session.lock.release()


@server.tool()
def browser_sessions() -> list[dict]:
    """List the open tabs this server owns. A session busy with a call is listed as running."""
    with sessions_lock:
        listed = list(sessions.items())
    return [session_listing(session_id, session) for session_id, session in listed]


@server.tool()
def browser_close(session_id: str) -> str:
    """Close an open tab."""
    with stdout_kept_for_protocol(), claimed_session(session_id) as agent:
        with sessions_lock:
            sessions.pop(session_id, None)
            closed_session_reasons[session_id] = "closed with browser_close"
        agent.close()
        return f"Closed {session_id}"


def main():
    stopped = threading.Event()
    threading.Thread(target=close_idle_sessions_until, args=(stopped,), name="jev-idle-tabs", daemon=True).start()
    try:
        server.run("stdio")
    finally:
        stopped.set()
        with stdout_kept_for_protocol(), sessions_lock:
            for session in sessions.values():
                with contextlib.suppress(Exception):
                    session.agent.close()


if __name__ == "__main__":
    main()
