"""Turn raw per-case measurements into three independent 0-100 scores + an overall.

  quality  mean case quality (0-1) x 100
  speed    from tokens/sec (default) or latency, vs a FIXED config anchor
  cost     from average USD/call, cheaper is better, vs a FIXED config anchor
  overall  weighted mean of the three (default equal weights, 33/33/33)

Anchors are fixed (not relative to the other models in the run) so scores stay comparable
across runs as new models are added over time. Raw metrics are always carried alongside the
scores as the ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

# Defaults — overridable via the `scoring:` block in benchmark.yaml.
DEFAULT_SCORING = {
    "weights": {"quality": 1.0, "speed": 1.0, "cost": 1.0},
    "speed": {"metric": "tps", "anchor_tps": 50.0, "anchor_latency_ms": 2000.0},
    "cost": {"zero_score_usd_per_call": 1.0},
}


@dataclass
class CaseMeasurement:
    domain: str
    quality: float | None        # 0-1, or None if not gradeable
    latency_ms: float
    tokens_per_sec: float | None
    cost_usd: float
    output_tokens: int
    tokens_exact: bool
    error: str | None = None


@dataclass
class ModelScore:
    name: str            # stable identity/key
    display: str = ""    # human-facing label, e.g. "GPT-5.5 (codex)"
    quality: float = 0.0
    speed: float = 0.0
    cost: float = 0.0
    overall: float = 0.0
    # Raw aggregates (ground truth).
    avg_latency_ms: float = 0.0
    avg_tokens_per_sec: float = 0.0     # startup-adjusted (generation speed), not raw wall-clock
    startup_overhead_ms: float = 0.0    # calibrated fixed startup subtracted from speed (CLI tools)
    avg_cost_usd: float = 0.0
    total_cost_usd: float = 0.0
    cost_vs_cheapest: float | None = None   # x-multiplier vs the run's cheapest model
    speed_vs_fastest: float | None = None   # x-multiplier vs the run's fastest model (tok/s)
    win_rate: float | None = None           # per-run pairwise win-rate (open-ended cases)
    avg_output_tokens: float = 0.0
    cost_estimated: bool = False
    n_cases: int = 0
    n_errors: int = 0
    quality_by_domain: dict = field(default_factory=dict)


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def speed_score(tokens_per_sec: float | None, latency_ms: float, cfg: dict) -> float:
    """Higher throughput / lower latency -> higher score, linear to a fixed anchor, capped."""
    metric = cfg.get("metric", "tps")
    if metric == "latency":
        anchor = float(cfg.get("anchor_latency_ms", 2000.0))
        if latency_ms <= 0:
            return 0.0
        return _clamp(100.0 * anchor / latency_ms)
    anchor = float(cfg.get("anchor_tps", 50.0))
    if not tokens_per_sec or tokens_per_sec <= 0:
        return 0.0
    return _clamp(100.0 * tokens_per_sec / anchor)


def cost_score(avg_cost_usd: float, cfg: dict) -> float:
    """Linear & absolute: free => 100, dropping evenly to 0 at `zero_score_usd_per_call`.

    Cheaper always scores strictly higher; the same price always yields the same score, so it
    is comparable across runs. The score is bounded [0, 100] so it can be weighted equally with
    quality/speed in the overall — the unbounded magnitude lives in the raw $/call and the
    `vs <baseline>` multiplier, not here.
    """
    z = float(cfg.get("zero_score_usd_per_call", 1.0))
    if z <= 0:
        return 100.0 if avg_cost_usd <= 0 else 0.0
    return _clamp(100.0 * (1.0 - avg_cost_usd / z))


def score_model(name: str, cases: list[CaseMeasurement], scoring_cfg: dict | None = None) -> ModelScore:
    cfg = {**DEFAULT_SCORING, **(scoring_cfg or {})}
    weights = {**DEFAULT_SCORING["weights"], **(cfg.get("weights") or {})}
    speed_cfg = {**DEFAULT_SCORING["speed"], **(cfg.get("speed") or {})}
    cost_cfg = {**DEFAULT_SCORING["cost"], **(cfg.get("cost") or {})}

    n = len(cases)
    score = ModelScore(name=name, n_cases=n)
    if n == 0:
        return score

    # Quality: errored calls and ungradeable cases handled explicitly. An error means the
    # model failed to answer -> quality 0 for that case (not excluded).
    quality_vals: list[float] = []
    by_domain: dict[str, list[float]] = {}
    for c in cases:
        if c.error is not None:
            q = 0.0
        elif c.quality is None:
            continue  # nothing gradeable; don't bias the mean
        else:
            q = c.quality
        quality_vals.append(q)
        by_domain.setdefault(c.domain, []).append(q)

    score.quality = round(100.0 * mean(quality_vals), 2) if quality_vals else 0.0
    score.quality_by_domain = {d: round(100.0 * mean(v), 2) for d, v in sorted(by_domain.items())}

    # Speed / cost aggregates over successful calls (errors have no meaningful latency/cost).
    ok = [c for c in cases if c.error is None]
    score.n_errors = n - len(ok)
    if ok:
        lat = [c.latency_ms for c in ok if c.latency_ms > 0]
        tps = [c.tokens_per_sec for c in ok if c.tokens_per_sec]
        costs = [c.cost_usd for c in ok]
        score.avg_latency_ms = round(mean(lat), 1) if lat else 0.0
        score.avg_tokens_per_sec = round(mean(tps), 2) if tps else 0.0
        score.avg_cost_usd = mean(costs) if costs else 0.0
        score.total_cost_usd = sum(c.cost_usd for c in cases)
        score.avg_output_tokens = round(mean([c.output_tokens for c in ok]), 1)
        score.cost_estimated = any(not c.tokens_exact for c in ok)

    score.speed = round(speed_score(score.avg_tokens_per_sec, score.avg_latency_ms, speed_cfg), 2)
    score.cost = round(cost_score(score.avg_cost_usd, cost_cfg), 2)

    wsum = sum(weights.values()) or 1.0
    score.overall = round(
        (weights["quality"] * score.quality + weights["speed"] * score.speed + weights["cost"] * score.cost)
        / wsum,
        2,
    )
    return score
