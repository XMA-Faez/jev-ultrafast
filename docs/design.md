# Dynamic operation + target

The input is a natural-language goal. Every page observation builds an indexed table of accessible elements and their current values. One node receives one index, even when it supports both clicking and typing.

One TypeSafe request asks which operation to perform and which target would be appropriate for each available operation. The executor consumes only the target head corresponding to the selected operation. This avoids serial operation-then-target calls and rejects targets incompatible with the operation. Dropdown targets include a code-owned option index.

Operation and target questions receive the same next-step rules. Target criteria include current values and checked/selected state. The questions run independently: a target cannot read the operation answer, so its premise explicitly names the operation it assumes.

TYPE_TEXT sends the goal, selected field, visible page context, and recent actions to a small LLM. Its JSON must contain exactly one valid `text` value. The code does not extract quoted literals. A value can be reused after a stale decision only while the entire helper input is identical, and is discarded after a successful mutation.

## Runtime

One browser-side DOM snapshot supplies common HTML/ARIA roles, names, values, visible text, and executable targets. A WeakMap gives each actual node a code-owned identity; a Map keeps the live references used for execution. Replaced elements receive new identities, disconnected references are pruned, and navigation starts a new cache. These IDs are not CDP backend node IDs. Geometry is always read again immediately before input.

The model sees visible text. Background focus emulation keeps animation frames running in the owned tab. Screenshots are optional and disabled in library calls by default; `screenshots=True` or `record_dir=...` enables them. The inspector enables them explicitly. A continuous screencast can record a run separately.

Freshness compares semantic state instead of counting DOM mutations. Before a click/select, guards compare the document, full URL, viewport, safe form values/states, selected target, and nearby form/dialog/row context. Text generation, typing, scrolling, waiting, and completion use a full semantic comparison. The executor rechecks target visibility, enabled state, geometry, and click occlusion. Scoped guards intentionally permit unrelated visible content to change; this is a practical heuristic, not proof that arbitrary page changes are irrelevant to the goal.

Browser mutations are not retried by transport recovery. Completed execution is logged before the next observation, including when that observation encounters a navigation. An interrupted native-select evaluation stops because its change event may already have fired. Typing uses a browser select-all command followed by CDP text insertion, so existing input contents are replaced.

The next observation waits for up to two animation frames or 50 ms after an interaction. Editable ARIA comboboxes instead wait for visible options, capped at 200 ms. This avoids paying for a prediction before autocomplete suggestions arrive. Every observation then waits for `document.readyState === 'complete'`, capped at 1 s, so a click that navigates does not cost a paid WAIT decision on a half-loaded page. Chrome holds evaluations while a cross-document navigation is pending, so the first read after a navigation already returns the new document. An explicit WAIT remains 100 ms; network loading is never fast-forwarded in the recording.

## Reach

Key controls (`PRESS_ENTER`, `PRESS_ESCAPE`, and arrow keys while a list is focused) are code-owned operations like SCROLL and WAIT. The focused element is part of the page marker, so a key or fill decision goes stale when focus moves. Date, time, month, range, and color inputs are fills with a `format` hint; the executor sets the value through the native setter and refuses, without retrying, if the browser normalizes it away.

Open shadow roots are walked in document order, for controls, visible text, form values, labels, and hit-testing. Same-origin iframes are read in a per-frame isolated world; their rects are translated into top-viewport coordinates, their guards are checked in their own context, and element indices are keyed by frame and node. Cross-origin iframes are counted in `skipped_frames`; [frames.md](frames.md) records the design for them. Every observation polls the browser's target list (about 0.2 ms) and adopts a page opened by an owned tab, so a pop-up is seen before the next decision even when the page opens it late. A pop-up that closes itself returns control to its opener.

## Trust

`DONE` triggers one Jev yes/no question over the same observed state; below `done_threshold` the rejection becomes a history note, and the third `DONE` for a goal is accepted and marked unverified. `pause_before` stops before a matching action label without consuming the decision; approval executes exactly that decision under the normal freshness guards. Extraction uses the text helper with strict validation: exactly the requested keys, scalar values, at most 4 KB. Loop detection blocks the third identical action on an identical page. Every run can write a JSONL trace of observations, decisions, executions, and outcomes.

## What changed after the first demo

The initial prototype used five manually prepared steps and copied quoted strings. That proved finite-choice browser execution but did not demonstrate task decomposition or text generation. The current policy removes that shortcut and uses the original goal throughout. Operation/target distributions replace the old flat-choice/lookahead/Noul arrangement.

The audit also found that treating every INPUT as editable misclassified checkboxes. Editable roles now control TYPE_TEXT availability. Tests cover checkbox/radio/button distinction, invalid operation/target outputs, stale decisions, text-cache invalidation, missing credentials, waits, and final-route verification.

## Boundaries

Sixty browser actions and 120 decision requests bound a run. Up to 250 action candidates are retained; truncated candidates cannot be selected. The service stays loopback-only, serializes inspector actions, and checks Host, Origin, and a local request token. Credentials remain server-side. Attached tabs share the existing Chrome profile; headless runs use a private or `JEV_PROFILE_DIR` profile.

The policy is generic, but two websites do not establish broad reliability. Name resolution covers common labels, ARIA references, and text; it is not the browser's full accessibility algorithm. Closed shadow roots, cross-origin frames, canvas, uploads, nested scrolling, and complex keyboard widgets can block progress. A valid action can still be wrong. Independent checks, rather than the model's DONE choice, determine whether the demonstrated task succeeded.
