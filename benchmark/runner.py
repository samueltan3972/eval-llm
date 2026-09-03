"""Orchestrate a benchmark run: models x cases -> grade -> score -> assemble a run dict.

Each (model, case) is a task on a thread pool: generate the answer (cached), then grade its
assertions (the judge is just another cached provider, so llm-rubric grades are cached too).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone

from . import assertions as asserts_mod
from .assertions import pairwise
from .config import Config
from .persistence import Cache
from .providers import build_provider
from .providers.base import GenerationResult
from .providers.cli import CliProvider
from .scoring import CaseMeasurement, DEFAULT_SCORING, score_model
from .suite import TestCase, suite_hash

OUTPUT_CAP = 4000  # chars of model output stored per case in the run file


class CachingProvider:
    """Wraps a provider so identical (model-identity, prompt) calls are served from disk.

    Latency/tokens come from the original measured call — reusing them keeps re-runs fast and
    scores stable while iterating. Use --no-cache for a fresh measurement.
    """

    def __init__(self, inner, cache: Cache):
        self.inner = inner
        self.cache = cache
        self.name = inner.name
        self.display = getattr(inner, "display", inner.name)

    @property
    def startup_overhead_ms(self) -> float:
        return getattr(self.inner, "startup_overhead_ms", 0.0)

    def throughput(self, result: GenerationResult):
        return self.inner.throughput(result)

    def generate(self, prompt: str) -> GenerationResult:
        key = Cache.make_key(self.inner.cache_key_fields(), prompt)
        hit = self.cache.get(key)
        if hit is not None:
            return GenerationResult(**hit)
        res = self.inner.generate(prompt)
        if res.ok:  # don't cache transient errors
            self.cache.set(key, asdict(res))
        return res


def _build_models(config: Config, dry_run: bool, only: list[str] | None):
    specs = config.models
    if only:
        wanted = set(only)
        specs = [m for m in specs if m["name"] in wanted]
        if not specs:
            raise ValueError(f"no models matched --models {only}")
    if dry_run:
        specs = [{**m, "type": "mock"} for m in specs]
    return [build_provider(s) for s in specs]


def _build_judges(config: Config, dry_run: bool) -> list:
    """Build the judge ensemble (>=0 providers). Dry-run uses two distinct mock judges."""
    if dry_run:
        return [
            build_provider({"name": "mock-judge-1", "type": "mock", "sim_out_tokens": 20}),
            build_provider({"name": "mock-judge-2", "type": "mock", "sim_out_tokens": 20}),
        ]
    out = []
    for i, spec in enumerate(config.judges or []):
        out.append(build_provider({**spec, "name": spec.get("name", f"judge-{i+1}")}))
    return out


# Calibration: a trivial prompt produces a near-empty answer, so its wall-clock is ~all
# fixed startup (CLI boot, auth, handshake). The MIN over a few tries is a robust floor.
CALIBRATION_PROMPT = "Reply with only: ok"
CALIBRATION_RUNS = 2


def _calibrate_startup(providers, dry_run: bool, scoring_cfg: dict) -> None:
    """Measure each CLI provider's fixed startup latency and store it as `startup_overhead_ms`.

    Run once per run, before scoring; the value is later subtracted from each call's latency so
    speed reflects generation, not process boot (see Provider.throughput). CLI-only — APIs have
    no process to boot. Mutates the inner providers in place. Skipped on dry-run.
    """
    if dry_run or not scoring_cfg.get("speed", {}).get("calibrate_startup", True):
        return
    runs = int(scoring_cfg.get("speed", {}).get("calibration_runs", CALIBRATION_RUNS))
    for prov in providers:
        inner = getattr(prov, "inner", prov)
        if not isinstance(inner, CliProvider):
            continue
        lats = [r.latency_ms for r in (inner.generate(CALIBRATION_PROMPT) for _ in range(runs))
                if r.ok and r.latency_ms > 0]
        if lats:
            inner.startup_overhead_ms = min(lats)


def run_benchmark(
    config: Config,
    cases: list[TestCase],
    dry_run: bool = False,
    only_models: list[str] | None = None,
    use_cache: bool = True,
) -> dict:
    cache = Cache(config.cache_dir, enabled=use_cache and not dry_run)
    providers = [CachingProvider(p, cache) for p in _build_models(config, dry_run, only_models)]
    judges = [CachingProvider(j, cache) for j in _build_judges(config, dry_run)]

    scoring_cfg = {**DEFAULT_SCORING, **(config.scoring or {})}
    displays = {p.name: p.display for p in providers}  # identity -> human label
    max_workers = max(1, int(config.concurrency))

    # Estimate each CLI tool's fixed startup latency so speed measures generation, not boot.
    _calibrate_startup(providers, dry_run, scoring_cfg)
    overhead_by_name = {p.name: p.startup_overhead_ms for p in providers}

    # --- Phase 1: generate every (model, case) answer and grade objective/rubric ------------
    tasks = [(prov, ci, case) for prov in providers for ci, case in enumerate(cases)]

    def run_one(task):
        prov, ci, case = task
        res = prov.generate(case.question)
        if not res.ok:
            measurement = CaseMeasurement(
                domain=case.domain, quality=None, latency_ms=res.latency_ms,
                tokens_per_sec=None, cost_usd=res.cost_usd, output_tokens=res.output_tokens,
                tokens_exact=res.tokens_exact, error=res.error,
            )
            detail = _detail(prov.name, prov.display, ci, case, res, quality=None, results=[], judge_cost=0.0)
            return prov.name, ci, measurement, detail, None

        graded = asserts_mod.grade_case(case.asserts, res.text, judges)
        measurement = CaseMeasurement(
            domain=case.domain, quality=graded["quality"], latency_ms=res.latency_ms,
            tokens_per_sec=prov.throughput(res), cost_usd=res.cost_usd,
            output_tokens=res.output_tokens, tokens_exact=res.tokens_exact, error=None,
        )
        detail = _detail(prov.name, prov.display, ci, case, res, graded["quality"],
                         graded["results"], graded["judge_cost_usd"])
        return prov.name, ci, measurement, detail, res.text

    by_model: dict[str, list[CaseMeasurement]] = {p.name: [] for p in providers}
    details: list[dict] = []
    detail_by: dict[tuple, dict] = {}          # (model, case_idx) -> detail
    answers: dict[int, dict[str, str]] = {}    # case_idx -> {model: answer} (ok results only)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for name, ci, measurement, detail, answer in ex.map(run_one, tasks):
            by_model[name].append(measurement)
            details.append(detail)
            detail_by[(name, ci)] = detail
            if answer is not None:
                answers.setdefault(ci, {})[name] = answer

    # --- Phase 2: pairwise "local judging" for open-ended cases ------------------------------
    head_to_head, win_rate, pairwise_cost = _pairwise_phase(cases, answers, judges, detail_by, max_workers)

    # --- Score + within-run relative metrics ------------------------------------------------
    model_scores = []
    for name, ms in by_model.items():
        s = score_model(name, ms, scoring_cfg)
        s.display = displays.get(name, name)
        s.win_rate = win_rate.get(name)
        s.startup_overhead_ms = round(overhead_by_name.get(name, 0.0), 1)
        model_scores.append(s)

    # best in run = 1.00x: x pricier than the cheapest, x slower than the fastest.
    positive_costs = [s.avg_cost_usd for s in model_scores if s.avg_cost_usd > 0]
    min_cost = min(positive_costs) if positive_costs else 0.0
    tps_vals = [s.avg_tokens_per_sec for s in model_scores if s.avg_tokens_per_sec > 0]
    max_tps = max(tps_vals) if tps_vals else 0.0
    for s in model_scores:
        s.cost_vs_cheapest = round(s.avg_cost_usd / min_cost, 2) if min_cost > 0 else None
        s.speed_vs_fastest = round(max_tps / s.avg_tokens_per_sec, 2) if s.avg_tokens_per_sec > 0 else None

    total_judge_cost = sum(d.get("judge_cost_usd", 0.0) for d in details) + pairwise_cost

    weights = {**DEFAULT_SCORING["weights"], **(scoring_cfg.get("weights") or {})}
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "label": config.label + ("-dryrun" if dry_run else ""),
        "n_cases": len(cases),
        "suite_hash": suite_hash(cases),
        "dry_run": dry_run,
        "weights_note": f"q{weights['quality']:g}/s{weights['speed']:g}/c{weights['cost']:g}",
        "scoring": scoring_cfg,
        "judges": [j.name for j in judges],
        "baseline": config.baseline,
        "total_judge_cost_usd": round(total_judge_cost, 6),
        "head_to_head": head_to_head,
        "models": [asdict(s) for s in sorted(model_scores, key=lambda s: s.overall, reverse=True)],
        "details": details,
    }


def _pairwise_phase(cases, answers, judges, detail_by, max_workers):
    """Run all-pairs ensemble battles for each open-ended case; return the head-to-head matrix,
    per-model win-rate, and total pairwise judge cost. Win-rate is each model's mean win-weight
    across the open-ended cases it answered (a per-run, relative signal)."""
    import itertools
    from collections import defaultdict

    if not judges:
        return {"models": [], "matrix": {}, "win_rate": {}}, {}, 0.0

    specs = []
    for ci, case in enumerate(cases):
        if case.kind != "open_ended":
            continue
        present = sorted(answers.get(ci, {}))
        if len(present) < 2:
            continue
        rubric = pairwise.rubric_of(case.asserts)
        for a, b in itertools.combinations(present, 2):
            specs.append((ci, case, a, b, rubric))

    if not specs:
        return {"models": [], "matrix": {}, "win_rate": {}}, {}, 0.0

    def run_battle(spec):
        ci, case, a, b, rubric = spec
        r = pairwise.battle(case.question, rubric, answers[ci][a], answers[ci][b], judges)
        return ci, a, b, r

    case_pts = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))   # ci -> model -> [sum, n]
    pair_acc = defaultdict(lambda: [0.0, 0])                        # (a,b) -> [sum a_win, n]
    cost = 0.0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for ci, a, b, r in ex.map(run_battle, specs):
            case_pts[ci][a][0] += r["a_win"]; case_pts[ci][a][1] += 1
            case_pts[ci][b][0] += r["b_win"]; case_pts[ci][b][1] += 1
            pair_acc[(a, b)][0] += r["a_win"]; pair_acc[(a, b)][1] += 1
            cost += r["judge_cost_usd"]

    # Per-model win-rate + attach each model's per-case win-weight to its detail.
    model_acc = defaultdict(lambda: [0.0, 0])
    for ci, mp in case_pts.items():
        for name, (s, n) in mp.items():
            w = s / n if n else 0.0
            model_acc[name][0] += w
            model_acc[name][1] += 1
            d = detail_by.get((name, ci))
            if d is not None:
                d["pairwise_win"] = round(w, 3)
    win_rate = {name: round(s / n, 3) for name, (s, n) in model_acc.items() if n}

    models = sorted({m for mp in case_pts.values() for m in mp})
    matrix = {a: {} for a in models}
    for (a, b), (s, n) in pair_acc.items():
        a_win = round(s / n, 3) if n else 0.5
        matrix[a][b] = a_win
        matrix[b][a] = round(1.0 - a_win, 3)
    return {"models": models, "matrix": matrix, "win_rate": win_rate}, win_rate, cost


def _detail(model: str, model_display: str, case_idx: int, case: TestCase, res: GenerationResult,
            quality, results, judge_cost) -> dict:
    return {
        "model": model,
        "model_display": model_display,
        "case_idx": case_idx,
        "domain": case.domain,
        "tier": case.tier,
        "kind": case.kind,
        "description": case.description,
        "quality": quality,
        "latency_ms": round(res.latency_ms, 1),
        "input_tokens": res.input_tokens,
        "output_tokens": res.output_tokens,
        "tokens_exact": res.tokens_exact,
        "cost_usd": res.cost_usd,
        "error": res.error,
        "output": (res.text or "")[:OUTPUT_CAP],
        "assertions": [
            {"type": r.type, "score": r.score, "pass": r.passed, "reason": r.reason,
             "per_judge": r.per_judge, "disagreement": r.disagreement}
            for r in results
        ],
        "judge_cost_usd": judge_cost,
    }
