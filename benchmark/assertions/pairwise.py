"""Pairwise judging for open-ended cases — "better/worse", not "right/wrong".

For a given prompt, two models' answers are shown to the judge ensemble blind (just "Answer 1"
and "Answer 2", never which model). To cancel position bias, the presentation order is swapped
across the ensemble (odd-indexed judges see the answers flipped). Each judge votes 1/2/tie; the
ensemble result is the **average win-weight** for answer A in [0, 1] (0.5 = tie). This is a
per-run signal (relative to the models present), surfaced as a head-to-head / win-rate.
"""

from __future__ import annotations

from .deterministic import extract_json

PAIRWISE_PROMPT = """\
You are comparing two AI responses to the same task. Pick the better one on the rubric below
(or "tie" if genuinely equal). You are not told which system produced which; judge only the text.

TASK:
{question}

RUBRIC (what "better" means):
{rubric}

ANSWER 1 (delimited by <<< >>>):
<<<
{a}
>>>

ANSWER 2 (delimited by <<< >>>):
<<<
{b}
>>>

Reply with ONLY a JSON object, no prose, no fences:
{{"winner": "1" | "2" | "tie", "reason": "<one short sentence>"}}
"""


def _parse_winner(text: str) -> str:
    try:
        data = extract_json(text)
        w = str(data.get("winner", "")).strip().lower()
    except (ValueError, AttributeError):
        low = text.lower()
        w = "1" if "answer 1" in low else ("2" if "answer 2" in low else "tie")
    if w in ("1", "2", "tie"):
        return w
    return "tie"


def _a_weight(winner: str, swapped: bool) -> float:
    """A's win-weight given the judge's verdict and whether A was shown as 'Answer 2'."""
    if winner == "tie":
        return 0.5
    a_label = "2" if swapped else "1"
    return 1.0 if winner == a_label else 0.0


def battle(question: str, rubric: str, answer_a: str, answer_b: str, judges: list) -> dict:
    """Run one A-vs-B battle across the judge ensemble. Returns A's average win-weight."""
    if not judges:
        return {"a_win": 0.5, "b_win": 0.5, "per_judge": [], "judge_cost_usd": 0.0}
    weights, per_judge, cost = [], [], 0.0
    for i, jp in enumerate(judges):
        swapped = i % 2 == 1                       # half the ensemble sees the flipped order
        first, second = (answer_b, answer_a) if swapped else (answer_a, answer_b)
        prompt = PAIRWISE_PROMPT.format(question=question, rubric=rubric or "overall quality",
                                        a=first, b=second)
        res = jp.generate(prompt)
        cost += res.cost_usd
        if not res.ok:
            continue
        winner = _parse_winner(res.text)
        aw = _a_weight(winner, swapped)
        weights.append(aw)
        per_judge.append({"judge": getattr(jp, "name", "judge"), "a_win": aw, "swapped": swapped})
    a_win = sum(weights) / len(weights) if weights else 0.5
    return {"a_win": round(a_win, 3), "b_win": round(1.0 - a_win, 3),
            "per_judge": per_judge, "judge_cost_usd": cost}


def rubric_of(case_asserts: list[dict]) -> str:
    """Use the case's llm-rubric text (if any) as the 'better' criterion for pairwise."""
    for a in case_asserts:
        if a.get("type") == "llm-rubric" and a.get("value"):
            return str(a["value"])
    return "overall quality, correctness, and helpfulness"
