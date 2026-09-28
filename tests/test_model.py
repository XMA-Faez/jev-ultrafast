"""Offline model-layer robustness: transport retries, diagnosable validation, tolerant JSON parsing. No paid APIs."""

from unittest.mock import Mock

import httpx
import pytest
from test_agent import choice, page

from jev_ultrafast import model, questions


def reply(status=200, body=None):
    return httpx.Response(status, json=body if body is not None else {"answers": {}})


@pytest.fixture
def sleeps(monkeypatch):
    recorded = []
    monkeypatch.setattr(model.time, "sleep", recorded.append)
    return recorded


def serve(monkeypatch, *outcomes):
    post = Mock(side_effect=list(outcomes))
    monkeypatch.setattr(model, "post_within_deadline", post)
    return post


class TricklingStream:
    """A provider response that keeps sending filler bytes, as OpenRouter does while a slow request waits."""

    def __init__(self, clock, chunks):
        self.clock, self.chunks = clock, chunks
        self.status_code, self.headers = 200, {"content-type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_bytes(self):
        for chunk in self.chunks:
            self.clock[0] += 5
            yield chunk


def test_a_response_that_trickles_past_the_deadline_is_abandoned_and_retried(monkeypatch, sleeps):
    clock = [0.0]
    monkeypatch.setattr(model.time, "monotonic", lambda: clock[0])
    streams = iter([TricklingStream(clock, [b" "] * 10), TricklingStream(clock, [b'{"answers": {}}'])])
    monkeypatch.setattr(model.CLIENT, "stream", lambda *_args, **_kwargs: next(streams))
    assert model.post_json("https://x.test", "k", {}) == {"answers": {}}
    assert sleeps == [0.5]


def test_a_response_that_always_trickles_fails_with_the_deadline(monkeypatch, sleeps):
    clock = [0.0]
    monkeypatch.setattr(model.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(model.CLIENT, "stream", lambda *_args, **_kwargs: TricklingStream(clock, [b" "] * 10))
    with pytest.raises(RuntimeError, match=r"longer than 20 s after 3 attempts; no action executed"):
        model.post_json("https://x.test", "k", {})


def test_error_body_with_transient_code_is_retried(monkeypatch, sleeps):
    idle = {"id": "gen-1", "error": {"message": "Upstream idle timeout exceeded", "code": 504}}
    post = serve(monkeypatch, reply(body=idle), reply(body={"answers": {"ok": 1}}))
    assert model.post_json("https://x.test", "k", {}) == {"answers": {"ok": 1}}
    assert post.call_count == 2 and sleeps == [0.5]


def test_error_body_that_keeps_failing_names_the_provider_message(monkeypatch, sleeps):
    idle = {"error": {"message": "Upstream idle timeout exceeded", "code": 504}}
    post = serve(monkeypatch, *[reply(body=idle)] * 3)
    with pytest.raises(RuntimeError, match="error 504: Upstream idle timeout exceeded after 3 attempts; no action"):
        model.post_json("https://x.test", "k", {})
    assert post.call_count == 3 and sleeps == [0.5, 1.0]


def test_permanent_error_body_raises_at_once_with_truncated_message(monkeypatch, sleeps):
    post = serve(monkeypatch, reply(body={"error": {"message": "bad model " + "x" * 500, "code": 400}}))
    with pytest.raises(RuntimeError) as raised:
        model.post_json("https://x.test", "k", {})
    message = str(raised.value)
    assert message.startswith("Model provider error 400: bad model") and message.endswith("; no action executed.")
    assert len(message) < 260 and post.call_count == 1 and sleeps == []


@pytest.mark.parametrize("status", sorted(model.TRANSIENT_STATUSES))
def test_transient_statuses_are_retried(monkeypatch, sleeps, status):
    post = serve(monkeypatch, reply(status), reply(body={"answers": {}}))
    assert model.post_json("https://x.test", "k", {}) == {"answers": {}}
    assert post.call_count == 2


def test_persistent_http_520_fails_after_three_attempts(monkeypatch, sleeps):
    serve(monkeypatch, *[reply(520)] * 3)
    with pytest.raises(RuntimeError, match="HTTP 520 after 3 attempts; no action executed"):
        model.post_json("https://x.test", "k", {})


def test_client_error_status_is_not_retried(monkeypatch, sleeps):
    post = serve(monkeypatch, reply(401))
    with pytest.raises(RuntimeError, match="HTTP 401; no action executed"):
        model.post_json("https://x.test", "k", {})
    assert post.call_count == 1


def test_transport_errors_are_retried_and_named(monkeypatch, sleeps):
    post = serve(monkeypatch, httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("reset"), reply())
    assert model.post_json("https://x.test", "k", {}) == {"answers": {}}
    assert post.call_count == 3
    serve(monkeypatch, *[httpx.ReadTimeout("slow")] * 3)
    with pytest.raises(RuntimeError, match=r"Model connection failed \(ReadTimeout\) after 3 attempts; no action"):
        model.post_json("https://x.test", "k", {})


def test_client_uses_a_short_connect_timeout():
    assert model.CLIENT.timeout.connect == 5 and model.CLIENT.timeout.read == 25


def decision_post(*answer_sets):
    return Mock(side_effect=[{"model": "test", "answers": answers} for answers in answer_sets])


def valid_click_answers(body_criteria):
    return {"operation": choice(body_criteria, "CLICK"), "click_target": choice(["1", "2"], "2")}


def test_invalid_decision_is_retried_once_then_accepted(monkeypatch):
    operations = ["TYPE_TEXT", "CLICK", "WAIT", "DONE", "BLOCKED"]
    post = decision_post({"operation": choice(operations, "CLICK")}, valid_click_answers(operations))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    chosen = model.choose(page(), "Press Go", [])
    assert chosen["choice"] == "e3" and post.call_count == 2


def test_invalid_decision_twice_names_the_head_and_reason(monkeypatch):
    operations = ["TYPE_TEXT", "CLICK", "WAIT", "DONE", "BLOCKED"]
    post = decision_post(*[{"operation": choice(operations, "CLICK")}] * 2)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match=r"\(click_target: missing answer\); no action executed"):
        model.choose(page(), "Press Go", [])
    assert post.call_count == 2


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda a: a.update(choice="z"), "choice 'z' not offered"),
        (lambda a: a["probabilities"].pop("b"), "probabilities miss 1 offered and add 0 unknown"),
        (lambda a: a["probabilities"].update(a=0.5), "probabilities sum to 0.500 over 2 options"),
        (lambda a: a.update(confidence=2), "confidence is not a number"),
        (lambda a: a.update(choice="b"), "choice is not the most probable option"),
    ],
)
def test_validation_reasons(mutate, reason):
    answer = choice(["a", "b"], "a")
    mutate(answer)
    with pytest.raises(ValueError, match=rf"^Invalid TypeSafe response \(operation: {reason}"):
        model.validate_choice(answer, {"a", "b"}, "operation")


