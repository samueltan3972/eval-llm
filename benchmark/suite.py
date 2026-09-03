"""Load and filter the test suite from datasets/*.yaml.

Reuses the existing promptfoo-style case schema verbatim:
  - description: str
    vars: { question: str }
    assert: [ {type, value, ...}, ... ]
    metadata: { domain: str, tier: core|extended }

Filtering mirrors promptfoo so existing tiers carry over (--filter domain=..., tier=...,
--sample N). A content hash of the selected cases is recorded with each run so two runs are
known to be comparable.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import random
from dataclasses import dataclass, field

import yaml


@dataclass
class TestCase:
    __test__ = False  # not a pytest test class despite the name

    description: str
    question: str
    asserts: list[dict]
    domain: str = "unknown"
    tier: str = "unknown"
    kind: str = "objective"   # "objective" (right/wrong) | "open_ended" (better/worse)

    @property
    def metadata(self) -> dict:
        return {"domain": self.domain, "tier": self.tier, "kind": self.kind}


def detect_kind(asserts: list[dict], explicit: str | None = None) -> str:
    """Open-ended if explicitly tagged, else if it has an llm-rubric (subjective) assertion.

    Open-ended cases have no single right answer — only better/worse — so they get pairwise
    judging in addition to rubric grading. Everything else is objective right/wrong.
    """
    if explicit in ("objective", "open_ended"):
        return explicit
    if any(a.get("type") == "llm-rubric" for a in asserts):
        return "open_ended"
    return "objective"


def load_suite(pattern: str = "datasets/*.yaml", root: str = ".") -> list[TestCase]:
    """Load all cases matched by `pattern` (sorted by file for stable ordering)."""
    cases: list[TestCase] = []
    for path in sorted(glob.glob(os.path.join(root, pattern))):
        with open(path, "r", encoding="utf-8") as f:
            doc = yaml.safe_load(f) or []
        if not isinstance(doc, list):
            raise ValueError(f"{path}: expected a YAML list of test cases")
        for item in doc:
            cases.append(_parse_case(item, path))
    return cases


def _parse_case(item: dict, path: str) -> TestCase:
    vars_ = item.get("vars") or {}
    question = vars_.get("question")
    if question is None:
        raise ValueError(f"{path}: a case is missing vars.question ({item.get('description')!r})")
    meta = item.get("metadata") or {}
    asserts = item.get("assert") or []
    return TestCase(
        description=item.get("description", "(no description)"),
        question=question,
        asserts=asserts,
        domain=meta.get("domain", "unknown"),
        tier=meta.get("tier", "unknown"),
        kind=detect_kind(asserts, meta.get("kind")),
    )


def filter_cases(
    cases: list[TestCase],
    filters: dict | None = None,
    sample: int | None = None,
    seed: int = 0,
) -> list[TestCase]:
    """Apply metadata equality filters then optionally take a random sample of N."""
    out = list(cases)
    for key, value in (filters or {}).items():
        out = [c for c in out if str(c.metadata.get(key)) == str(value)]
    if sample is not None and sample < len(out):
        out = random.Random(seed).sample(out, sample)
        # Keep deterministic display order after sampling.
        out.sort(key=lambda c: (c.domain, c.description))
    return out


def suite_hash(cases: list[TestCase]) -> str:
    """Stable content hash of the selected cases (question + asserts + metadata)."""
    payload = [
        {"q": c.question, "a": c.asserts, "m": c.metadata}
        for c in sorted(cases, key=lambda c: (c.domain, c.description))
    ]
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def domains(cases: list[TestCase]) -> list[str]:
    return sorted({c.domain for c in cases})
