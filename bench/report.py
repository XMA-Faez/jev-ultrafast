"""python -m bench.report RESULTS [--output docs/benchmark.md]

Renders one recorded benchmark run (a results.json or the folder holding it) into a Markdown scorecard.
"""

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from statistics import median

from bench.tasks import load_task
from jev_ultrafast import settings

REPOSITORY = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY / "docs" / "benchmark.md"


def load_results(location):
    location = Path(location)
    return json.loads((location / "results.json" if location.is_dir() else location).read_text())


def current_commit():
    try:
        output = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPOSITORY, capture_output=True, text=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return output.strip() or None


def environment_models():
    try:
        _, _, decision_model = settings.decision_route()
    except ValueError:
        decision_model = None
    return {"decision_model": decision_model, "text_model": settings.text_model_name()}


def task_description(name):
    try:
        documentation = load_task(name).__doc__ or ""
    except ValueError:
        return ""
    return documentation.strip().splitlines()[0] if documentation.strip() else ""


def median_of(runs, key):
    values = [run[key] for run in runs if isinstance(run.get(key), (int, float))]
    return median(values) if values else None


def seconds(milliseconds):
    return "–" if milliseconds is None else f"{milliseconds / 1000:.2f} s"


def count(value):
    return "–" if value is None else f"{value:g}"


def percent(passed, total):
    return f"{passed / total:.0%}" if total else "–"


def failure_summary(runs):
    reasons = Counter()
    for run in runs:
        if run.get("passed"):
            continue
        if run.get("error"):
            reasons[run["error"].split(":")[0]] += 1
        for check, passed in (run.get("checks") or {}).items():
            if not passed:
                reasons[check] += 1
    return ", ".join(f"{reason} ×{times}" for reason, times in reasons.most_common()) or "–"


def render(results, source_label):
    runs = results.get("runs", [])
    by_task = {}
    for run in runs:
        by_task.setdefault(run["task"], []).append(run)
    models = results.get("models") or environment_models()
    commit = results.get("commit") or current_commit() or "unknown"
    dirty = " (with uncommitted changes)" if results.get("uncommitted_changes") else ""
    passed_total = sum(bool(run.get("passed")) for run in runs)
    lines = [
        "# Benchmark",
        "",
        f"> Every number on this page comes from one recorded run: `{source_label}`, started "
        f"{results.get('started_at', 'unknown')} at commit `{commit}`{dirty}. "
        "Re-running changes them. This is a small task suite, not a general reliability claim.",
        "",
        f"- Decision model: `{models.get('decision_model') or 'unknown'}`",
        f"- Text model: `{models.get('text_model') or 'unknown'}`",
        f"- Browser: {'private headless Chrome' if results.get('headless') else 'attached Chrome'}; "
        f"platform `{results.get('platform', 'unknown')}`",
        f"- Repeats per task: {results.get('repeat', '?')}; time budget {results.get('time_budget_seconds', '?')} s "
        "per run; failed runs are recorded, never retried",
        "",
        f"**Overall: {passed_total}/{len(runs)} runs passed ({percent(passed_total, len(runs))}).**",
        "",
        "A run passes only when an independent verifier accepts a fresh final observation and the run raised no error. "
        "Time is the agent clock (first decision to stop); medians cover all runs of a task.",
        "",
        "| Task | Passed | Pass rate | Median time | Median Jev requests | Median text calls "
        "| Failed checks / errors |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for task, task_runs in by_task.items():
        passed = sum(bool(run.get("passed")) for run in task_runs)
        lines.append(
            f"| {task} | {passed}/{len(task_runs)} | {percent(passed, len(task_runs))} "
            f"| {seconds(median_of(task_runs, 'elapsed_ms'))} | {count(median_of(task_runs, 'jev_requests'))} "
            f"| {count(median_of(task_runs, 'text_calls'))} | {failure_summary(task_runs)} |"
        )
    lines += ["", "## Tasks", ""]
    for task, task_runs in by_task.items():
        lines.append(f"- **{task}**: {task_description(task)} Goal: “{task_runs[0].get('goal', '')}”")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("results", type=Path, help="results.json or the folder containing it")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args(argv)
    settings.load_dotenv()
    results = load_results(arguments.results)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(render(results, arguments.results.as_posix()))
    print(arguments.output, flush=True)


if __name__ == "__main__":
    main()
