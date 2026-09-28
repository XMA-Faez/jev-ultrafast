# Project: Jev Ultrafast

Read AGENTS.md and README.md first. AGENTS.md invariants are hard rules.

## Overview
- **Type**: Python library + local inspector + MCP server for a browser agent
- **Stack**: Python 3.12+, httpx, Browser Harness (CDP), TypeSafe Jev decisions, OpenAI-compatible text helper, vanilla JS inspector
- **Package Manager**: uv (Python). Never npm.
- **Started**: 2026-09 (audit and roadmap: 2026-09-23)

## Architecture Decisions
- One TypeSafe request per decision: operation head plus per-operation target heads; only the chosen operation's target executes.
- All environment defaults live in `jev_ultrafast/settings.py`. Do not read env vars with defaults anywhere else.
- Browser Harness binds its daemon name (`BU_NAME`) at import, so `jev_ultrafast/browser.py` imports it lazily through a module-level `cdp` wrapper. Tests monkeypatch `browser.cdp`.
- Headless and test browsers come from `jev_ultrafast/launch.py`: a private Chrome with `--remote-debugging-port=0`, attached via `BU_CDP_URL` and a per-process `BU_NAME`. It never touches the user's default daemon.
- CDP events are not used: the harness only exposes a shared, destructive event buffer. Poll instead.
- MCP tabs are bounded (2026-09-26): `browser_task` with `session_id` + `url` navigates the same tab; a daemon thread closes sessions idle past `JEV_MCP_IDLE_MINUTES` (10); opening beyond `JEV_MCP_MAX_SESSIONS` (3) closes the least recently used idle session. Busy sessions are never closed; closed-session reasons are kept for the error message.
- JavaScript dialogs (2026-09-28): `attach_tab` enables the Page domain so the harness records `pending_dialog`; a dialog is ours when its `frameId` is our target (else a 0.3 s `Runtime.evaluate("1")` hangs). While one is open, `observe()` returns a dialog page with `accept_dialog`/`dismiss_dialog` (+ a prompt `TYPE_TEXT` target) and nothing evaluates in the page. Input dispatch has a 2.5 s timeout; a timeout with our dialog open means the input ran.
- `go_back` control comes from `Page.getNavigationHistory` (never back to our `about:blank` start) and refuses if history moved since observation.
- Loop guards (2026-09-28): stop `blocked` after the same action 10× in a row, 5 unchanged WAITs, 3 fields without helper text, or a third DONE whose verifier p < 0.1. A rejected DONE waits for a page change before re-deciding.
- Plan of record: `~/.claude/plans/come-up-with-a-spicy-dolphin.md` (stages 0–4).

- Remotes (2026-09-28): `fork` = github.com/XMA-Faez/jev-ultrafast (our work, push here); `origin` = upstream browser-use/jev-ultrafast (read only). `main` tracks `fork/main`.

## Preferences & Rules
- Delegate large multi-part work to parallel agents with strict per-file ownership (user request, 2026-09-23).
- Avoid comments; use expressive names. Keep the loop small and readable.
- Tests never call paid APIs. Browser tests use headless Chrome from `tests/browser/conftest.py`.

## Patterns & Conventions
- Offline agent tests build `Agent` via `__new__` with a Mock browser (see the `runner` fixture in `tests/test_agent.py`).
- Browser tests use the `open_browser(html=..., page=..., url=...)` fixture; pages live in `tests/browser/pages/`.
- Verifiers inspect the final page independently of the model's DONE (see `bench/tasks/`).

