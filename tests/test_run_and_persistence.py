import json

from benchmark.config import Config
from benchmark.persistence import Cache, compare_runs, load_run, save_run, write_leaderboard
from benchmark.runner import run_benchmark
from benchmark.suite import TestCase


def _config():
    return Config(
        models=[
            {"name": "fast", "model": "Fast-1", "tool": "toolx", "type": "mock",
             "pricing": {"input": 1, "output": 1}, "sim_latency_ms": 200, "sim_out_tokens": 60},
            {"name": "slow", "model": "Slow-1", "tool": "toolx", "type": "mock",
             "pricing": {"input": 5, "output": 20}, "sim_latency_ms": 1500, "sim_out_tokens": 60},
        ],
        judges=[{"name": "j", "type": "mock"}],
        scoring={},
    )


def _cases():
    return [
        TestCase("num", "Reply with only the number: 2+2", [{"type": "regex", "value": r"\d+"}], "math", "core"),
        TestCase("ack", "Say ACKNOWLEDGED", [{"type": "contains", "value": "ACK"}], "instruction_following", "core"),
        TestCase("judged", "Be nice", [{"type": "llm-rubric", "value": "polite"}], "creative_writing", "core", "open_ended"),
    ]


def test_run_benchmark_dry_run_shape(tmp_path):
    cfg = _config()
    cfg.runs_dir = str(tmp_path / "runs")
    cfg.cache_dir = str(tmp_path / "cache")
    run = run_benchmark(cfg, _cases(), dry_run=True)

    assert run["n_cases"] == 3
    assert len(run["models"]) == 2
    for m in run["models"]:
        for k in ("overall", "quality", "speed", "cost", "avg_latency_ms", "avg_tokens_per_sec"):
            assert k in m
    # fast model should out-score slow on speed and cost
    by = {m["name"]: m for m in run["models"]}
    assert by["fast"]["speed"] >= by["slow"]["speed"]
    assert by["fast"]["cost"] >= by["slow"]["cost"]
    # details include per-assertion grading
    assert any(d["assertions"] for d in run["details"])
    # display label flows through while name stays the key
    assert by["fast"]["display"] == "Fast-1 (toolx)"
    assert by["fast"]["name"] == "fast"
    assert all(d.get("model_display") for d in run["details"])


def test_save_load_and_leaderboard(tmp_path):
    cfg = _config()
    runs_dir = str(tmp_path / "runs")
    run = run_benchmark(cfg, _cases(), dry_run=True)
    path = save_run(run, runs_dir)
    assert load_run(path)["n_cases"] == 3
    lb = write_leaderboard(run, runs_dir)
    text = open(lb).read()
    assert "Overall" in text and "fast" in text


def test_compare_runs_deltas():
    cfg = _config()
    a = run_benchmark(cfg, _cases(), dry_run=True)
    b = run_benchmark(cfg, _cases(), dry_run=True)
    cmp = compare_runs(a, b)
    assert cmp["suite_match"] is True
    assert any(r["name"] == "fast" for r in cmp["rows"])


def test_compare_matches_by_name_even_if_display_changes():
    # Same identity (name) but a relabeled `model` must still line up in compare.
    a = run_benchmark(_config(), _cases(), dry_run=True)
    b = run_benchmark(_config(), _cases(), dry_run=True)
    for m in b["models"]:
        m["display"] = m["display"] + " v2"   # simulate a renamed label in a later run
    cmp = compare_runs(a, b)
    fast = next(r for r in cmp["rows"] if r["name"] == "fast")
    assert fast["in_a"] and fast["in_b"]      # matched despite the display change
    assert "deltas" in fast and "overall" in fast["deltas"]


def test_html_report_shows_display_text_and_name_anchor():
    from benchmark.report_html import render_report
    run = run_benchmark(_config(), _cases(), dry_run=True)
    html = render_report(run)
    assert "Fast-1 (toolx)" in html      # visible label
    assert 'id="m-fast"' in html          # anchor still keyed on name
    assert 'href="#m-fast"' in html


def test_cost_vs_cheapest_is_relative_to_min():
    run = run_benchmark(_config(), _cases(), dry_run=True)
    by = {m["name"]: m for m in run["models"]}
    # both models priced; cheapest must be 1.0x, the pricier strictly above
    mults = {n: m["cost_vs_cheapest"] for n, m in by.items()}
    assert min(mults.values()) == 1.0
    assert max(mults.values()) > 1.0


def test_html_has_interactive_baseline_selector():
    from benchmark.report_html import render_report
    run = run_benchmark(_config(), _cases(), dry_run=True)
    html = render_report(run)
    assert 'id="baseline-select"' in html        # the dropdown
    assert 'class="num mult"' in html             # multiplier cells
    assert "data-cost=" in html                   # per-cell cost for JS recompute
    assert "function rebaseMult()" in html        # recompute logic, self-contained
    assert "http://" not in html and "https://" not in html


def test_html_shows_head_to_head_and_speed_vs_fastest():
    # The judged (open-ended) case drives a head-to-head between the two models.
    from benchmark.report_html import render_report
    run = run_benchmark(_config(), _cases(), dry_run=True)
    assert run["head_to_head"]["models"]            # open-ended case present
    html = render_report(run)
    assert "Head-to-head" in html                    # the section renders
    assert "Win matrix" in html
    assert "vs fastest" in html                       # the new leaderboard column header
    # per-judge ensemble breakdown shows for the llm-rubric case
    assert 'class="ensemble"' in html


def test_html_omits_head_to_head_for_objective_only_run():
    from benchmark.report_html import render_report
    objective = [
        TestCase("num", "Reply with only the number: 2+2", [{"type": "regex", "value": r"\d+"}], "math", "core"),
    ]
    run = run_benchmark(_config(), objective, dry_run=True)
    assert run["head_to_head"]["models"] == []        # no open-ended cases
    html = render_report(run)
    assert "Head-to-head" not in html
    assert "http://" not in html and "https://" not in html


def test_html_report_is_self_contained(tmp_path):
    from benchmark.persistence import save_html_report
    from benchmark.report_html import render_report

    run = run_benchmark(_config(), _cases(), dry_run=True)
    html = render_report(run)
    # all the major sections are present
    for needle in ("<svg", 'class="lb"', "heatmap", "model-block", "Per-case drill-in"):
        assert needle in html
    # self-contained: no external resource references
    assert "http://" not in html and "https://" not in html
    # writes a file next to the run
    path = save_html_report(run, str(tmp_path))
    assert path.endswith(".html")
    assert "<!doctype html>" in open(path, encoding="utf-8").read().lower()


def test_html_report_escapes_output(tmp_path):
    # A model output with HTML must be escaped, not injected into the page.
    from benchmark.report_html import render_report

    run = {
        "n_cases": 1, "suite_hash": "abc", "models": [{"name": "m", "overall": 50,
        "quality": 50, "speed": 50, "cost": 50, "quality_by_domain": {"x": 50}}],
        "details": [{"model": "m", "domain": "x", "description": "<script>alert(1)</script>",
                     "quality": 0.5, "latency_ms": 1, "output_tokens": 1, "cost_usd": 0.0,
                     "output": "<img src=x onerror=alert(1)>", "assertions": []}],
    }
    html = render_report(run)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_cache_roundtrip(tmp_path):
    c = Cache(str(tmp_path / "c"), enabled=True)
    key = Cache.make_key({"name": "x"}, "prompt")
    assert c.get(key) is None
    c.set(key, {"text": "hi", "input_tokens": 1})
    assert c.get(key)["text"] == "hi"
