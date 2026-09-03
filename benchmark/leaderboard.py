"""Cross-run leaderboard aggregation — the relative, comparative view across `runs/*.json`.

Where the per-run report is a point-in-time snapshot, this pools many runs and ranks models with
a *relative* methodology: speed/cost are expressed both raw and **baseline-relative** (vs a
configurable model, falling back to the fastest/cheapest in the pool), and a **weighted composite**
normalizes each dimension to 0-100 within the selected pool so interactive weight sliders are
meaningful. All logic lives here (pure, unit-testable); `dashboard.py` is a thin Streamlit shell.

Identity rule (same as the rest of the harness): aggregate and key everything on a model's `name`;
`display` is for showing only.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean

from .persistence import list_runs, load_run

# Per-model metrics we pool across runs. (key, higher_is_better) — direction drives normalization.
DIMENSIONS = {
    "quality": True,            # 0-100 ensemble-rubric mean
    "avg_tokens_per_sec": True,  # throughput, raw
    "avg_cost_usd": False,       # $/call, raw (cheaper is better)
}


def load_all_runs(runs_dir: str = "runs") -> list[dict]:
    """Every saved run, oldest-first (list_runs is already sorted by timestamp)."""
    out = []
    for path in list_runs(runs_dir):
        try:
            out.append(load_run(path))
        except (OSError, ValueError):
            continue
    return out


def select_runs(runs: list[dict], timestamps: list[str] | None = None) -> list[dict]:
    """Filter to the chosen run timestamps (None/empty = all), preserving order."""
    if not timestamps:
        return list(runs)
    wanted = set(timestamps)
    return [r for r in runs if r.get("timestamp") in wanted]


def aggregate(runs: list[dict], only_models: list[str] | None = None) -> list[dict]:
    """Pool the given runs into one row per model — each metric is the MEAN across the runs the
    model appears in. Carries raw aggregates + the per-run 0-100 quality/speed/cost scores so the
    dashboard can sort by either. Baseline-relative columns are added by `with_baseline`."""
    want = set(only_models) if only_models else None
    acc: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    display: dict[str, str] = {}
    last_seen: dict[str, str] = {}

    for run in runs:
        ts = run.get("timestamp", "")
        for m in run.get("models", []):
            name = m.get("name")
            if name is None or (want is not None and name not in want):
                continue
            display[name] = m.get("display") or name
            last_seen[name] = max(last_seen.get(name, ""), ts)
            for k in ("quality", "speed", "cost", "overall", "avg_tokens_per_sec",
                      "avg_cost_usd", "avg_latency_ms", "win_rate"):
                v = m.get(k)
                if v is not None:
                    acc[name][k].append(v)
            acc[name]["__cost_estimated"].append(bool(m.get("cost_estimated")))

    rows = []
    for name, metrics in acc.items():
        row = {"name": name, "display": display.get(name, name),
               "n_runs": len(metrics.get("quality", [])) or len(metrics.get("avg_cost_usd", [])),
               "last_seen": last_seen.get(name, ""),
               "cost_estimated": any(metrics.get("__cost_estimated", []))}
        for k in ("quality", "speed", "cost", "overall", "avg_tokens_per_sec",
                  "avg_cost_usd", "avg_latency_ms", "win_rate"):
            vals = metrics.get(k)
            row[k] = round(mean(vals), 4) if vals else None
        rows.append(row)
    rows.sort(key=lambda r: (r["quality"] is None, -(r["quality"] or 0)))
    return rows


def pick_baseline(runs: list[dict], rows: list[dict]) -> str | None:
    """The configured baseline model if it's in the pool, else None (callers fall back per-dim)."""
    names = {r["name"] for r in rows}
    for run in reversed(runs):                       # most recent run's baseline wins
        b = run.get("baseline")
        if b and b in names:
            return b
    return None


