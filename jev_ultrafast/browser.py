"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import sys
import time
from pathlib import Path


def cdp(method, **params):
    """Browser Harness binds its daemon name at import, so import it only after launch settings exist."""
    from browser_harness.helpers import cdp as harness_cdp

    return harness_cdp(method, **params)


def harness_meta(request):
    """Browser Harness daemon meta requests, e.g. the dialog it last saw open."""
    from browser_harness.helpers import _send

    return _send({"meta": request})


def pending_dialog():
    """The JavaScript dialog the daemon last saw open in any attached tab, or None. Read-only; drains nothing."""
    try:
        return harness_meta("pending_dialog").get("dialog")
    except (RuntimeError, OSError):
        return None


# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"


KEY_CODES = {"Enter": 13, "Escape": 27, "ArrowDown": 40, "ArrowUp": 38}
SETTLE_SECONDS = 1.0
NEW_TAB_SECONDS = 3.0
NAVIGATION_SECONDS = 15.0
WAIT_CAP_SECONDS = 3.0
WAIT_POLL_SECONDS = 0.12
INPUT_RESPONSE_SECONDS = 2.5
AFTER_INPUT_RESPONSE_SECONDS = 1.5
DIALOG_PROBE_SECONDS = 0.3
HISTORY_LABEL_LIMIT = 80
DIALOG_MESSAGE_LIMIT = 300
MAX_ACTIONS = 250
KEYSTROKE_TEXT_LIMIT = 200
CONTEXT_GONE = ("Cannot find context", "Execution context was destroyed")
DOCUMENT_COMPLETE = """(limit => new Promise(resolve => {
  const check=()=>{ if (document.readyState==='complete') resolve(true); };
  document.addEventListener('readystatechange',check);
  setTimeout(()=>resolve(false),limit);
  check();
}))(%d)"""
DOCUMENT_READY = "document.readyState==='complete'"
NEW_TAB_READY = (
    "document.readyState==='complete' && (location.href!=='about:blank' || document.body?.childNodes.length>0)"
)
AFTER_INPUT_WAIT = """(action => new Promise(resolve => {
  const field=window.__jevFast?.nodes.get(action.node);
  const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
  let frames=0, stopped=false;
  const finish=()=>{stopped=true;resolve()};
  setTimeout(finish,autocomplete ? 200 : 50);
  const ready=()=>{
    if (stopped) return;
    const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
      .split(/\\s+/).filter(Boolean);
    const byId=id=>field.getRootNode().getElementById?.(id) || document.getElementById(id);
    const roots=ids.length ? ids.map(byId).filter(Boolean) : [document];
    const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
    if (++frames>=2 && (!autocomplete || options.some(e=>{
      const r=e.getBoundingClientRect();
      return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
        e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
    }))) finish();
    else requestAnimationFrame(ready);
  };
  requestAnimationFrame(ready);
}))(%s)"""
SCROLL_AREA = """(action => {
  const area=window.__jevFast?.nodes.get(action.container);
  if (!area?.isConnected || !area.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
  const r=area.getBoundingClientRect();
  const left=Math.max(r.left,0), right=Math.min(r.right,innerWidth);
  const top=Math.max(r.top,0), bottom=Math.min(r.bottom,innerHeight);
  if (right<=left || bottom<=top) return null;
  const x=(left+right)/2, y=(top+bottom)/2;
  let hit=document.elementFromPoint(x,y);
  while (hit?.shadowRoot) {
    const inner=hit.shadowRoot.elementFromPoint(x,y);
    if (!inner || inner===hit) break;
    hit=inner;
  }
  for (let n=hit; n; n=n.assignedSlot || n.parentElement || n.getRootNode().host || null)
    if (n===area) return {x,y};
  area.scrollBy({top:action.delta});
  return {scrolled:true};
})(%s)"""
NODE_GUARD ="(() => { const c=window.__jevFast; return c ? [c.pageKey(),c.guard(c.nodes.get(%d))] : null; })()"


