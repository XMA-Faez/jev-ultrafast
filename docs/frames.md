# Frames

## Same-origin frames (implemented)

`Browser.observe` reads the top document as before. When `window.length` reports child frames, it calls `Page.getFrameTree` once and walks it depth first. A frame is read only when its `securityOrigin` equals the top document's origin, and so do all of its ancestors. Each readable frame gets an isolated world (`Page.createIsolatedWorld`, `worldName: "jev"`), and its context id is cached per frame id. When the frame navigates, the cached context disappears ("Cannot find context"). The world is then created again once, and the new world starts a new node cache, so earlier decisions become stale.

The snapshot runs unchanged in that world. When `window.frameElement` is set, it adds the frame's viewport offset to every rect and reports `frame_offset`. The offset is the frame element's rect plus its border and padding, summed up the frame chain. Merging:

- Frame element actions carry `"frame": <frame id>`. Ids are renumbered `e1..eN` across the merged list, capped at 250. Scroll, key and wait controls come from the top document only.
- Node ids stay integers per frame. The pair `(frame, node)` identifies an element.
- Frame text is appended after `\n\n[frame]\n`.
- `page["frames"]` lists `{id, url, offset, page_key, guards}`, and `page["marker"]` becomes `[top marker, frame markers...]`. Without readable frames, the page shape is unchanged.
- `page["skipped_frames"]` counts child frames that were not read. That covers cross-origin frames, out-of-process frames (missing from the parent's frame tree but counted by `window.length`), and frames that were not loaded yet.

Guards and execution run in the action's frame context. The executor hit-tests with the frame's own `elementFromPoint` at frame-local coordinates. It then walks up the chain and checks that each parent's `elementFromPoint` at the translated point is the frame element. It returns top-viewport coordinates. Mouse and keyboard input is dispatched on the top page session, and Chrome routes it into the frame.

## Cross-origin (out-of-process) frames: design spike

The experiment was a throwaway script that served `tests/browser/pages` from two local `http.server` ports. The parent came from `127.0.0.1` and the child from `localhost`. Those are different sites, so the child ran in its own process (an OOPIF).

| Step | Result | Local cost |
| --- | --- | --- |
| `Page.getFrameTree` on the parent session | The OOPIF is **absent** (0 children). `window.length` still counts it. | – |
| `Target.getTargets` | The child appears as `type: "iframe"`, with `parentId` set to the owning page target and `parentFrameId` set. | 0.6 ms |
| `Target.attachToTarget(flatten=True)` | Returns a session for the frame's process. | 0.4 ms |
| `Runtime.evaluate(READ_STATE)` in that session | Full snapshot with frame-local rects. `frameElement` is null, so there is no `frame_offset`. | 3.0 ms |
| `DOM.getFrameOwner(frameId=<iframe target id>)` + `DOM.getBoxModel` on the parent session | `content` quad = viewport offset of the frame's content box (border and padding included) | 1.0 ms |
| Click and `Input.insertText` on the **top** session at owner offset + frame rect | Reached the OOPIF's input and button ("Hello Grace") | – |

Design, if built:

1. On observe, when `child_frames` exceeds the tree's child count, call `Target.getTargets` once. Keep `iframe` targets whose `parentId` is the current tab, or a frame target that was already adopted (nested OOPIFs).
2. Attach flat once per target id, cache the session, and re-attach when the target id disappears. A cross-site navigation replaces the target. No event subscriptions: poll only.
3. Snapshot in the frame session. Get the offset from the owner's `content` quad on the parent session, summing up the `parentId` chain for nested OOPIFs. Add it to the rects in Python.
4. Actions carry `frame` plus the frame's session. Guards, markers and the executor script run in that session. The executor adds a parent-side check: in the parent session, `elementFromPoint` at the translated point must return the owner element (via `DOM.getNodeForLocation` or a main-world evaluate).
5. Input stays on the top session with translated coordinates.

Risks:

- **Non-atomic checks.** The parent occlusion check and the frame's target check are two evaluations in two processes. A layout shift between them can land a click elsewhere. That is weaker than the same-origin path, where one evaluation checks the whole chain.
- **Transforms.** A scaled or rotated iframe needs the full quad, not just its top-left corner. Clipping by an ancestor's overflow needs the parent-side hit test.
- **Cost and noise.** There are about 5 extra CDP calls per OOPIF per observation. Ad and tracking frames are common and would flood the element table. They need a cap and a filter such as `adFrameStatus` from the frame's own `Page.getFrameTree`, or a minimum visible size.
- **Sensitive embeds.** Payment and sign-in widgets are usually cross-origin frames. Typing into them should stay behind human checkpoints.
- **Lifecycle.** OOPIF targets come and go with navigations. A missing session must turn into `StalePage`, never into a retried mutation.

**Go/no-go: go, as a separate stage.** Every primitive worked in headless Chrome through Browser Harness, with no event subscriptions, at single-digit-millisecond cost. Implement it only with the parent-side occlusion check, an ad/size filter and a per-page OOPIF cap. Until then, cross-origin frames are skipped and counted in `skipped_frames`.