def with_baseline(rows: list[dict], baseline: str | None) -> list[dict]:
    """Add `cost_vs_baseline` (model$/baseline$) and `speed_vs_baseline` (model_tps/baseline_tps)
    to each row. With no usable baseline, fall back to the cheapest (cost) / fastest (speed) in the
    pool — mirroring the per-run report's vs-cheapest / vs-fastest references. Ratios: 1.0 = same,
    cost <1 cheaper, speed >1 faster."""
    by_name = {r["name"]: r for r in rows}
    base = by_name.get(baseline) if baseline else None

    costs = [r["avg_cost_usd"] for r in rows if r.get("avg_cost_usd")]
    tps = [r["avg_tokens_per_sec"] for r in rows if r.get("avg_tokens_per_sec")]
    base_cost = (base or {}).get("avg_cost_usd") or (min(costs) if costs else None)
    base_tps = (base or {}).get("avg_tokens_per_sec") or (max(tps) if tps else None)

    for r in rows:
        c, t = r.get("avg_cost_usd"), r.get("avg_tokens_per_sec")
        r["cost_vs_baseline"] = round(c / base_cost, 3) if (c and base_cost) else None
        r["speed_vs_baseline"] = round(t / base_tps, 3) if (t and base_tps) else None
    return rows


def _normalize(values: list[float], higher_is_better: bool) -> list[float]:
    """Min-max each value to 0-100 within the pool (all-equal -> all 100, so it never zeroes a
    dimension)."""
    present = [v for v in values if v is not None]
    if not present:
        return [0.0 for _ in values]
    lo, hi = min(present), max(present)
    out = []
    for v in values:
        if v is None:
            out.append(0.0)
        elif hi == lo:
            out.append(100.0)
        else:
            frac = (v - lo) / (hi - lo)
            out.append(round(100.0 * (frac if higher_is_better else 1.0 - frac), 2))
    return out


def with_composite(rows: list[dict], weights: dict[str, float]) -> list[dict]:
    """Normalize each dimension to 0-100 within the pool, then set `composite` = weighted mean.

    `weights` keys: quality, speed, cost (missing -> 0). Speed uses raw tok/s, cost uses raw $/call
    (inverted), so weights act on comparable 0-100 axes. Mutates rows in place and returns them."""
    norms = {
        "quality": _normalize([r.get("quality") for r in rows], True),
        "speed": _normalize([r.get("avg_tokens_per_sec") for r in rows], True),
        "cost": _normalize([r.get("avg_cost_usd") for r in rows], False),
    }
    w = {k: float(weights.get(k, 0.0)) for k in ("quality", "speed", "cost")}
    wsum = sum(w.values()) or 1.0
    for i, r in enumerate(rows):
        r["norm_quality"] = norms["quality"][i]
        r["norm_speed"] = norms["speed"][i]
        r["norm_cost"] = norms["cost"][i]
        r["composite"] = round(
            (w["quality"] * r["norm_quality"] + w["speed"] * r["norm_speed"]
             + w["cost"] * r["norm_cost"]) / wsum, 2
        )
    return rows


def sort_rows(rows: list[dict], key: str, descending: bool = True) -> list[dict]:
    """Stable sort by a metric; None sinks to the bottom regardless of direction."""
    def sk(r):
        v = r.get(key)
        return (v is None, -(v if descending else -v) if v is not None else 0)
    return sorted(rows, key=sk)


def trend_series(runs: list[dict], metric: str) -> dict[str, list[tuple[str, float]]]:
    """Per-model time series of a metric across runs (oldest-first) for trend charts."""
    series: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for run in runs:
        ts = run.get("timestamp", "")
        for m in run.get("models", []):
            v = m.get(metric)
            if v is not None:
                series[m["name"]].append((ts, v))
    return dict(series)


def build_leaderboard(runs: list[dict], only_models: list[str] | None = None,
                      weights: dict[str, float] | None = None) -> list[dict]:
    """Convenience: aggregate -> baseline-relative -> composite, in one call."""
    rows = aggregate(runs, only_models)
    rows = with_baseline(rows, pick_baseline(runs, rows))
    rows = with_composite(rows, weights or {"quality": 1, "speed": 1, "cost": 1})
    return rows
