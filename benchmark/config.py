"""Load benchmark.yaml and the .env file, applying defaults.

The config declares the models to benchmark, the judge used for llm-rubric grading, the
scoring anchors/weights, and where the suite lives. API keys are read from the environment
(.env is loaded automatically, mirroring the promptfoo workflow).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import yaml

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    load_dotenv = None

DEFAULT_CONFIG_PATH = "benchmark.yaml"


@dataclass
class Config:
    models: list[dict]
    judges: list[dict]            # ensemble of LLM judges (>=1); de-biases quality grading
    scoring: dict
    baseline: str | None = None   # model `name` used as the speed/cost reference
    suite_glob: str = "datasets/*.yaml"
    concurrency: int = 4
    runs_dir: str = "runs"
    cache_dir: str = ".cache"
    label: str = "run"
    raw: dict = field(default_factory=dict)

    @property
    def judge(self) -> dict | None:
        """Back-compat: the first judge (older code referenced a single `judge`)."""
        return self.judges[0] if self.judges else None


def load_env(root: str = ".") -> None:
    if load_dotenv is not None:
        load_dotenv(os.path.join(root, ".env"))


def _judges(doc: dict) -> list[dict]:
    """Accept either `judges:` (a list, the ensemble) or a single `judge:` (back-compat)."""
    judges = doc.get("judges")
    if judges:
        if not isinstance(judges, list):
            raise ValueError("`judges` must be a list of judge configs")
        return judges
    single = doc.get("judge")
    return [single] if single else []


def load_config(path: str = DEFAULT_CONFIG_PATH, root: str = ".") -> Config:
    load_env(root)
    full = os.path.join(root, path)
    if not os.path.exists(full):
        raise FileNotFoundError(f"config not found: {full}")
    with open(full, "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}

    models = doc.get("models") or []
    if not models:
        raise ValueError("config has no `models`")
    for m in models:
        if not m.get("name"):
            raise ValueError(f"every model needs a `name`: {m}")

    judges = _judges(doc)

    return Config(
        models=models,
        judges=judges,
        baseline=doc.get("baseline"),
        scoring=doc.get("scoring") or {},
        suite_glob=doc.get("suite", {}).get("glob", "datasets/*.yaml") if isinstance(doc.get("suite"), dict) else doc.get("suite", "datasets/*.yaml"),
        concurrency=int(doc.get("concurrency", 4)),
        runs_dir=doc.get("runs_dir", "runs"),
        cache_dir=doc.get("cache_dir", ".cache"),
        label=doc.get("label", "run"),
        raw=doc,
    )
