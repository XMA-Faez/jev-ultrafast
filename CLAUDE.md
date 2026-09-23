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
- Plan of record: `~/.claude/plans/come-up-with-a-spicy-dolphin.md` (stages 0–4).

## Preferences & Rules
- Delegate large multi-part work to parallel agents with strict per-file ownership (user request, 2026-09-23).
- Avoid comments; use expressive names. Keep the loop small and readable.
- Tests never call paid APIs. Browser tests use headless Chrome from `tests/browser/conftest.py`.

## Patterns & Conventions
- Offline agent tests build `Agent` via `__new__` with a Mock browser (see the `runner` fixture in `tests/test_agent.py`).
- Browser tests use the `open_browser(html=..., page=..., url=...)` fixture; pages live in `tests/browser/pages/`.
- Verifiers inspect the final page independently of the model's DONE (see `bench/tasks/`).

## Learnings & Corrections
- ❌ Calling Browser Harness `restart_daemon` for a daemon we spawned waits 15 s, because the exited child stays a zombie and looks alive → ✅ send the `shutdown` meta request and `os.waitpid` it (`launch.stop_harness_daemon`).
- ❌ Polling for new tabs once right after a click missed pop-ups opened late (flaky test, 1 in 20) → ✅ poll on every observation; it costs ~0.2 ms.
- ❌ Asserting a tab is gone right after `Target.closeTarget` is racy → ✅ poll the target list briefly in tests.
- ❌ A fixed date in a benchmark goal expires → ✅ bench tasks compute dates relative to the run; historical examples keep their recorded dates.
- ❌ Registering pytest options in `tests/browser/conftest.py` fails in full runs → ✅ `pytest_addoption` lives in `tests/conftest.py`.

## Dependencies & Tooling
- `browser-harness==0.1.13`: CDP daemon; no headless launch of its own; Linux scan misses native Brave.
- `httpx[http2]`: model calls. `mcp[cli]`: MCP server (optional extra).
- Checks: `uv run ruff check .`, `uv run pytest`, `node --check jev_ultrafast/static/app.js`, `node --check jev_ultrafast/snapshot.js`, `uv build`.

## Component Registry
- `agent.py` loop · `model.py` decisions/text/verifier/extraction · `questions.py` instructions · `browser.py` CDP execution · `snapshot.js` DOM snapshot · `settings.py` env · `launch.py` private Chrome · `trace.py` JSONL traces · `demo.py` + `static/` inspector · `mcp_server.py` + `endpoint.py` MCP · `bench/` benchmark.

## API & Data Layer
- Decisions: TypeSafe `POST https://api.typesafe.ai/v1/systemone` or OpenRouter Decisions API. Text helper: OpenAI-compatible `/chat/completions`.

## Current State
- Stages 0–4 implemented (2026-09-23): settings, launcher, traces, browser tests + CI, keys, native inputs, settle, loop detection, DONE verifier, checkpoints, extraction, shadow DOM, same-origin frames, pop-ups, headless, multi-goal, MCP cleanup, benchmark.
- Not yet done: a paid benchmark run and scorecard; a paid Flights re-measure for the settle change; manual check of native Brave through `BU_CDP_WS` (about 10 s to click Allow); cross-origin iframes (design only, docs/frames.md).
- `BU_CDP_WS` puts the harness in cdp mode with a short handshake timeout, unlike its patient local mode.
