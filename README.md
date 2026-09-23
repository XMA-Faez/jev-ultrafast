<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Jev Ultrafast ⚡

> [!IMPORTANT]
> **The Browser Use Cloud waitlist is open.** Get early access to ultrafast browser agents in the cloud.
> **[Join the waitlist →](https://browser-use.com/ultrafast?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)**

**A browser agent with a dynamic, indexed action space.**

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an element. A small LLM writes text only when the operation is `TYPE_TEXT`.

**Zürich → London on Google Flights in 7.1 seconds.** One natural-language goal, actual text generation, and loading waits included.

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="A real Google Flights search at 1× speed, with generated city names and dynamic operation/target decisions" width="100%" /></a>

[Watch the MP4](docs/demo.mp4) · [Measurements](docs/performance.md) · [Read the loop](jev_ultrafast/agent.py)

## The action space

Every observation produces a new element table:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `PRESS_ENTER`, `PRESS_ESCAPE`, `ARROW_UP`/`ARROW_DOWN` (only in an open list), `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered. Date, time, range, and color inputs take a formatted value through `TYPE_TEXT`.

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

Target questions are speculative. If the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**. Each target head contains only compatible elements. Native dropdown choices carry an observed element/option index.

There are no site-specific action scripts or prepared field strings in the policy. The Flights example supplies a goal and independently verifies the outcome. The screenshot renderer adds labels afterward; it does not drive the browser.

## Try it

```bash
git clone https://github.com/browser-use/jev-ultrafast.git
cd jev-ultrafast
uv sync
cp .env.example .env
# One OPENROUTER_API_KEY covers decisions and text, or set TYPESAFE_API_KEY + TEXT_MODEL_API_KEY.
uv run jev
```

Open **http://127.0.0.1:8766** and click **Start demo → Run automatically**. The inspector shows numbered elements, operation probabilities, target probabilities, and executed actions. **Choose next** pauses before execution.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness), installed by `uv sync`. Run `uv run browser-harness --doctor` if it needs connecting. Allow remote debugging in Chrome when prompted.

With only `OPENROUTER_API_KEY`, Jev decisions go through OpenRouter's Decisions API and the same key pays for text. A `TYPESAFE_API_KEY` takes precedence for decisions. The current demo uses `inception/mercury-2.5` with reasoning disabled. Gemini, GLM, and DeepSeek can also use the OpenAI-compatible text helper; configure the appropriate model, endpoint, and reasoning setting.

## Use the library

```python
from jev_ultrafast import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London on September 20, 2026, "
    "for one adult in economy. Stop when matching flight options are visible.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

Run with `uv run --env-file .env python your_script.py`. The same policy can run a different task:

```bash
uv run --env-file .env python examples/run.py \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`uv run --env-file .env python examples/flights.py --keep-open` performs the flight search, checks the actual route/date/results, and saves its trace. It does not select or book a flight.

### Options

```python
Agent(
    url,
    goals,                      # one goal, or a list run in order on the same tab
    headless=True,              # private headless Chrome instead of your running browser
    trace_dir="artifacts/traces",  # JSONL events + final state per run
    pause_before=["buy", "delete"],  # stop before matching actions; call approve() or reject()
    extract={"price": "the monthly price"},  # values read from the final page into snapshot["extracted"]
    done_threshold=0.5,         # second-opinion check on DONE; None disables it
)
```

A run stops with status `done`, `blocked`, or `paused`, plus a `reason`. On `paused`, `snapshot()["pending"]` names the action; `agent.approve()` executes exactly that decision and `agent.reject()` skips it. When the model chooses `DONE`, one extra Jev yes/no question checks the visible page against the goal; a rejected `DONE` is fed back as history, and the third `DONE` is accepted with `verification.accepted` set to false. Environment equivalents: `JEV_HEADLESS=1`, `JEV_PROFILE_DIR` (keeps headless logins between runs), `JEV_TRACE_DIR`, `JEV_CHROME_PATH`.

### Headless and servers

Headless mode launches its own Chromium-family browser with `--remote-debugging-port=0` and a private profile, so it needs no approval prompt and works in CI. Without it, owned tabs run in your existing Chrome profile and share its logins. Log in once in headless mode by pointing `JEV_PROFILE_DIR` at a folder and reusing it.

## MCP server

```bash
uv sync --extra mcp
uv run jev-mcp
```

Client configuration:

```json
{"mcpServers": {"jev-browser": {"command": "uv", "args": ["--directory", "/path/to/jev-ultrafast", "run", "jev-mcp"]}}}
```

Tools: `browser_task` (goal or goals, optional `url`, `session_id`, `pause_before`, `extract`, `headless`), `browser_approve`, `browser_reject`, `browser_read`, `browser_screenshot`, `browser_sessions`, `browser_close`. Results carry `status`, `reason`, `verification`, `extracted`, the executed steps, the final page text, and `trace_path`. On Linux, a native Brave or Chrome profile that Browser Harness does not scan is found through its `DevToolsActivePort` file.

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. The inspector opts into screenshots; the video uses a separate continuous screencast.
- **One browser call per snapshot.** Read visible controls, their names, values, and text atomically. Keep references to the actual DOM nodes.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby context. Animation alone does not force another prediction. Resolve current geometry and reject covered controls before input.
- **Wait for useful state.** After typing into a combobox, wait for visible suggestions, capped at 200 ms. Other interactions get at most two animation frames or 50 ms, then the document gets up to 1 s to finish loading after a navigation. These reads happen after execution is logged.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without switching Chrome's visible tab.
- **Send visible text.** Offscreen article bodies and footers do not fill the model context.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the entire text-helper input is unchanged.

Every executed target is resolved from an observed node. The executor rechecks page freshness and click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable JavaScript. Text-helper output must parse as a small JSON object before typing.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](jev_ultrafast/agent.py) | The complete loop and text-helper handoff |
| [snapshot.js](jev_ultrafast/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.py](jev_ultrafast/browser.py) | Browser connection, current geometry, execution |
| [model.py](jev_ultrafast/model.py) | Dynamic operation/target heads and text generation |
| [questions.py](jev_ultrafast/questions.py) | Model instructions |
| [demo.py](jev_ultrafast/demo.py) | Local inspector |
| [settings.py](jev_ultrafast/settings.py) | Every environment default, once |
| [launch.py](jev_ultrafast/launch.py) | Private headless Chrome |
| [trace.py](jev_ultrafast/trace.py) | Per-run JSONL traces |
| [mcp_server.py](jev_ultrafast/mcp_server.py) | MCP tools |
| [bench/](bench) | Benchmark tasks with independent verifiers |

## Evidence and limits

The current video is a **7,073 ms** Google Flights run. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. A fresh independent check verifies the one-way setting, Zürich, London, September 20, 2026, and visible flight options. The video plays at 1×, with no opening hold and a 0.5-second final hold.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification; the built-in verifier is a second opinion, not proof. The DOM reader handles common HTML and ARIA controls, open shadow roots, same-origin iframes, and tabs or pop-ups opened by the page. It does not implement the full accessible-name specification. Cross-origin iframes are counted and skipped ([design note](docs/frames.md)). Canvas, uploads, nested scrolling, closed shadow roots, and arbitrary keyboard widgets remain unsupported. Attached mode shares the existing Chrome profile.

A broader task suite lives in [bench/](bench): public sites and local pages for keyboard submit, shadow DOM, iframes, pop-ups, native inputs, and checkpoints. Results appear in [benchmark.md](docs/benchmark.md) only after a recorded run.

## Development

```bash
uv sync --extra mcp
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

Tests make no model calls. `tests/browser` drives a private headless Chromium and skips when none is installed (`JEV_CHROME_PATH` selects one, `JEV_SKIP_BROWSER_TESTS=1` skips them). `uv run python scripts/check_guards.py` runs the same browser tests against your running Chrome. CI runs lint, offline tests, browser tests, and the build on every push; the paid benchmark runs only on manual dispatch. Live examples and recording scripts make paid API calls. `scripts/record_flights.py <new-folder>` captures original browser timestamps; `scripts/render_demo.py <recording-folder>` renders that verified run at 1× and crops out the Google account strip. Credentials and raw traces stay ignored.

---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
