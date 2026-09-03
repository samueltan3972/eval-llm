"""Saving runs, a response/judge cache, the leaderboard index, and run-to-run compare.

Each run is a timestamped JSON file under runs/ holding per-case raw results, per-model
aggregates + scores, the suite hash (so two runs are known comparable), and the resolved
config. A small on-disk cache makes unchanged re-runs near-instant.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone


# --- run files --------------------------------------------------------------------------
# Each run lives in its own timestamped folder `runs/<ts>__<label>/` holding `run.json` and
# `report.html`; the cross-run `leaderboard.md` sits at the `runs/` root.
RUN_JSON = "run.json"
RUN_HTML = "report.html"


def run_dir(run: dict, runs_dir: str = "runs") -> str:
    """The per-run folder `runs/<ts>__<label>/` for this run (created on demand by callers)."""
    ts = run.get("timestamp") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = run.get("label", "run")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)
    return os.path.join(runs_dir, f"{ts}__{safe}")


def save_run(run: dict, runs_dir: str = "runs") -> str:
    d = run_dir(run, runs_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, RUN_JSON)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(run, f, indent=2, ensure_ascii=False)
    return path


def save_html_report(run: dict, runs_dir: str = "runs") -> str:
    """Write a self-contained HTML report next to the run JSON, in the same per-run folder."""
    from .report_html import render_report

    d = run_dir(run, runs_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, RUN_HTML)
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_report(run))
    return path


def load_run(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_runs(runs_dir: str = "runs") -> list[str]:
    """Paths to every run's `run.json`, oldest-first (folders sort by their `<ts>__` prefix)."""
    if not os.path.isdir(runs_dir):
        return []
    files = [
        os.path.join(runs_dir, d, RUN_JSON)
        for d in os.listdir(runs_dir)
        if "__" in d and os.path.isfile(os.path.join(runs_dir, d, RUN_JSON))
    ]
    return sorted(files)


def latest_run(runs_dir: str = "runs") -> dict | None:
    runs = list_runs(runs_dir)
    return load_run(runs[-1]) if runs else None


# --- response / judge cache -------------------------------------------------------------
class Cache:
    """Tiny JSON file cache. Keys are hashed; values are arbitrary JSON-able dicts."""

    def __init__(self, cache_dir: str = ".cache", enabled: bool = True):
        self.dir = cache_dir
        self.enabled = enabled
        if enabled:
            os.makedirs(cache_dir, exist_ok=True)

    @staticmethod
    def make_key(*parts) -> str:
        blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def _path(self, key: str) -> str:
        return os.path.join(self.dir, key + ".json")

    def get(self, key: str):
        if not self.enabled:
            return None
        path = self._path(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, key: str, value: dict) -> None:
        if not self.enabled:
            return
        try:
            with open(self._path(key), "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False)
        except OSError:
            pass


# --- leaderboard index ------------------------------------------------------------------
def write_leaderboard(run: dict, runs_dir: str = "runs") -> str:
    """Write `leaderboard.md` *inside this run's folder*: the run's own table + an index of all
    runs. Kept per-run (not a single root file) so each folder is a complete, immutable snapshot
    and nothing is overwritten between runs."""
    from .report import leaderboard_markdown

    d = run_dir(run, runs_dir)
    os.makedirs(d, exist_ok=True)
    md = [leaderboard_markdown(run), "", "## Run history", ""]
    for path in reversed(list_runs(runs_dir)):
        try:
            r = load_run(path)
        except (OSError, json.JSONDecodeError):
            continue
        models = ", ".join(m["name"] for m in r.get("models", []))
        folder = os.path.basename(os.path.dirname(path))   # the <ts>__<label> run folder
        md.append(f"- `{folder}/` — {r.get('n_cases', '?')} cases · {models}")
    out_path = os.path.join(d, "leaderboard.md")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    return out_path


# --- compare ----------------------------------------------------------------------------
def compare_runs(run_a: dict, run_b: dict) -> dict:
    """Per-model score deltas from run_a -> run_b. Warns if suites differ."""
    a = {m["name"]: m for m in run_a.get("models", [])}
    b = {m["name"]: m for m in run_b.get("models", [])}
    metrics = ["overall", "quality", "speed", "cost", "avg_latency_ms", "avg_tokens_per_sec", "avg_cost_usd"]
    rows = []
    for name in sorted(set(a) | set(b)):
        ma, mb = a.get(name), b.get(name)
        display = (mb or {}).get("display") or (ma or {}).get("display") or name
        row = {"name": name, "display": display, "in_a": ma is not None, "in_b": mb is not None, "deltas": {}}
        if ma and mb:
            for k in metrics:
                row["deltas"][k] = round(mb.get(k, 0) - ma.get(k, 0), 4)
        rows.append(row)
    return {
        "suite_match": run_a.get("suite_hash") == run_b.get("suite_hash"),
        "a": {"timestamp": run_a.get("timestamp"), "label": run_a.get("label")},
        "b": {"timestamp": run_b.get("timestamp"), "label": run_b.get("label")},
        "rows": rows,
    }
