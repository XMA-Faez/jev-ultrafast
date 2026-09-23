"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import time

import httpx

from .questions import EXTRACT, EXTRACT_MAX_BYTES, NEXT_ACTION, TARGET, TEXT_VALUE, VERIFY_DONE, VERIFY_DONE_CRITERIA
from .settings import decision_route, text_model

CLIENT = httpx.Client(http2=True, timeout=25)


def post_json(url, key, body):
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


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
    return [{k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-limit:]]


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
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": recent_actions(history),
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_json(url, key, body)
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
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
        "raw_answers": result["answers"],
        "model": result.get("model", model),
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def validate_noul(answer):
    try:
        probability = answer["noul"]
        valid = type(probability) in (int, float) and math.isfinite(probability) and 0 <= probability <= 1
    except (KeyError, TypeError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return float(probability)


def verify_done(page, goal, history):
    """A second, independent noul judgment on whether the visible page satisfies the whole goal."""
    url, key, model = decision_route()
    body = {
        "model": model,
        "state": {
            "page": {k: page[k] for k in ("url", "title", "text")},
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
    result = post_json(url, key, body)
    try:
        answer = result["answers"]["done"]
    except (KeyError, TypeError):
        answer = None
    return {
        "probability": validate_noul(answer),
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


def field_text(context):
    content, meta = ask_text_helper(TEXT_VALUE, context)
    try:
        output = json.loads(content)
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, meta


def is_extracted_scalar(value):
    if isinstance(value, float):
        return math.isfinite(value)
    return value is None or isinstance(value, (str, int, bool))


def extract_fields(goal, schema, page):
    """Structured values read from the visible page; every requested key present, scalars only, at most 4 KB."""
    context = {
        "goal": goal,
        "fields": schema,
        "page": {"url": page["url"], "title": page["title"], "text": page["text"][:6000]},
    }
    content, meta = ask_text_helper(EXTRACT, context)
    try:
        output = json.loads(content)
        valid = (
            isinstance(output, dict)
            and set(output) == set(schema)
            and all(is_extracted_scalar(v) for v in output.values())
            and len(json.dumps(output).encode()) <= EXTRACT_MAX_BYTES
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError("Extraction returned invalid data.")
    return output, meta
