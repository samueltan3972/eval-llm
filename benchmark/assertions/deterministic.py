"""Deterministic, reference-based assertions — each grades to 0 or 1.

Types mirror the ones used in datasets/*.yaml: equals, contains, icontains, contains-all,
regex, is-json (JSON-schema validated), contains-json (partial-schema match against the
first JSON object found in the output).
"""

from __future__ import annotations

import json
import re

try:
    from jsonschema import validate as _js_validate
    from jsonschema import ValidationError as _JsValidationError
    _HAVE_JSONSCHEMA = True
except Exception:  # pragma: no cover
    _HAVE_JSONSCHEMA = False

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _result(passed: bool, reason: str) -> dict:
    return {"pass": passed, "score": 1.0 if passed else 0.0, "reason": reason}


def extract_json(output: str):
    """Best-effort: parse a JSON value out of model output (strips ``` fences, then
    falls back to the first balanced {...} or [...] block). Returns the parsed value or
    raises ValueError."""
    text = output.strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first balanced object/array.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise ValueError("no parseable JSON found in output")


def evaluate_deterministic(assertion: dict, output: str) -> dict:
    atype = assertion.get("type")
    value = assertion.get("value")

    if atype == "equals":
        return _result(output.strip() == str(value).strip(), "exact match" if output.strip() == str(value).strip() else f"expected {value!r}")

    if atype == "contains":
        return _result(str(value) in output, f"contains {value!r}")

    if atype == "icontains":
        return _result(str(value).lower() in output.lower(), f"contains (case-insensitive) {value!r}")

    if atype == "contains-all":
        missing = [v for v in value if str(v) not in output]
        return _result(not missing, "all present" if not missing else f"missing {missing}")

    if atype == "contains-any":
        present = any(str(v) in output for v in value)
        return _result(present, "at least one present" if present else f"none of {value} present")

    if atype == "regex":
        ok = re.search(str(value), output) is not None
        return _result(ok, f"regex {value!r} {'matched' if ok else 'did not match'}")

    if atype in ("is-json", "contains-json"):
        return _evaluate_json(value, output)

    return {"pass": None, "score": None, "reason": f"unsupported deterministic type {atype!r}"}


def _evaluate_json(schema, output: str) -> dict:
    try:
        parsed = extract_json(output)
    except ValueError as e:
        return _result(False, str(e))
    if schema is None:
        return _result(True, "valid JSON")
    if not _HAVE_JSONSCHEMA:
        return _result(True, "valid JSON (schema not checked: jsonschema unavailable)")
    try:
        _js_validate(instance=parsed, schema=schema)
        return _result(True, "JSON matches schema")
    except _JsValidationError as e:
        return _result(False, f"schema mismatch: {e.message}")
