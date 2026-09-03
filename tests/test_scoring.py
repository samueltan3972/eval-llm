from benchmark.scoring import CaseMeasurement, cost_score, score_model, speed_score


def _case(quality=1.0, latency_ms=1000.0, tps=50.0, cost=0.001, domain="math", error=None, exact=False):
    return CaseMeasurement(
        domain=domain, quality=quality, latency_ms=latency_ms, tokens_per_sec=tps,
        cost_usd=cost, output_tokens=100, tokens_exact=exact, error=error,
    )


def test_speed_score_tps_anchor_and_cap():
    cfg = {"metric": "tps", "anchor_tps": 50}
    assert speed_score(50, 1000, cfg) == 100.0   # at anchor
    assert speed_score(25, 1000, cfg) == 50.0    # half
    assert speed_score(200, 1000, cfg) == 100.0  # capped


def test_speed_score_latency_metric():
    cfg = {"metric": "latency", "anchor_latency_ms": 2000}
    assert speed_score(None, 2000, cfg) == 100.0
    assert speed_score(None, 4000, cfg) == 50.0


def test_cost_score_linear_free_to_zero():
    cfg = {"zero_score_usd_per_call": 1.0}
    assert cost_score(0.0, cfg) == 100.0       # free -> 100
    assert cost_score(1.0, cfg) == 0.0         # at Z -> 0
    assert cost_score(0.5, cfg) == 50.0        # linear midpoint
    assert cost_score(2.0, cfg) == 0.0         # beyond Z -> floored at 0
    # cheaper strictly higher; cheap models still differentiated
    assert cost_score(0.0004, cfg) > cost_score(0.0006, cfg)
    assert round(cost_score(0.0004, cfg), 2) == 99.96


def test_overall_is_equal_weighted_average():
    cases = [_case(quality=1.0, tps=50.0, cost=0.0)]  # free -> cost 100
    s = score_model("m", cases)
    assert s.quality == 100.0 and s.speed == 100.0 and s.cost == 100.0
    assert s.overall == 100.0


def test_error_counts_as_zero_quality_but_not_speed_cost():
    cases = [_case(quality=1.0), _case(error="boom")]
    s = score_model("m", cases)
    # quality mean of [1.0, 0.0] => 50
    assert s.quality == 50.0
    assert s.n_errors == 1
    # speed/cost computed only over the one successful call
    assert s.n_cases == 2


def test_quality_by_domain_breakdown():
    cases = [_case(quality=1.0, domain="math"), _case(quality=0.0, domain="logic")]
    s = score_model("m", cases)
    assert s.quality_by_domain == {"logic": 0.0, "math": 100.0}


def test_ungradeable_case_excluded_from_quality():
    cases = [_case(quality=None), _case(quality=1.0)]
    s = score_model("m", cases)
    assert s.quality == 100.0  # the None case doesn't drag it down


def test_cost_estimated_flag():
    assert score_model("m", [_case(exact=False)]).cost_estimated is True
    assert score_model("m", [_case(exact=True)]).cost_estimated is False
