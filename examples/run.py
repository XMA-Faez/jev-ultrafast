"""uv run --env-file .env python examples/run.py --url URL --goal 'A narrow goal' [--headless] [--trace-dir DIR]"""

import argparse

from jev_ultrafast import Agent

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True)
parser.add_argument("--goal", action="append", required=True, help="Repeat for an ordered list of goals.")
parser.add_argument("--headless", action="store_true", help="Launch a private headless Chrome instead of attaching.")
parser.add_argument("--trace-dir", help="Write a JSONL trace and final state under this folder.")
args = parser.parse_args()

with Agent(args.url, args.goal, headless=True if args.headless else None, trace_dir=args.trace_dir) as agent:
    state = agent.snapshot()
    for state in agent.run():
        print(f"{state['elapsed_ms']:>5} ms  {len(state['history'])} actions  {state['status']}")
    print(state["page"]["url"])
    print(f"status: {state['status']}  reason: {state.get('reason') or '-'}")
    if state.get("trace_path"):
        print(f"trace: {state['trace_path']}")