def test_probability_sum_tolerance_scales_with_option_count():
    ids = [str(i) for i in range(400)]
    rounded_down = {i: 0.0 for i in ids} | {"0": 0.9}
    assert model.validate_choice({"choice": "0", "confidence": 0.9, "probabilities": rounded_down}, ids)
    small = {"a": 0.9, "b": 0.05}
    with pytest.raises(ValueError, match="sum to 0.950"):
        model.validate_choice({"choice": "a", "confidence": 0.9, "probabilities": small}, ["a", "b"])


def helper_reply(content):
    return Mock(return_value={"choices": [{"message": {"content": content}}], "usage": {}})


@pytest.mark.parametrize(
    "content",
    ['{"text": "httpx"}\n', '{"text": "httpx"}\n```', '```json\n{"text": "httpx"}\n```', '  {"text":"httpx"} extra'],
)
def test_text_helper_tolerates_whitespace_fences_and_trailing_text(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", helper_reply(content))
    assert model.field_text({"goal": "Search httpx"})[0] == "httpx"


@pytest.mark.parametrize(
    "content, message",
    [
        ('{"text": null}', "Text helper found no value for this field in the goal; nothing typed."),
        ("Thinking: httpx", "Text helper output was not valid JSON; nothing typed."),
        (None, "Text helper output was not valid JSON; nothing typed."),
        ('{"text": "a", "why": 1}', "Text helper output must have exactly the key `text`; nothing typed."),
        ('{"text": "  "}', "Text helper returned an empty, non-text, or oversized value; nothing typed."),
    ],
)
def test_text_helper_errors_say_why(monkeypatch, content, message):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", helper_reply(content))
    with pytest.raises(ValueError) as raised:
        model.field_text({"goal": "Search"})
    assert str(raised.value) == message


def entry(action, kind="click", text=None, page_changed=True, **extra):
    return {"action": action, "kind": kind, "text": text, "page_changed": page_changed, **extra}


def test_recent_actions_collapse_consecutive_repeats():
    history = [entry("1", step=i) for i in range(13)] + [entry("+"), entry("1"), entry("1")]
    assert model.recent_actions(history) == [
        {**entry("1"), "times": 13},
        entry("+"),
        {**entry("1"), "times": 2},
    ]


def test_recent_actions_keep_distinct_entries_and_apply_the_window_after_collapsing():
    history = [entry("A", text="x"), entry("A", text="y"), entry("A", page_changed=False)]
    assert [a.get("times") for a in model.recent_actions(history)] == [None, None, None]
    many = [entry(str(i)) for i in range(12)] + [entry("last")] * 5
    collapsed = model.recent_actions(many)
    assert len(collapsed) == 10 and collapsed[-1] == {**entry("last"), "times": 5}


def test_scroll_context_rides_in_choose_and_verify_requests(monkeypatch):
    bodies = []

    def post(_url, _key, body):
        bodies.append(body)
        if "done" in body["questions"]:
            return {"answers": {"done": {"noul": 0.5}}}
        return {"answers": {"operation": choice(body["questions"]["operation"]["criteria"], "WAIT")}}

    scrolled = {**page(), "scroll": {"y": 300.4, "height": 3000}, "h": 800}
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    model.choose(scrolled, "Open page 2", [])
    model.verify_done(scrolled, "Open page 2", [])
    assert [b["state"]["page"]["scroll"] for b in bodies] == [{"above_px": 300, "below_px": 1900}] * 2
    bare = {k: v for k, v in page().items() if k != "scroll"}
    model.choose(bare, "Open page 2", [])
    assert bodies[-1]["state"]["page"]["scroll"] == {"above_px": 0, "below_px": 0}


def test_rules_cover_scrolling_commits_repeats_dialogs_and_exact_pages():
    rules = questions.NEXT_ACTION
    assert "`page.scroll`" in rules and "before BLOCKED" in rules
    assert "TYPE_TEXT replaces the field's whole value" in rules and "`times`" in rules
    assert "GO_BACK" in rules and "dialog is open" in rules
    assert "each\nWAIT waits until the page changes" in rules
    assert "exact page" in rules and "exact page" in questions.VERIFY_DONE
