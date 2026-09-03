from benchmark.leaderboard import (
    aggregate,
    pick_baseline,
    sort_rows,
    trend_series,
    with_baseline,
    with_composite,
)


def _run(ts, baseline=None, **models):
    """A minimal run dict: models={name: (quality, tps, cost)}."""
    return {
        "timestamp": ts,
        "label": "t",
        "baseline": baseline,
        "models": [
            {"name": n, "display": f"{n} (tool)", "quality": q, "speed": q, "cost": 50,
             "overall": q, "avg_tokens_per_sec": tps, "avg_cost_usd": cost,
             "avg_latency_ms": 100, "win_rate": None}
            for n, (q, tps, cost) in models.items()
        ],
    }


def test_aggregate_means_metrics_across_runs():
    runs = [
        _run("20260101T000000Z", a=(80, 100, 0.01), b=(60, 50, 0.005)),
        _run("20260102T000000Z", a=(90, 120, 0.02), b=(70, 60, 0.007)),
    ]
    rows = {r["name"]: r for r in aggregate(runs)}
    assert rows["a"]["quality"] == 85          # mean of 80, 90
    assert rows["a"]["avg_tokens_per_sec"] == 110
    assert rows["a"]["n_runs"] == 2
    assert rows["a"]["display"] == "a (tool)"   # display carried, name is the key
    # default sort is by quality desc
    assert [r["name"] for r in aggregate(runs)] == ["a", "b"]


def test_only_models_filter():
    runs = [_run("t1", a=(80, 100, 0.01), b=(60, 50, 0.005))]
    rows = aggregate(runs, only_models=["b"])
    assert [r["name"] for r in rows] == ["b"]


def test_baseline_relative_uses_configured_baseline():
    runs = [_run("t1", baseline="b", a=(80, 100, 0.02), b=(60, 50, 0.01))]
    rows = with_baseline(aggregate(runs), pick_baseline(runs, aggregate(runs)))
    by = {r["name"]: r for r in rows}
    # a is 2x b's cost and 2x b's throughput, relative to baseline b
    assert by["a"]["cost_vs_baseline"] == 2.0
    assert by["a"]["speed_vs_baseline"] == 2.0
    assert by["b"]["cost_vs_baseline"] == 1.0
    assert by["b"]["speed_vs_baseline"] == 1.0


def test_baseline_falls_back_to_cheapest_and_fastest():
    runs = [_run("t1", a=(80, 100, 0.02), b=(60, 50, 0.01))]   # no baseline configured
    rows = aggregate(runs)
    assert pick_baseline(runs, rows) is None
    rows = with_baseline(rows, None)
    by = {r["name"]: r for r in rows}
    # cost vs cheapest (b=0.01): a=2.0, b=1.0 ; speed vs fastest (a=100): a=1.0, b=0.5
    assert by["a"]["cost_vs_baseline"] == 2.0 and by["b"]["cost_vs_baseline"] == 1.0
    assert by["a"]["speed_vs_baseline"] == 1.0 and by["b"]["speed_vs_baseline"] == 0.5


def test_composite_weights_change_ranking():
    runs = [_run("t1", fast=(50, 200, 0.5), good=(95, 20, 0.5))]
    rows = aggregate(runs)
    # quality-only weighting -> 'good' wins
    q = sort_rows(with_composite([dict(r) for r in rows], {"quality": 1, "speed": 0, "cost": 0}),
                  "composite")
    assert q[0]["name"] == "good"
    # speed-only weighting -> 'fast' wins
    s = sort_rows(with_composite([dict(r) for r in rows], {"quality": 0, "speed": 1, "cost": 0}),
                  "composite")
    assert s[0]["name"] == "fast"


def test_composite_normalizes_equal_dimension_to_full():
    runs = [_run("t1", a=(80, 100, 0.01), b=(80, 100, 0.01))]   # identical
    rows = with_composite(aggregate(runs), {"quality": 1, "speed": 1, "cost": 1})
    for r in rows:
        assert r["norm_quality"] == 100 and r["norm_speed"] == 100 and r["norm_cost"] == 100
        assert r["composite"] == 100


def test_trend_series_is_time_ordered():
    runs = [
        _run("20260101T000000Z", a=(80, 100, 0.01)),
        _run("20260102T000000Z", a=(90, 100, 0.01)),
    ]
    series = trend_series(runs, "quality")
    assert series["a"] == [("20260101T000000Z", 80), ("20260102T000000Z", 90)]


def test_sort_rows_sinks_none():
    rows = [{"name": "a", "win_rate": 0.7}, {"name": "b", "win_rate": None}]
    assert [r["name"] for r in sort_rows(rows, "win_rate")] == ["a", "b"]