class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class Browser:
    def __init__(self, url):
        from browser_harness.admin import ensure_daemon

        ensure_daemon()
        self.tabs = []
        self.frame_contexts = {}
        self.after_input = None
        # Headless Chrome stops painting background targets, so its screenshots hang; the user's Chrome keeps focus.
        self.background_tab = not browser_is_headless()
        self.target = cdp("Target.createTarget", url="about:blank", background=self.background_tab)["targetId"]
        self.seen_targets = {self.target}
        self.session = attach_tab(self.target)
        self.navigate(url)

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def navigate(self, url):
        """Load `url` in the current tab. Page.navigate returns once the new document has committed."""
        self.call("Page.navigate", url=url, _response_timeout=NAVIGATION_SECONDS)
        self.after_input, self.frame_contexts = None, {}
        self.wait_until(DOCUMENT_READY, NAVIGATION_SECONDS)

    def evaluate(self, expression, frame=None):
        response = self.evaluate_in(frame, expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def evaluate_in(self, frame, **params):
        """Top document: main world. Same-origin frame: its cached isolated world, renewed once if it was replaced."""
        if frame is None:
            return self.call("Runtime.evaluate", **params)
        for attempt in range(2):
            try:
                return self.call("Runtime.evaluate", contextId=self.frame_context(frame, renew=attempt > 0), **params)
            except RuntimeError as error:
                if attempt or not any(gone in str(error) for gone in CONTEXT_GONE):
                    raise StalePage("Frame changed. Observe again.") from error

    def frame_context(self, frame, renew=False):
        if renew or frame not in self.frame_contexts:
            try:
                world = self.call("Page.createIsolatedWorld", frameId=frame, worldName="jev", grantUniveralAccess=True)
            except RuntimeError as error:
                raise StalePage("Frame is gone. Observe again.") from error
            self.frame_contexts[frame] = world["executionContextId"]
        return self.frame_contexts[frame]

    def wait_until(self, expression, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.open_dialog():
                return False
            try:
                if self.evaluate(expression):
                    return True
            except (StalePage, RuntimeError, TimeoutError):
                pass
            time.sleep(0.02)
        return False

    def wait_for_document(self):
        """Only waits, never fast-forwards. A navigation that replaces the document gets one more wait."""
        deadline = time.monotonic() + SETTLE_SECONDS
        for _ in range(2):
            remaining = round((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                return
            try:
                response = cdp(
                    "Runtime.evaluate",
                    session_id=self.session,
                    _response_timeout=remaining / 1000 + 1,
                    expression=DOCUMENT_COMPLETE % remaining,
                    awaitPromise=True,
                    returnByValue=True,
                )
                if not response.get("exceptionDetails"):
                    return
            except RuntimeError:
                pass
            except TimeoutError:
                return

    def observe(self, screenshot=True):
        if dialog := self.open_dialog():
            self.after_input = None
            return self.dialog_page(dialog)
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.evaluate_in(
                    action.get("frame"),
                    expression=AFTER_INPUT_WAIT % json.dumps(action),
                    awaitPromise=True,
                    returnByValue=True,
                    _response_timeout=AFTER_INPUT_RESPONSE_SECONDS,
                )
            except (RuntimeError, StalePage, TimeoutError):
                pass
            self.wait_for_document()
        late_tab = self.adopt_new_tab()
        for attempt in range(40):
            try:
                page = self.read_page(screenshot)
                if late_tab:
                    page["new_tab"] = late_tab
                return page
            except StalePage:
                if attempt == 39:
                    raise
                time.sleep(0.025)
            except TimeoutError:
                if dialog := self.open_dialog():
                    return self.dialog_page(dialog)
                raise RuntimeError("The page stopped responding while it was read.") from None
        raise StalePage("Page did not settle")

    def open_dialog(self):
        """The JavaScript dialog blocking this tab, or None.

        The daemon keeps one dialog for all tabs: the main frame id identifies ours; otherwise (a dialog from a
        child frame) our page is blocked exactly when a trivial evaluation does not answer.
        """
        dialog = pending_dialog()
        if not dialog:
            return None
        if dialog.get("frameId") == self.target:
            return dialog
        try:
            cdp("Runtime.evaluate", session_id=self.session, _response_timeout=DIALOG_PROBE_SECONDS, expression="1")
        except TimeoutError:
            return dialog
        except RuntimeError:
            return None
        return None

    def dialog_page(self, dialog):
        """An observation of the open dialog: its message and the only actions it allows."""
        dialog_type = dialog.get("type") or "alert"
        message = (dialog.get("message") or "")[:DIALOG_MESSAGE_LIMIT]
        default_prompt = dialog.get("defaultPrompt") or ""
        try:
            info = cdp("Target.getTargetInfo", targetId=self.target)["targetInfo"]
        except (RuntimeError, KeyError, TimeoutError):
            info = {}
        actions = []
        if dialog_type == "prompt":
            actions.append({"id": "e1", "kind": "fill", "role": "textbox", "node": 0, "dialog_prompt": True,
                            "label": "Dialog answer", "value": default_prompt})
        if dialog_type == "alert":
            actions.append({"id": "accept_dialog", "kind": "dialog", "accept": True,
                            "label": f"Close the alert (OK): {message}"})
        else:
            actions.append({"id": "accept_dialog", "kind": "dialog", "accept": True,
                            "label": f"Accept the {dialog_type} dialog (OK): {message}"})
            actions.append({"id": "dismiss_dialog", "kind": "dialog", "accept": False,
                            "label": f"Dismiss the {dialog_type} dialog (Cancel)"})
        observed = {"type": dialog_type, "message": message, "default_prompt": default_prompt,
                    "url": dialog.get("url"), "frame": dialog.get("frameId")}
        page = {
            "url": info.get("url") or dialog.get("url") or "",
            "title": info.get("title") or "",
            "text": f"A {dialog_type} dialog is open: {message}",
            "w": 1120,
            "h": 780,
            "scroll": {"y": 0, "height": 780},
            "actions": actions,
            "dialog": observed,
            "marker": ["dialog", observed],
            "omitted_actions": 0,
            "skipped_frames": 0,
        }
        page["fingerprint"] = fingerprint(page)
        return page

    def read_page(self, screenshot):
        try:
            page = browser_operation({"operation": "observe", "session": self.session, "screenshot": screenshot,
                                      "background_tab": getattr(self, "background_tab", False)})
        except RuntimeError:
            if not self.return_from_closed_tab():
                raise
            raise StalePage("The active tab closed; returned to its opener.")
        page["skipped_frames"] = 0
        if page.get("child_frames"):
            self.merge_frames(page)
        if back := self.back_control():
            wait_at = next((i for i, a in enumerate(page["actions"]) if a["kind"] == "wait"), len(page["actions"]))
            page["actions"].insert(wait_at, back)
            page["fingerprint"] = fingerprint(page)
        return page

    def back_control(self):
        """Offer the tab's previous history entry, exactly as observed now."""
        try:
            history = self.call("Page.getNavigationHistory")
        except RuntimeError:
            return None
        index, entries = history["currentIndex"], history["entries"]
        if index <= 0 or entries[index - 1]["url"] == "about:blank":
            return None
        previous = entries[index - 1]
        where = (previous.get("title") or previous["url"])[:HISTORY_LABEL_LIMIT]
        return {
            "id": "go_back",
            "kind": "back",
            "label": f"Go back to the previous page ({where})",
            "entry": previous["id"],
            "from_entry": entries[index]["id"],
        }

    def go_back(self, action):
        """Navigate to the observed previous entry; refuse if the history moved since the observation."""
        history = self.call("Page.getNavigationHistory")
        index, entries = history["currentIndex"], history["entries"]
        if index <= 0 or entries[index]["id"] != action["from_entry"] or entries[index - 1]["id"] != action["entry"]:
            raise StalePage("Tab history changed since this decision. Observe again.")
        self.call("Page.navigateToHistoryEntry", entryId=action["entry"])
        self.after_input, self.frame_contexts = None, {}
        deadline = time.monotonic() + NAVIGATION_SECONDS
        while time.monotonic() < deadline:
            try:
                history = self.call("Page.getNavigationHistory")
            except RuntimeError:
                break
            if history["entries"][history["currentIndex"]]["id"] == action["entry"] or self.open_dialog():
                break
            time.sleep(0.02)
        self.wait_until(DOCUMENT_READY, NAVIGATION_SECONDS)
        return {"executed": action["id"]}

    def answer_dialog(self, action, page, text=None):
        """Accept or dismiss the observed dialog; a prompt answer is sent with acceptance."""
        dialog = self.open_dialog()
        if dialog is None or not same_dialog(dialog, page.get("dialog")):
            raise StalePage("The dialog closed or changed. Observe again.")
        if action.get("dialog_prompt"):
            params = {"accept": True, "promptText": text or ""}
        else:
            params = {"accept": bool(action["accept"])}
        try:
            self.call("Page.handleJavaScriptDialog", **params)
        except RuntimeError as error:
            raise StalePage("The dialog closed before it was answered. Observe again.") from error
        self.after_input = None
        self.wait_for_document()
        return {"executed": action["id"]}

    def wait_for_change(self, page):
        """WAIT: poll until the observed page differs, or the cap elapses. Never touches the page."""
        deadline = time.monotonic() + WAIT_CAP_SECONDS
        while time.monotonic() < deadline:
            time.sleep(WAIT_POLL_SECONDS)
            try:
                if not self.fresh(page):
                    return
            except (StalePage, RuntimeError, TimeoutError):
                return

    def merge_frames(self, page):
        """Append same-origin frame controls; ids continue e<n>, node ids stay per-frame integers."""
        tree = self.call("Page.getFrameTree")["frameTree"]
        top_origin = tree["frame"]["securityOrigin"]
        # Out-of-process frames are missing from this tree; window.length still counts them.
        page["skipped_frames"] += max(0, page["child_frames"] - len(tree.get("childFrames", [])))
        readable, in_tree, pending = [], {}, list(reversed(tree.get("childFrames", [])))
        while pending:
            node = pending.pop()
            frame = node["frame"]
            if frame["securityOrigin"] != top_origin:
                page["skipped_frames"] += 1 + count_frames(node)
                continue
            readable.append(frame["id"])
            in_tree[frame["id"]] = len(node.get("childFrames", []))
            pending.extend(reversed(node.get("childFrames", [])))
        self.frame_contexts = {frame: context for frame, context in self.frame_contexts.items() if frame in readable}
        elements = [action for action in page["actions"] if "node" in action]
        controls = [action for action in page["actions"] if "node" not in action]
        texts, markers, frames = [page["text"]], [page["marker"]], []
        for frame in readable:
            try:
                state = self.evaluate(READ_STATE, frame=frame)
            except StalePage:
                state = None
            if not state or "frame_offset" not in state:
                page["skipped_frames"] += 1
                continue
            page["skipped_frames"] += max(0, state["child_frames"] - in_tree[frame])
            for action in state["actions"]:
                rect = action.get("rect")
                if rect and 0 <= rect["x"] + rect["w"] / 2 < page["w"] and 0 <= rect["y"] + rect["h"] / 2 < page["h"]:
                    elements.append({**action, "frame": frame})
            if state["text"]:
                texts.append(state["text"])
            markers.append(state["marker"])
            page["omitted_actions"] += state["omitted_actions"]
            frames.append(
                {"id": frame, "url": state["url"], "offset": state["frame_offset"]}
                | {key: state[key] for key in ("page_key", "guards")}
            )
        if not frames:
            return
        page["omitted_actions"] += max(0, len(elements) - MAX_ACTIONS)
        elements = elements[:MAX_ACTIONS]
        for index, action in enumerate(elements, 1):
            action["id"] = f"e{index}"
        page.update(actions=elements + controls, text="\n\n[frame]\n".join(texts), marker=markers, frames=frames)
        page["fingerprint"] = fingerprint(page)

    def fresh(self, page, action=None):
        dialog = self.open_dialog()
        if "dialog" in page:
            return dialog is not None and same_dialog(dialog, page["dialog"])
        if dialog:
            return False
        if action is not None and action["kind"] in {"click", "select"}:
            node, frame = action["node"], action.get("frame")
            if type(node) is not int:
                return False
            observed = page if frame is None else next((f for f in page.get("frames", []) if f["id"] == frame), None)
            if observed is None:
                return False
            current = self.evaluate(NODE_GUARD % node, frame=frame)
            return current == [observed["page_key"], observed["guards"].get(str(node))]
        top = self.evaluate(MARKER)
        if "frames" not in page:
            return top == page["marker"]
        return [top, *(self.evaluate(MARKER, frame=f["id"]) for f in page["frames"])] == page["marker"]

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        kind = action["kind"]
        if kind == "wait":
            self.after_input = None
            self.wait_for_change(page)
            return {"executed": action["id"]}
        if kind == "dialog" or action.get("dialog_prompt"):
            return self.answer_dialog(action, page, text)
        if kind == "back":
            return self.go_back(action)
        request = {"operation": "act", "session": self.session, "action": action, "text": text}
        if action.get("frame"):
            request["context"] = self.frame_context(action["frame"])
        try:
            result = browser_operation(request)
        except TimeoutError:
            self.after_input = None
            if dialog := self.open_dialog():
                return {"executed": action["id"], "dialog": {"type": dialog.get("type") or "alert",
                                                             "message": dialog.get("message") or ""}}
            raise RuntimeError("Browser input did not complete; inspect the page before retrying.") from None
        self.after_input = action
        if kind in {"click", "key"} and (new_tab := self.adopt_new_tab()):
            result["new_tab"] = new_tab
        return result

    def adopt_new_tab(self):
        """Switch to a page this Browser's tabs just opened. Never raises: the action has already executed."""
        try:
            targets = cdp("Target.getTargets")["targetInfos"]
            owned = {self.target, *(target for target, _ in self.tabs)}
            opened = [
                info["targetId"]
                for info in targets
                if info["type"] == "page" and info.get("openerId") in owned
                and info["targetId"] not in self.seen_targets
            ]
            self.seen_targets.update(info["targetId"] for info in targets)
            if not opened:
                return None
            self.tabs.extend((target, None) for target in opened[:-1])
            session = attach_tab(opened[-1])
        except RuntimeError:
            return None
        self.tabs.append((self.target, self.session))
        self.target, self.session = opened[-1], session
        self.after_input, self.frame_contexts = None, {}
        self.wait_until(NEW_TAB_READY, NEW_TAB_SECONDS)
        try:
            return self.evaluate("({url: location.href, title: document.title})")
        except (StalePage, RuntimeError):
            return {"url": "", "title": ""}

    def return_from_closed_tab(self):
        """A popup may close itself (e.g. after a sign-in); continue in the tab that opened it."""
        try:
            alive = {info["targetId"] for info in cdp("Target.getTargets")["targetInfos"]}
        except RuntimeError:
            return False
        if self.target in alive:
            return False
        while self.tabs:
            target, session = self.tabs.pop()
            if session and target in alive:
                self.target, self.session = target, session
                self.after_input, self.frame_contexts = None, {}
                return True
        return False

    def close(self):
        for target in [self.target, *(target for target, _ in getattr(self, "tabs", []))]:
            if target:
                try:
                    cdp("Target.closeTarget", targetId=target)
                except RuntimeError:
                    pass
        self.target, self.tabs = None, []


def type_text(send_input, text):
    """Short text arrives as key presses so key and autocomplete listeners fire; long or multi-line text is inserted."""
    if len(text) > KEYSTROKE_TEXT_LIMIT or "\n" in text:
        send_input("Input.insertText", text=text)
        return
    for character in text:
        identity = key_identity(character)
        send_input("Input.dispatchKeyEvent", type="keyDown", text=character, unmodifiedText=character, **identity)
        send_input("Input.dispatchKeyEvent", type="keyUp", **identity)


def key_identity(character):
    """Key, code and legacy keyCode for a typed character; handlers reading event.which see ASCII keys."""
    identity = {"key": character}
    if character.isascii() and character.isalpha():
        identity.update(code=f"Key{character.upper()}", windowsVirtualKeyCode=ord(character.upper()))
    elif character.isascii() and character.isdigit():
        identity.update(code=f"Digit{character}", windowsVirtualKeyCode=ord(character))
    elif character == " ":
        identity.update(code="Space", windowsVirtualKeyCode=32)
    return identity


def browser_is_headless():
    try:
        version = cdp("Browser.getVersion")
    except (RuntimeError, OSError):
        return False
    return "Headless" in version.get("product", "") + version.get("userAgent", "")


def same_dialog(current, observed):
    return bool(observed) and (
        (current.get("type") or "alert", (current.get("message") or "")[:DIALOG_MESSAGE_LIMIT], current.get("url"),
         current.get("frameId"))
        == (observed["type"], observed["message"], observed["url"], observed["frame"])
    )


def attach_tab(target):
    session = cdp("Target.attachToTarget", targetId=target, flatten=True)["sessionId"]
    # Browser Harness records JavaScript dialogs only from sessions with the Page domain enabled.
    cdp("Page.enable", session_id=session)
    cdp(
        "Emulation.setDeviceMetricsOverride",
        session_id=session,
        width=1120,
        height=780,
        deviceScaleFactor=1,
        mobile=False,
    )
    # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
    cdp("Emulation.setFocusEmulationEnabled", session_id=session, enabled=True)
    return session


def count_frames(node):
    return sum(1 + count_frames(child) for child in node.get("childFrames", []))


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    context = {"contextId": request["context"]} if request.get("context") else {}

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def send_input(method, **params):
        """A dialog opened by this input blocks Chrome's reply, so the wait is short; the input was delivered."""
        return cdp(method, session_id=session, _response_timeout=INPUT_RESPONSE_SECONDS, **params)

    def evaluate(expression):
        try:
            result = call("Runtime.evaluate", expression=expression, returnByValue=True, **context)
        except RuntimeError as error:
            if context and "Cannot find context" in str(error):
                raise StalePage("Frame changed before execution. Observe again.") from error
            raise
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            if operation == "act" and request["action"].get("native_value"):
                raise RuntimeError("Field value execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll" and "container" in action:
            if type(action["container"]) is not int:
                raise ValueError("Invalid observed scroll area")
            wheel_point = evaluate(SCROLL_AREA % json.dumps(action))
            if wheel_point is None:
                raise StalePage("Scroll area changed or is hidden. Observe again.")
            if "x" in wheel_point:
                send_input(
                    "Input.dispatchMouseEvent",
                    type="mouseWheel",
                    x=wheel_point["x"],
                    y=wheel_point["y"],
                    deltaX=0,
                    deltaY=action["delta"],
                )
        elif kind == "scroll":
            send_input("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=650, deltaX=0, deltaY=action["delta"])
        elif kind == "key":
            key = action["key"]
            if key not in KEY_CODES:
                raise ValueError("Unsupported key")
            pressed = {"key": key, "code": key, "windowsVirtualKeyCode": KEY_CODES[key]}
            typed = {"text": "\r"} if key == "Enter" else {}
            send_input("Input.dispatchKeyEvent", type="keyDown", **pressed, **typed)
            send_input("Input.dispatchKeyEvent", type="keyUp", **pressed)
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            target = evaluate("""((action,text) => {
              const e=window.__jevFast?.nodes.get(action.node);
              const composedParent=n=>n.assignedSlot || n.parentElement || n.getRootNode().host || null;
              const composedClosest=(n,query)=>{ for (; n; n=composedParent(n)) if (n.matches(query)) return n; };
              if (!e?.isConnected || e.matches(':disabled') || composedClosest(e,'[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
                return {selected:true};
              }
              let hit=document.elementFromPoint(x,y);
              while (hit?.shadowRoot) {
                const inner=hit.shadowRoot.elementFromPoint(x,y);
                if (!inner || inner===hit) break;
                hit=inner;
              }
              let reachesTarget=false;
              for (let n=hit; n && !reachesTarget; n=composedParent(n)) reachesTarget=n===e;
              if (!reachesTarget) return null;
              const point=window.__jevFast.toTop(x,y,true);
              if (!point) return null;
              if (action.kind==='fill' && action.native_value) {
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,text);
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
                return {...point,accepted:e.value===text};
              }
              return point;
            })(""" + json.dumps(action) + "," + json.dumps(request.get("text")) + ")")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            native_fill = kind == "fill" and action.get("native_value")
            if native_fill and not target["accepted"]:
                raise RuntimeError("Field rejected the value; nothing else typed.")
            if kind != "select" and not native_fill:
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    send_input("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    send_input(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                        commands=["selectAll"],
                    )
                    send_input(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                    )
                    type_text(send_input, request["text"])
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = capture_screenshot(session, request.get("background_tab", False))
    return info


def capture_screenshot(session, background_tab):
    """A background tab stops producing frames when idle; a tiny screencast wakes its compositor first."""
    if not background_tab:
        return cdp("Page.captureScreenshot", session_id=session, format="jpeg", quality=72)["data"]
    cdp("Page.startScreencast", session_id=session, format="jpeg", quality=10, maxWidth=64, maxHeight=64)
    try:
        return cdp("Page.captureScreenshot", session_id=session, format="jpeg", quality=72)["data"]
    finally:
        try:
            cdp("Page.stopScreencast", session_id=session)
        except (RuntimeError, TimeoutError):
            pass
