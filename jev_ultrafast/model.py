"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import time

import httpx

from .questions import EXTRACT, EXTRACT_MAX_BYTES, NEXT_ACTION, TARGET, TEXT_VALUE, VERIFY_DONE, VERIFY_DONE_CRITERIA
from .settings import decision_route, text_model

CLIENT = httpx.Client(http2=True, timeout=httpx.Timeout(25, connect=5))
TRANSIENT_STATUSES = {429, 500, 502, 503, 504, 520, 521, 522, 523, 524, 529}
MODEL_ATTEMPTS = 3
ATTEMPT_SECONDS = 20
PROVIDER_MESSAGE_CHARS = 200
RECENT_ACTION_KEYS = ("action", "kind", "text", "page_changed")


def provider_error(payload):
    """A provider failure reported inside an HTTP 200 body, as (code or None, message)."""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not error:
        return None
    details = error if isinstance(error, dict) else {"message": error}
    try:
        code = int(details.get("code"))
    except (TypeError, ValueError):
        code = None
    return code, str(details.get("message") or details)[:PROVIDER_MESSAGE_CHARS]


def post_within_deadline(url, key, body, deadline):
    """Providers keep slow requests alive with filler bytes, so the read timeout alone never ends them."""
    with CLIENT.stream("POST", url, json=body, headers={"Authorization": f"Bearer {key}"}) as response:
        chunks = []
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            if time.monotonic() > deadline:
                raise TimeoutError
        content_type = response.headers.get("content-type", "application/json")
        return httpx.Response(response.status_code, headers={"content-type": content_type}, content=b"".join(chunks))


def post_json(url, key, body):
    """POST a read-only model request, retrying transient failures a bounded number of times."""
    failure = None
    for attempt in range(MODEL_ATTEMPTS):
        if attempt:
            time.sleep(0.5 * 2 ** (attempt - 1))
        try:
            response = post_within_deadline(url, key, body, time.monotonic() + ATTEMPT_SECONDS)
        except httpx.HTTPError as error:
            failure = f"Model connection failed ({type(error).__name__})"
            continue
        except TimeoutError:
            failure = f"Model call took longer than {ATTEMPT_SECONDS} s"
            continue
        if response.status_code in TRANSIENT_STATUSES:
            failure = f"Model provider returned HTTP {response.status_code}"
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("Model provider returned a non-JSON body; no action executed.") from None
        error = provider_error(payload)
        if error is None:
            return payload
        code, message = error
        failure = f"Model provider error {code}: {message}" if code else f"Model provider error: {message}"
        if code not in TRANSIENT_STATUSES:
            raise RuntimeError(f"{failure}; no action executed.")
    raise RuntimeError(f"{failure} after {MODEL_ATTEMPTS} attempts; no action executed.")


def is_probability(number):
    return type(number) in (int, float) and math.isfinite(number) and 0 <= number <= 1


def probability_sum_tolerance(option_count):
    """Half a unit of three-decimal rounding per option, never tighter than 0.02."""
    return max(0.02, 0.0005 * option_count)


def choice_problem(answer, ids):
    if not isinstance(answer, dict) or not answer:
        return "missing answer"
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict):
        return "missing probabilities"
    chosen = answer.get("choice")
    if not isinstance(chosen, str) or chosen not in ids:
        return f"choice {chosen!r} not offered"
    missing, unknown = set(ids) - set(probabilities), set(probabilities) - set(ids)
    if missing or unknown:
        return f"probabilities miss {len(missing)} offered and add {len(unknown)} unknown options"
    if not all(is_probability(n) for n in probabilities.values()):
        return "a probability is not a number in [0, 1]"
    if not is_probability(answer.get("confidence")):
        return "confidence is not a number in [0, 1]"
    total = sum(probabilities.values())
    if abs(total - 1) > probability_sum_tolerance(len(ids)):
        return f"probabilities sum to {total:.3f} over {len(ids)} options"
    if probabilities[chosen] < max(probabilities.values()) - 1e-6:
        return "choice is not the most probable option"
    return None


def invalid_response(head, problem):
    return ValueError(f"Invalid TypeSafe response ({head}: {problem}); no action executed.")


def validate_choice(answer, ids, head="choice"):
    problem = choice_problem(answer, ids)
    if problem:
        raise invalid_response(head, problem)
    return answer


def answers_of(result):
    answers = result.get("answers") if isinstance(result, dict) else None
    return answers if isinstance(answers, dict) else {}


def ask_decision(url, key, body, read_answers):
    """Decision requests are pure reads, so one invalid response earns one fresh request."""
    for attempt in range(2):
        result = post_json(url, key, body)
        try:
            return result, read_answers(answers_of(result))
        except ValueError:
            if attempt:
                raise


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = (action.get("frame"), action["node"])
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def recent_actions(history, limit=10):
    """The last entries with consecutive identical ones collapsed into one entry counted by `times`."""
    runs = []
    for entry in history:
        summary = {k: entry.get(k) for k in RECENT_ACTION_KEYS}
        if runs and runs[-1][0] == summary:
            runs[-1][1] += 1
        else:
            runs.append([summary, 1])
    return [{**summary, "times": times} if times > 1 else summary for summary, times in runs[-limit:]]


