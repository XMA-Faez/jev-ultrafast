"""Per-run evidence: a JSONL event log, the final snapshot, and optional screenshots."""

import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path

EVENTS = {
    "observe",
    "decide",
    "execute",
    "stale",
    "text",
    "verify",
    "pause",
    "approve",
    "reject",
    "note",
    "done",
    "blocked",
    "error",
}


def without_screenshots(value):
    if isinstance(value, dict):
        return {k: without_screenshots(v) for k, v in value.items() if k != "screenshot"}
    if isinstance(value, list):
        return [without_screenshots(v) for v in value]
    return value


class Trace:
    def __init__(self, root_dir):
        self.path = Path(root_dir) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.path.mkdir(parents=True, exist_ok=True)
        self.events_path = self.path / "events.jsonl"
        self.started = time.perf_counter()

    def emit(self, event, **fields):
        if event not in EVENTS:
            raise ValueError(f"Unknown trace event {event!r}")
        record = {"event": event, "elapsed_ms": round((time.perf_counter() - self.started) * 1000), **fields}
        with self.events_path.open("a") as events:
            events.write(json.dumps(without_screenshots(record), default=str) + "\n")

    def events(self):
        if not self.events_path.exists():
            return []
        return [json.loads(line) for line in self.events_path.read_text().splitlines() if line]

    def write_state(self, snapshot):
        path = self.path / "state.json"
        path.write_text(json.dumps(without_screenshots(snapshot), default=str, indent=2))
        return path

    def save_screenshot(self, name, encoded):
        path = self.path / f"{name}.jpg"
        path.write_bytes(base64.b64decode(encoded))
        return path
