from benchmark.assertions import grade_case
from benchmark.assertions.deterministic import evaluate_deterministic, extract_json
from benchmark.assertions.js_bridge import evaluate_js
from benchmark.assertions.judge import build_judge_prompt, parse_judge_reply


def _score(assertion, output):
    return evaluate_deterministic(assertion, output)["score"]


def test_equals_trims():
    assert _score({"type": "equals", "value": "1081"}, "1081\n") == 1.0
    assert _score({"type": "equals", "value": "1081"}, "1082") == 0.0


def test_contains_and_all():
    assert _score({"type": "contains", "value": "cat"}, "a cat sat") == 1.0
    assert _score({"type": "contains-all", "value": ["a", "b"]}, "a and b") == 1.0
    assert _score({"type": "contains-all", "value": ["a", "z"]}, "a only") == 0.0


def test_regex():
    assert _score({"type": "regex", "value": r"\b80(\.0+)?\b"}, "80 km/h") == 1.0
    assert _score({"type": "regex", "value": r"^ACK$"}, "nope") == 0.0


def test_is_json_schema():
    schema = {"type": "object", "required": ["name", "age"],
              "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}
    ok = '{"name": "Jane", "age": 34}'
    bad = '{"name": "Jane"}'
    assert _score({"type": "is-json", "value": schema}, ok) == 1.0
    assert _score({"type": "is-json", "value": schema}, bad) == 0.0


def test_extract_json_from_fences():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('here: {"a": [1,2]} done') == {"a": [1, 2]}


def test_contains_json_const():
    schema = {"type": "object", "properties": {"quantity": {"const": 3}}}
    assert _score({"type": "contains-json", "value": schema}, '{"quantity": 3, "x": 1}') == 1.0
    assert _score({"type": "contains-json", "value": schema}, '{"quantity": 4}') == 0.0


def test_js_bridge_executes_generated_code():
    code = (
        "const m=output.match(/```(?:javascript|js)?\\s*([\\s\\S]*?)```/i);"
        "const c=(m?m[1]:output).trim();"
        "const fn=new Function(c+\"\\nreturn typeof add==='function'?add:null;\")();"
        "const ok=fn(2,3)===5 && fn(-1,1)===0; return {pass:ok,score:ok?1:0};"
    )
    r = evaluate_js(code, "```js\nfunction add(a,b){return a+b}\n```")
    assert r["pass"] and r["score"] == 1.0


def test_js_bridge_word_limit_bool():
    r = evaluate_js("return output.trim().split(/\\s+/).filter(Boolean).length <= 5;", "one two three")
    assert r["pass"] is True


def test_judge_prompt_is_blind():
    p = build_judge_prompt("Be concise", "Some answer")
    for leak in ("codex", "antigravity", "claude", "gpt", "gemini"):
        assert leak not in p.lower()


def test_judge_parse_variants():
    assert parse_judge_reply('{"score": 0.9, "pass": true}')["score"] == 0.9
    # clamps out-of-range
    assert parse_judge_reply('{"score": 1.7}')["score"] == 1.0
    # fallback to a bare number
    assert parse_judge_reply("rating: 0.4 / 1")["score"] == 0.4


def test_grade_case_mean_of_multiple():
    # one passing deterministic + one failing => quality 0.5
    asserts = [
        {"type": "contains", "value": "yes"},
        {"type": "contains", "value": "zzz"},
    ]
    out = grade_case(asserts, "the answer is yes")
    assert out["quality"] == 0.5


def test_grade_case_ignores_latency_type():
    out = grade_case([{"type": "latency", "threshold": 1000}], "anything")
    assert out["quality"] is None