## Learnings & Corrections
- ❌ Headless Chrome stops painting a `background=True` target after ~3 s idle, so `Page.captureScreenshot` hangs → ✅ headless (detected via `Browser.getVersion`) creates foreground targets; the user's Chrome keeps background tabs and screenshots them after a tiny `Page.startScreencast` (then stop).
- ❌ WAIT slept 100 ms, so async content (dynamic loading, map search results) never arrived → ✅ WAIT polls `fresh(page)` until the page changes, capped at 3 s.
- ❌ Password inputs were filtered out, so logins were impossible → ✅ offered as `secret` fill targets with a masked value; page key/guards use the length; history/traces store `••••••`.
- ❌ `Input.insertText` fires no key events, so key listeners and keyup-driven autocompletes never saw typing → ✅ ≤200 chars are typed as key presses with `code`/`windowsVirtualKeyCode`.
- ❌ OpenRouter returns HTTP 200 with `{"error": {"code": 504}}` and trickles filler bytes on slow calls (a text call took 121 s) → ✅ `post_json` retries error bodies/5xx/transport errors and streams each attempt with a 20 s deadline.
- ❌ The text helper sometimes appends a code fence after the JSON → ✅ parse the first JSON object with `raw_decode`; a helper with no value is a `NoFieldText` note, not a crash.
- ❌ A native `<select>` covered by a styled overlay (Amazon sort) failed the click hit-test → ✅ selects are set programmatically, so no occlusion check.
- ❌ Unnamed checkboxes/selects were labelled by role or by all their option text → ✅ adjacent text, whitespace collapsed.
- ❌ `Page.navigate` used the harness's 5 s IPC default and timed out on slow sites → ✅ pass `_response_timeout=NAVIGATION_SECONDS`.
- ❌ The verifier demanded submitted form values stay visible and so rejected correct logins → ✅ VERIFY_DONE accepts a visible successful outcome for submitted values. It remains conservative (correct runs score 0.2–0.45).
- ❌ Calling Browser Harness `restart_daemon` for a daemon we spawned waits 15 s, because the exited child stays a zombie and looks alive → ✅ send the `shutdown` meta request and `os.waitpid` it (`launch.stop_harness_daemon`).
- ❌ Polling for new tabs once right after a click missed pop-ups opened late (flaky test, 1 in 20) → ✅ poll on every observation; it costs ~0.2 ms.
- ❌ Asserting a tab is gone right after `Target.closeTarget` is racy → ✅ poll the target list briefly in tests.
- ❌ A fixed date in a benchmark goal expires → ✅ bench tasks compute dates relative to the run; historical examples keep their recorded dates.
- ❌ The MCP opened a new tab per task and never closed them (user report, 2026-09-26) → ✅ reuse the session's tab for new URLs, close idle tabs, cap open tabs.
- ❌ Guarding `Page.navigate` against reading the old document → ✅ unnecessary: `Page.navigate` returns after the new document commits (tested with a slow server).
- ❌ Indexing only semantic elements missed Yandex Maps folder rows (div role=listitem, tabindex=0, cursor:pointer); the model clicked the five identical `More` buttons instead (user report, 2026-09-28) → ✅ snapshot.js infers script-only click targets and appends row text to repeated labels (`More (Bar)`).
- ❌ Page `SCROLL_DOWN` wheeled at (550,650) over the map and zoomed it while the side list never moved; it was offered because body content overflowed a `overflow:hidden` viewport (2026-09-28) → ✅ page scroll only when the viewport can scroll; overflowing panels get `scroll_down_in_<n>` actions targeted by node.
- ❌ Registering pytest options in `tests/browser/conftest.py` fails in full runs → ✅ `pytest_addoption` lives in `tests/conftest.py`.

## Dependencies & Tooling
- `browser-harness==0.1.13`: CDP daemon; no headless launch of its own; Linux scan misses native Brave.
- `scripts/inspect_page.py URL`: print Jev's element table for a page in headless Chrome, no model calls. Use it before guessing at label/snapshot bugs.
- `httpx[http2]`: model calls. `mcp[cli]`: MCP server (optional extra).
- Checks: `uv run ruff check .`, `uv run pytest`, `node --check jev_ultrafast/static/app.js`, `node --check jev_ultrafast/snapshot.js`, `uv build`.

## Component Registry
- `agent.py` loop · `model.py` decisions/text/verifier/extraction · `questions.py` instructions · `browser.py` CDP execution · `snapshot.js` DOM snapshot · `settings.py` env · `launch.py` private Chrome · `trace.py` JSONL traces · `demo.py` + `static/` inspector · `mcp_server.py` + `endpoint.py` MCP · `bench/` benchmark.

## API & Data Layer
- Decisions: TypeSafe `POST https://api.typesafe.ai/v1/systemone` or OpenRouter Decisions API. Text helper: OpenAI-compatible `/chat/completions`.

## Current State
- Stages 0–4 implemented (2026-09-23): settings, launcher, traces, browser tests + CI, keys, native inputs, settle, loop detection, DONE verifier, checkpoints, extraction, shadow DOM, same-origin frames, pop-ups, headless, multi-goal, MCP cleanup, benchmark.
- Live pressure test (2026-09-28): 29 public-site scenarios went from 1/23 to 25/29 passing; bench grew to 17 tasks (login_confirm, slow_catalogue, web_form, shop_checkout, map_search) and passed 15/17 headless. Remaining failures: CAPTCHAs/bot checks in headless (PyPI, DuckDuckGo), HN "newest" judged DONE on the front page, calculator digit loops, TodoMVC typing a second todo before Enter (model choices).
- Not yet done: a paid benchmark run and scorecard; a paid Flights re-measure for the settle change; manual check of native Brave through `BU_CDP_WS` (about 10 s to click Allow); cross-origin iframes (design only, docs/frames.md).
- `BU_CDP_WS` puts the harness in cdp mode with a short handshake timeout, unlike its patient local mode.
