"""Loopback-only inspector for the Jev browser agent."""

import atexit
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .agent import Agent
from .endpoint import use_native_profile_endpoint
from .questions import MAX_STEPS
from .settings import headless, load_dotenv, text_model_name

ROOT = Path(__file__).parent
PORT = int(os.environ.get("TYPESAFE_DEMO_PORT", "8766"))
ORIGIN = f"http://127.0.0.1:{PORT}"
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
AGENT = None
SCENARIO = None
SCENARIOS = {"travel", "research", "flights"}
MAX_PAUSE_WORDS = 10
MAX_PAUSE_WORD_LENGTH = 100
IDLE_STATE = {
    "page": None,
    "status": "idle",
    "history": [],
    "decision": None,
    "pending": None,
    "verification": None,
    "extracted": None,
    "reason": None,
}


def load_environment():
    load_dotenv()


def response_state():
    state = AGENT.snapshot() if AGENT else IDLE_STATE
    return {**state, "scenario": SCENARIO, "text_model": text_model_name(), "max_steps": MAX_STEPS}


def close_browser():
    global AGENT, SCENARIO
    if AGENT:
        AGENT.close()
        AGENT = None
    SCENARIO = None


def pause_words(value):
    """Comma-separated text or a list of action-label words; empty means no checkpoints."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list) or not all(isinstance(word, str) for word in value):
        raise ValueError("Pause before takes comma-separated words")
    words = [word.strip() for word in value if word.strip()]
    if len(words) > MAX_PAUSE_WORDS:
        raise ValueError(f"Pause before takes at most {MAX_PAUSE_WORDS} words")
    if any(len(word) > MAX_PAUSE_WORD_LENGTH for word in words):
        raise ValueError(f"Each pause word must be at most {MAX_PAUSE_WORD_LENGTH} characters")
    return words or None


def command(name, body):
    global AGENT, SCENARIO
    if name == "reset":
        scenario = body.get("scenario", "flights")
        if scenario not in SCENARIOS:
            raise ValueError("Unknown demo scenario")
        goal = body.get("goal", "")
        goal = goal.strip() if isinstance(goal, str) else ""
        if not goal or len(goal) > 2000:
            raise ValueError("Enter 1–2,000 characters")
        pause_before = pause_words(body.get("pause_before"))
        close_browser()
        AGENT = Agent(
            "https://www.google.com/travel/flights?hl=en"
            if scenario == "flights"
            else f"{ORIGIN}/fixture.html?scenario={scenario}",
            goal,
            screenshots=True,
            record_dir=Path.cwd() / "artifacts" / "frames" if body.get("record") else None,
            pause_before=pause_before,
        )
        SCENARIO = scenario
    elif AGENT is None:
        raise ValueError("Start a demo first")
    elif name == "approve":
        AGENT.approve()
    elif name == "reject":
        AGENT.reject()
    else:
        AGENT.command(name, body)
    return response_state()


class Handler(BaseHTTPRequestHandler):
    def send(self, status, content, mime="application/json"):
        content = content if isinstance(content, bytes) else content.encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.headers.get("Host") != f"127.0.0.1:{PORT}":
            return self.send(403, "Forbidden", "text/plain")
        path = urlparse(self.path).path
        if path == "/api/state":
            with LOCK:
                return self.send(200, json.dumps(response_state()))
        if path == "/demo.mp4":
            video = ROOT.parent / "docs" / "demo.mp4"
            if video.exists():
                return self.send(200, video.read_bytes(), "video/mp4")
        files = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
            "/fixture.html": ("fixture.html", "text/html"),
        }
        if path not in files:
            return self.send(404, "Not found", "text/plain")
        name, mime = files[path]
        content = (ROOT / "static" / name).read_text().replace("__TOKEN__", TOKEN)
        self.send(200, content, mime + "; charset=utf-8")

    def do_POST(self):
        if (
            self.headers.get("Host") != f"127.0.0.1:{PORT}"
            or self.headers.get("X-Demo-Token") != TOKEN
            or self.headers.get("Origin") not in (None, ORIGIN)
        ):
            return self.send(403, json.dumps({"error": "Local demo requests only"}))
        if not LOCK.acquire(blocking=False):
            return self.send(409, json.dumps({"error": "A browser step is already running"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 8192:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            result = command(self.path.removeprefix("/api/"), body)
            self.send(200, json.dumps(result))
        except (ValueError, RuntimeError, TimeoutError) as error:
            self.send(400, json.dumps({"error": str(error)}))
        except Exception:
            self.send(500, json.dumps({"error": "Local demo failed; no automatic retry. Reset to recover."}))
        finally:
            LOCK.release()

    def log_message(self, *_args):
        pass


def main():
    load_environment()
    if not headless():
        use_native_profile_endpoint()
    atexit.register(close_browser)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Jev Ultrafast: {ORIGIN}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