def page_state(page):
    scroll = page.get("scroll") or {}
    above = scroll.get("y", 0)
    below = scroll.get("height", 0) - above - page.get("h", 0)
    return {
        **{k: page[k] for k in ("url", "title", "text")},
        "scroll": {"above_px": max(0, round(above)), "below_px": max(0, round(below))},
    }


def choose(state, goal, history, completed_goals=()):
    elements, targets, controls = action_space(state["actions"])
    goal_context = {"goal": goal, **({"completed_goals": list(completed_goals)} if completed_goals else {})}
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {**goal_context, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {**goal_context, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    url, key, model = decision_route()
    body = {
        "model": model,
        "state": {"page": page_state(state), "elements": elements, "recent_actions": recent_actions(history)},
        "questions": questions,
    }

    def read_answers(answers):
        operation_answer = validate_choice(answers.get("operation"), operations, "operation")
        operation = operation_answer["choice"]
        if operation not in targets:
            return operation_answer, None
        head = operation.lower() + "_target"
        return operation_answer, validate_choice(answers.get(head), targets[operation], head)

    started = time.perf_counter()
    result, (operation_answer, target_answer) = ask_decision(url, key, body, read_answers)
    operation = operation_answer["choice"]
    target = None
    probabilities = {}
    if target_answer:
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": answers_of(result),
        "model": result.get("model", model),
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def validate_noul(answer, head="noul"):
    probability = answer.get("noul") if isinstance(answer, dict) else None
    if probability is None:
        raise invalid_response(head, "missing answer")
    if not is_probability(probability):
        raise invalid_response(head, "noul is not a number in [0, 1]")
    return float(probability)


def verify_done(page, goal, history):
    """A second, independent noul judgment on whether the visible page satisfies the whole goal."""
    url, key, model = decision_route()
    body = {
        "model": model,
        "state": {
            "page": page_state(page),
            "elements": action_space(page["actions"])[0],
            "recent_actions": recent_actions(history),
        },
        "questions": {
            "done": {
                "type": "noul",
                "instructions": {"goal": goal, "rules": VERIFY_DONE},
                "criteria": VERIFY_DONE_CRITERIA,
            }
        },
    }
    started = time.perf_counter()
    result, probability = ask_decision(url, key, body, lambda answers: validate_noul(answers.get("done"), "done"))
    return {
        "probability": probability,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
        "model": result.get("model", model),
    }


def field_context(goal, action, page, history):
    field = {k: action.get(k) for k in ("label", "role", "value")}
    field.update({k: action[k] for k in ("format", "native_value") if k in action})
    return {
        "goal": goal,
        "field": field,
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def ask_text_helper(system_prompt, context):
    """One JSON-mode call to the OpenAI-compatible text helper; returns the raw content and call metadata."""
    helper = text_model()
    model = helper["model"]
    started = time.perf_counter()
    result = post_json(
        helper["base_url"] + "/chat/completions",
        helper["key"],
        {
            "model": model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            **helper["reasoning"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(context)},
            ],
        },
    )
    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        content = None
    return content, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}) if isinstance(result, dict) else {},
    }


def parse_json_object(content):
    """The first complete JSON object in the content; code fences and trailing text around it are ignored."""
    start = content.find("{") if isinstance(content, str) else -1
    if start < 0:
        raise ValueError("no JSON object")
    output, _ = json.JSONDecoder().raw_decode(content, start)
    return output


class NoFieldText(ValueError):
    """The text helper produced no usable value; nothing was typed."""


def field_text(context):
    content, meta = ask_text_helper(TEXT_VALUE, context)
    try:
        output = parse_json_object(content)
    except ValueError:
        raise NoFieldText("Text helper output was not valid JSON; nothing typed.") from None
    value = output.get("text")
    if set(output) != {"text"}:
        raise NoFieldText("Text helper output must have exactly the key `text`; nothing typed.")
    if value is None:
        raise NoFieldText("Text helper found no value for this field in the goal; nothing typed.")
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise NoFieldText("Text helper returned an empty, non-text, or oversized value; nothing typed.")
    return value, meta


def is_extracted_scalar(value):
    if isinstance(value, float):
        return math.isfinite(value)
    return value is None or isinstance(value, (str, int, bool))


def extraction_problem(output, schema):
    if not isinstance(output, dict):
        return "not a JSON object"
    missing, extra = set(schema) - set(output), set(output) - set(schema)
    if missing:
        return "missing keys " + ", ".join(sorted(missing))
    if extra:
        return "extra keys " + ", ".join(sorted(extra))
    nested = sorted(k for k, v in output.items() if not is_extracted_scalar(v))
    if nested:
        return "non-scalar values for " + ", ".join(nested)
    size = len(json.dumps(output).encode())
    if size > EXTRACT_MAX_BYTES:
        return f"too large ({size} bytes > {EXTRACT_MAX_BYTES})"
    return None


def extract_fields(goal, schema, page):
    """Structured values read from the visible page; every requested key present, scalars only, at most 4 KB."""
    context = {
        "goal": goal,
        "fields": schema,
        "page": {"url": page["url"], "title": page["title"], "text": page["text"][:6000]},
    }
    content, meta = ask_text_helper(EXTRACT, context)
    try:
        output = parse_json_object(content)
    except ValueError:
        raise ValueError("Extraction returned invalid data: not JSON.") from None
    problem = extraction_problem(output, schema)
    if problem:
        raise ValueError(f"Extraction returned invalid data: {problem}.")
    return output, meta
