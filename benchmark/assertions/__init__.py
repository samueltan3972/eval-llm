"""Assertion evaluation: dispatch a case's asserts to graded 0-1 scores.

A case's quality is the mean of its gradeable assertion scores. Non-quality asserts (e.g.
`latency`, which the harness measures separately) are ignored here and excluded from the mean.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .deterministic import evaluate_deterministic
from .js_bridge import evaluate_js
from .judge import evaluate_rubric_ensemble

DETERMINISTIC_TYPES = {
    "equals", "contains", "icontains", "contains-all", "contains-any",
    "regex", "is-json", "contains-json",
}
# Measured by the harness, not a quality signal — skip in quality scoring.
IGNORED_TYPES = {"latency", "cost", "perplexity"}


@dataclass
class AssertionResult:
    type: str
    score: Optional[float]   # None => not counted toward quality
    passed: Optional[bool]
    reason: str
    judge_cost_usd: float = 0.0
    per_judge: list = None    # ensemble breakdown for llm-rubric
    disagreement: float = 0.0


def evaluate_assertion(assertion: dict, output: str, judges=None) -> AssertionResult:
    atype = assertion.get("type", "")

    if atype in IGNORED_TYPES:
        return AssertionResult(atype, None, None, "skipped (measured separately)")

    if atype in DETERMINISTIC_TYPES:
        r = evaluate_deterministic(assertion, output)
        return AssertionResult(atype, r["score"], r["pass"], r["reason"])

    if atype == "javascript":
        r = evaluate_js(assertion.get("value", ""), output)
        return AssertionResult(atype, r["score"], r["pass"], r["reason"])

    if atype == "llm-rubric":
        if not judges:
            return AssertionResult(atype, None, None, "no judge configured")
        r = evaluate_rubric_ensemble(assertion.get("value", ""), output, judges)
        return AssertionResult(atype, r["score"], r["pass"], r["reason"],
                               r.get("judge_cost_usd", 0.0), r.get("per_judge"), r.get("disagreement", 0.0))

    return AssertionResult(atype, None, None, f"unsupported assertion type {atype!r}")


def grade_case(asserts: list[dict], output: str, judges=None) -> dict:
    """Grade all asserts for one case. Returns quality in [0,1] (None if nothing gradeable),
    the per-assertion results, and total judge cost incurred. `judges` is the ensemble list."""
    results = [evaluate_assertion(a, output, judges) for a in asserts]
    scored = [r.score for r in results if r.score is not None]
    quality = sum(scored) / len(scored) if scored else None
    judge_cost = sum(r.judge_cost_usd for r in results)
    return {"quality": quality, "results": results, "judge_cost_usd": judge_cost}


__all__ = ["AssertionResult", "evaluate_assertion", "grade_case"]
