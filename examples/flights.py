"""Live Google Flights search. Calls TypeSafe; never selects or books a flight."""

import argparse
import json
from pathlib import Path

from bench.tasks.flights import RECORDED_DEPARTURE, URL, goal_for, verifier_for
from jev_ultrafast import Agent

GOALS = goal_for(RECORDED_DEPARTURE)
verify = verifier_for(RECORDED_DEPARTURE)
__all__ = ["GOALS", "URL", "verify"]


def trace_folder(state):
    if not state.get("trace_path"):
        return None
    path = Path(state["trace_path"])
    return path if path.is_dir() else path.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="artifacts/flights", help="Trace folder; each run adds a timestamped run.")
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()
    agent = Agent(URL, GOALS, trace_dir=args.output)
    try:
        for state in agent.run():
            last = state["history"][-1] if state["history"] else {}
            print(state["elapsed_ms"], state["status"], last.get("action", ""), flush=True)
    finally:
        state = agent.snapshot()
        outcome = verify(state["page"])
        folder = trace_folder(state)
        if folder:
            (folder / "outcome.json").write_text(json.dumps(outcome, indent=2, ensure_ascii=False))
            if args.keep_open:
                agent.trace.write_state(state)
                (folder / "session.json").write_text(
                    json.dumps({"target": agent.browser.target, "session": agent.browser.session})
                )
        if not args.keep_open:
            agent.close()
    print(json.dumps(outcome, indent=2, ensure_ascii=False))
    if folder:
        print(f"Trace: {folder}")
    if not outcome["passed"]:
        raise SystemExit("Final page did not satisfy the route/date checks")


if __name__ == "__main__":
    main()
