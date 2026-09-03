"""Streamlit cross-run leaderboard — `python -m benchmark dashboard`.

A thin UI over `benchmark/leaderboard.py` (all aggregation logic lives there and is unit-tested
without a browser). Reads every `runs/*.json`, lets you pick runs/models, sort each dimension,
slide weights for a live composite re-rank, view trends over runs, and inspect a run's
head-to-head matrix. Launch via the `dashboard` subcommand; run directly with
`streamlit run benchmark/dashboard.py`.
"""

from __future__ import annotations

import os

import streamlit as st

try:  # normal package import (`python -m benchmark dashboard`)
    from .leaderboard import (
        aggregate,
        load_all_runs,
        pick_baseline,
        select_runs,
        sort_rows,
        trend_series,
        with_baseline,
        with_composite,
    )
except ImportError:  # `streamlit run benchmark/dashboard.py` executes this as a top-level script
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from benchmark.leaderboard import (
        aggregate,
        load_all_runs,
        pick_baseline,
        select_runs,
        sort_rows,
        trend_series,
        with_baseline,
        with_composite,
    )

RUNS_DIR = os.environ.get("BENCHMARK_RUNS_DIR", "runs")


def _label(run: dict) -> str:
    return f'{run.get("timestamp", "?")} · {run.get("label", "run")} · {run.get("n_cases", "?")} cases'


def main() -> None:
    st.set_page_config(page_title="LLM benchmark leaderboard", layout="wide")
    st.title("LLM benchmark — cross-run leaderboard")

    runs = load_all_runs(RUNS_DIR)
    if not runs:
        st.warning(f"No runs found in `{RUNS_DIR}/`. Run `python -m benchmark run` first.")
        return

    # --- run + model selection ----------------------------------------------------------
    with st.sidebar:
        st.header("Selection")
        labels = {_label(r): r.get("timestamp") for r in runs}
        chosen = st.multiselect("Runs (default: all)", list(labels), default=list(labels))
        sel = select_runs(runs, [labels[c] for c in chosen]) or runs

        all_models = sorted({m["name"] for r in sel for m in r.get("models", [])})
        models = st.multiselect("Models (default: all)", all_models, default=all_models)

        st.header("Composite weights")
        wq = st.slider("Quality", 0.0, 1.0, 1.0, 0.05)
        ws = st.slider("Speed", 0.0, 1.0, 1.0, 0.05)
        wc = st.slider("Cost", 0.0, 1.0, 1.0, 0.05)

    weights = {"quality": wq, "speed": ws, "cost": wc}
    rows = aggregate(sel, models or None)
    if not rows:
        st.info("No models in the selected runs.")
        return
    baseline = pick_baseline(sel, rows)
    rows = with_composite(with_baseline(rows, baseline), weights)
    st.caption(f"Pooled {len(sel)} run(s) · baseline: {baseline or 'auto (cheapest/fastest)'}")

    # --- leaderboards -------------------------------------------------------------------
    sort_key = st.radio(
        "Sort by", ["composite", "quality", "avg_tokens_per_sec", "avg_cost_usd", "win_rate"],
        horizontal=True, format_func=lambda k: {
            "composite": "Composite", "quality": "Quality",
            "avg_tokens_per_sec": "Speed (tok/s)", "avg_cost_usd": "Cost ($/call)",
            "win_rate": "Win-rate"}[k],
    )
    ordered = sort_rows(rows, sort_key, descending=(sort_key != "avg_cost_usd"))
    table = [{
        "Model": r["display"], "Composite": r.get("composite"), "Quality": r.get("quality"),
        "tok/s": r.get("avg_tokens_per_sec"), "speed vs base": r.get("speed_vs_baseline"),
        "$/call": (None if r.get("avg_cost_usd") is None
                   else ("~" if r.get("cost_estimated") else "") + f'{r["avg_cost_usd"]:.5f}'),
        "cost vs base": r.get("cost_vs_baseline"), "win-rate": r.get("win_rate"),
        "runs": r.get("n_runs"),
    } for r in ordered]
    st.subheader("Leaderboard")
    st.dataframe(table, use_container_width=True, hide_index=True)

    # --- trends -------------------------------------------------------------------------
    st.subheader("Trends over runs")
    metric = st.selectbox("Metric", ["quality", "overall", "avg_tokens_per_sec", "avg_cost_usd"],
                          format_func=lambda k: {"quality": "Quality", "overall": "Overall",
                          "avg_tokens_per_sec": "Speed (tok/s)", "avg_cost_usd": "Cost ($/call)"}[k])
    series = trend_series(sel, metric)
    if series:
        ts = sorted({t for pts in series.values() for t, _ in pts})
        chart = {t: {} for t in ts}
        names = {m["name"]: (m.get("display") or m["name"]) for r in sel for m in r.get("models", [])}
        for name in (models or series):
            for t, v in series.get(name, []):
                chart[t][names.get(name, name)] = v
        st.line_chart([{"run": t, **vals} for t, vals in chart.items()], x="run")

    # --- per-run head-to-head -----------------------------------------------------------
    st.subheader("Per-run head-to-head")
    h2h_runs = [r for r in sel if (r.get("head_to_head") or {}).get("models")]
    if not h2h_runs:
        st.caption("No open-ended (pairwise-judged) cases in the selected runs.")
        return
    pick = st.selectbox("Run", h2h_runs, format_func=_label)
    h2h = pick["head_to_head"]
    names = {m["name"]: (m.get("display") or m["name"]) for m in pick.get("models", [])}
    mods = h2h["models"]
    grid = [{"row \\ vs": names.get(a, a),
             **{names.get(b, b): (None if a == b else h2h["matrix"].get(a, {}).get(b)) for b in mods}}
            for a in mods]
    st.dataframe(grid, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
