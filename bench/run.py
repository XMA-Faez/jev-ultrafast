"""python -m bench.run [--tasks a,b] [--repeat N] [--headless] [--out DIR] [--trace]

Runs each task through Agent with paid model calls, then verifies a fresh final observation independently.
Failed runs are recorded, never retried. results.json is rewritten after every run.
"""

import argparse
import json
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from bench.server import LocalPages
from bench.tasks import TASK_NAMES, load_task
from jev_ultrafast import Agent, settings
from jev_ultrafast.launch import launch_chrome

DEFAULT_TIME_BUDGET_SECONDS = 120
MAXIMUM_PAUSES_PER_RUN = 2
REPOSITORY = Path(__file__).resolve().parents[1]


class TimeBudgetExceeded(RuntimeError):
    pass


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", default=",".join(TASK_NAMES), help="Comma-separated task names.")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--headless", action="store_true", help="Launch a private headless Chrome for the run.")
    parser.add_argument("--out", type=Path, default=None, help="Default: artifacts/bench/<utc timestamp>.")
    parser.add_argument("--trace", action="store_true", help="Write a JSONL trace per run under <out>/traces.")
    parser.add_argument("--time-budget", type=float, default=DEFAULT_TIME_BUDGET_SECONDS, help="Seconds per run.")
    arguments = parser.parse_args(argv)
    arguments.tasks = [name.strip() for name in arguments.tasks.split(",") if name.strip()]
    for name in arguments.tasks:
        try:
            load_task(name)
        except ValueError as error:
            parser.error(str(error))
    if arguments.repeat < 1:
        parser.error("--repeat must be at least 1")
    arguments.out = arguments.out or Path("artifacts/bench") / utc_timestamp()
    return arguments


def utc_timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def git_output(*command):
    try:
        return subprocess.run(["git", *command], cwd=REPOSITORY, capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None


def source_revision():
    commit = git_output("rev-parse", "HEAD")
    changes = git_output("status", "--porcelain")
    return {"commit": commit.strip() if commit else None, "uncommitted_changes": bool(changes and changes.strip())}


def model_configuration():
    try:
        _, _, decision_model = settings.decision_route()
    except ValueError:
        decision_model = None
    return {"decision_model": decision_model, "text_model": settings.text_model_name()}


def token_total(usages, *keys):
    total = 0
    for usage in usages:
        usage = usage or {}
        total += next((usage[key] for key in keys if isinstance(usage.get(key), (int, float))), 0)
    return total


def drive(agent, task, deadline):
    """Run to done/blocked, answering pauses as the task specifies; returns (last snapshot, pause count)."""
    snapshot, pauses = agent.snapshot(), 0
    while True:
        for snapshot in agent.run():
            if time.monotonic() > deadline:
                raise TimeBudgetExceeded("Time budget exceeded")
        if snapshot["status"] != "paused":
            return snapshot, pauses
        pauses += 1
        on_pause = getattr(task, "ON_PAUSE", "reject")
        if on_pause == "stop":
            return snapshot, pauses
        snapshot = agent.approve() if on_pause == "approve" else agent.reject()
        if pauses >= MAXIMUM_PAUSES_PER_RUN or time.monotonic() > deadline:
            return snapshot, pauses


def run_record(name, run_index, task, pages, arguments, launched_chrome):
    url = pages.url(task.URL) if pages else task.URL
    record = dict(task=name, run=run_index, goal=task.GOAL, url=url, passed=False, checks={}, error=None)
    record.update(status=None, reason=None)
    trace_dir = arguments.out / "traces" / f"{name}-{run_index}" if arguments.trace else None
    extract = getattr(task, "EXTRACT", None)
    started = time.monotonic()
    agent, snapshot, pauses = None, None, 0
    try:
        agent = Agent(
            url,
            task.GOAL,
            headless=False if launched_chrome else None,
            trace_dir=trace_dir,
            extract=extract,
            pause_before=getattr(task, "PAUSE_BEFORE", None),
        )
        snapshot, pauses = drive(agent, task, started + arguments.time_budget)
    except Exception as error:
        record["error"] = f"{type(error).__name__}: {error}"
    try:
        if agent is not None:
            snapshot = agent.snapshot()
            final_page = agent.browser.observe(screenshot=False)
            verification = task.verify(final_page, (snapshot.get("extracted") or {}) if extract else None)
            if getattr(task, "PAUSE_BEFORE", None):
                verification["checks"]["paused"] = pauses > 0
                verification["passed"] = all(verification["checks"].values())
            record.update(checks=verification["checks"], final_url=final_page.get("url"))
            record["passed"] = bool(verification["passed"]) and record["error"] is None
    except Exception as error:
        record["error"] = record["error"] or f"Verification failed: {type(error).__name__}: {error}"
    finally:
        if agent is not None:
            agent.close()
    record["wall_ms"] = round((time.monotonic() - started) * 1000)
    if snapshot:
        decisions = snapshot.get("decisions", [])
        verification = snapshot.get("verification") or {}
        record.update(
            status=snapshot.get("status"),
            reason=snapshot.get("reason"),
            elapsed_ms=snapshot.get("elapsed_ms"),
            actions=len([h for h in snapshot.get("history", []) if h.get("kind") != "note"]),
            jev_requests=len(decisions),
            text_calls=len(snapshot.get("text_calls", [])),
            input_tokens=token_total([d.get("usage") for d in decisions], "input_tokens", "prompt_tokens"),
            output_tokens=token_total([d.get("usage") for d in decisions], "output_tokens", "completion_tokens"),
            verification_probability=verification.get("probability"),
            extracted=snapshot.get("extracted"),
            pauses=pauses,
            trace_path=snapshot.get("trace_path"),
        )
    return record


def write_results(folder, results):
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / "results.json.tmp"
    temporary.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    temporary.replace(folder / "results.json")


def main(argv=None):
    arguments = parse_arguments(argv)
    settings.load_dotenv()
    tasks = {name: load_task(name) for name in arguments.tasks}
    results = {
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **source_revision(),
        "models": model_configuration(),
        "headless": arguments.headless,
        "repeat": arguments.repeat,
        "time_budget_seconds": arguments.time_budget,
        "tasks": arguments.tasks,
        "platform": platform.platform(),
        "runs": [],
    }
    write_results(arguments.out, results)
    needs_local_pages = any("{local}" in task.URL for task in tasks.values())
    pages = LocalPages() if needs_local_pages else None
    launched_chrome = None
    try:
        if arguments.headless:
            launched_chrome = launch_chrome(profile_dir=settings.profile_dir(), headless=True)
        for run_index in range(1, arguments.repeat + 1):
            for name, task in tasks.items():
                record = run_record(name, run_index, task, pages, arguments, launched_chrome)
                results["runs"].append(record)
                write_results(arguments.out, results)
                verdict = "PASS" if record["passed"] else "FAIL"
                print(verdict, name, f"#{run_index}", record.get("elapsed_ms"), "ms", record["error"] or "", flush=True)
    finally:
        if launched_chrome:
            launched_chrome.close()
        if pages:
            pages.close()
    passed = sum(record["passed"] for record in results["runs"])
    print(f"{passed}/{len(results['runs'])} runs passed. Results: {arguments.out / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
