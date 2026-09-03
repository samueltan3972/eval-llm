"""`llm-rubric` assertion — a blind LLM-as-judge.

The judge sees only the model's output and the rubric, never which model produced it, so it
can't be biased by provider identity. It returns a 0-1 quality score. Judge calls are pure
functions of (judge identity, output, rubric), so callers cache them to avoid re-grading.
"""

from __future__ import annotations

import json
import re

from .deterministic import extract_json

JUDGE_PROMPT = """\
You are a strict grader. Grade the RESPONSE below against the RUBRIC.
Judge only by the rubric. Do not reward verbosity. You are not told which system produced
the response and must not assume one.

RUBRIC:
{rubric}

RESPONSE (delimited by <<< >>>):
<<<
{output}
>>>

Reply with ONLY a JSON object, no prose, no code fences:
{{"score": <number 0.0-1.0>, "pass": <true|false>, "reason": "<one short sentence>"}}
"""


def build_judge_prompt(rubric: str, output: str) -> str:
    return JUDGE_PROMPT.format(rubric=rubric, output=output)


def parse_judge_reply(text: str) -> dict:
    """Parse the judge's JSON reply, clamping score to [0,1]; tolerant of stray prose."""
    try:
        data = extract_json(text)
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except ValueError:
        # Last resort: pull the first number in [0,1] out of the text.
        m = re.search(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", text)
        score = float(m.group(1)) if m else 0.0
        return {"pass": score >= 0.5, "score": score, "reason": "parsed from non-JSON reply"}
    score = float(data.get("score", 0.0))
    score = max(0.0, min(1.0, score))
    passed = bool(data.get("pass", score >= 0.5))
    return {"pass": passed, "score": score, "reason": str(data.get("reason", ""))[:200]}


def evaluate_rubric(rubric: str, output: str, judge_provider) -> dict:
    """Grade `output` against `rubric` using `judge_provider` (any Provider)."""
    if not output.strip():
        return {"pass": False, "score": 0.0, "reason": "empty response"}
    prompt = build_judge_prompt(rubric, output)
    res = judge_provider.generate(prompt)
    if not res.ok:
        return {"pass": False, "score": 0.0, "reason": f"judge error: {res.error}"}
    out = parse_judge_reply(res.text)
    out["judge_cost_usd"] = res.cost_usd
    return out


def evaluate_rubric_ensemble(rubric: str, output: str, judges: list) -> dict:
    """Grade with every judge and combine by MEAN to de-bias a single grader.

    Returns the mean score, per-judge breakdown, total judge cost, and a `disagreement`
    (max-min spread of judge scores) as a confidence signal.
    """
    if not judges:
        return {"pass": None, "score": None, "reason": "no judges configured",
                "judge_cost_usd": 0.0, "per_judge": [], "disagreement": 0.0}
    per_judge = []
    cost = 0.0
    for jp in judges:
        r = evaluate_rubric(rubric, output, jp)
        per_judge.append({"judge": getattr(jp, "name", "judge"), "score": r["score"],
                          "reason": r.get("reason", "")})
        cost += r.get("judge_cost_usd", 0.0)
    scores = [p["score"] for p in per_judge if p["score"] is not None]
    if not scores:
        return {"pass": False, "score": 0.0, "reason": "all judges errored",
                "judge_cost_usd": cost, "per_judge": per_judge, "disagreement": 0.0}
    mean = sum(scores) / len(scores)
    return {
        "pass": mean >= 0.5,
        "score": mean,
        "reason": f"ensemble mean of {len(scores)} judge(s)",
        "judge_cost_usd": cost,
        "per_judge": per_judge,
        "disagreement": round(max(scores) - min(scores), 3),
    }
