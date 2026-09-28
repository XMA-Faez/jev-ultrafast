# Benchmark

> No benchmark run has been recorded yet. This page is a placeholder; it contains no measured numbers.

`python -m bench.report` overwrites this file from one recorded run. It lists the run date, commit, models, per-task pass rate, median time, median Jev requests and median text calls, with a header naming the run the numbers come from.

## Record a run

Paid model calls. Needs `TYPESAFE_API_KEY` or `OPENROUTER_API_KEY` (and a text-model key for `TYPE_TEXT`).

```bash
uv run python -m bench.run --headless --repeat 3 --out artifacts/bench/latest
uv run python -m bench.report artifacts/bench/latest
```

`--tasks wikipedia,travel_fixture` limits the run. `--trace` writes a JSONL trace per run. The CI `benchmark` job does the same on manual dispatch and uploads `artifacts/bench`.

## Tasks

Each task in `bench/tasks/` is one natural-language goal plus a verifier that reads a fresh final observation (URL, title, visible text, controls) and ignores the model's `DONE`.

| Task | Site | What the verifier checks |
| --- | --- | --- |
| flights | Google Flights | Search URL, one-way, Zürich → London, the departure date 30 days after the run (in the Departure field and the encoded URL or page), visible matching flights |
| travel_fixture | local `fixture.html` | Casa Flora detail page with Design, Free cancellation and Lisbon applied |
| research_fixture | local `fixture.html` | The finite-choices article is open, by fragment, title and body text |
| wikipedia | en.wikipedia.org | Exact article path and title for Gödel's incompleteness theorems |
| duckduckgo | duckduckgo.com | Results URL whose `q` mentions pathlib, and at least two visible result links mentioning it |
| python_docs | docs.python.org | `/3/library/pathlib.html` and the module page title |
| hacker_news | news.ycombinator.com | `/newest`, its title, and a visible listing |
| keyboard_search | local, no submit button | Submitted `q`, a result count, and the expected note visible |
| shadow_settings | local, open shadow root | Saved weekly digest, weekend mute and topic, plus the saved notice |
| iframe_booking | local, same-origin iframe | Native date and range values submitted: 2026-10-14 and 4 guests |
| popup_docs | local, `target=_blank` | Pricing opened in the new tab and the extracted monthly price is $24 |
| checkpoint | local, library only | Runs with `pause_before=["delete"]` and rejects; passes if a pause happened and nothing was deleted |
| login_confirm | local, password + `confirm()` | Signed in as ada, the draft deleted through the confirm dialog, no dialog left open |
| slow_catalogue | local, results after 2.5 s | Query `lamp`, page 2 opened from pagination below the fold, and its results loaded |
| web_form | httpbin.org | The echoed POST has the name, large size, bacon and cheese, 19:30 and the instruction |
| shop_checkout | saucedemo.com | Runs with `pause_before=["finish"]` and stops at the pause: overview page with the backpack, no order placed |
| map_search | openstreetmap.org | A way/node/relation details page for the Paris Eiffel Tower |

The flights task departs 30 days after the run so the date is always searchable. `examples/flights.py` keeps the recorded September 20, 2026 trip that the README's demo measured.
