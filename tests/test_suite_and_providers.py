import os

from benchmark.providers import build_provider
from benchmark.providers.mock import MockProvider
from benchmark.suite import TestCase, filter_cases, load_suite, suite_hash
from benchmark.tokens import count_tokens, estimate_cost


def _cases():
    return [
        TestCase("a", "q1", [], domain="math", tier="core"),
        TestCase("b", "q2", [], domain="math", tier="extended"),
        TestCase("c", "q3", [], domain="logic", tier="core"),
    ]


def test_filter_by_metadata():
    out = filter_cases(_cases(), {"domain": "math"})
    assert {c.description for c in out} == {"a", "b"}
    out = filter_cases(_cases(), {"tier": "core"})
    assert {c.description for c in out} == {"a", "c"}


def test_filter_sample_is_deterministic():
    a = filter_cases(_cases(), {}, sample=2, seed=1)
    b = filter_cases(_cases(), {}, sample=2, seed=1)
    assert [c.description for c in a] == [c.description for c in b]
    assert len(a) == 2


def test_suite_hash_stable_and_order_independent():
    cases = _cases()
    assert suite_hash(cases) == suite_hash(list(reversed(cases)))
    changed = _cases() + [TestCase("d", "q4", [], domain="x", tier="core")]
    assert suite_hash(cases) != suite_hash(changed)


def test_load_real_datasets():
    root = os.path.dirname(os.path.dirname(__file__))
    cases = load_suite("datasets/*.yaml", root=root)
    assert len(cases) > 30
    assert all(c.question for c in cases)
    assert {"core", "extended"} & {c.tier for c in cases}


def test_tokens_and_cost():
    assert count_tokens("") == 0
    assert count_tokens("hello world") >= 1
    # 1000 in @ $1/M + 500 out @ $2/M = 0.001 + 0.001 = 0.002
    assert estimate_cost(1000, 500, 1.0, 2.0) == 0.002


def test_mock_provider_measures_cost_from_pricing():
    p = build_provider({"name": "m", "type": "mock", "pricing": {"input": 1.0, "output": 2.0},
                        "sim_out_tokens": 100, "sim_latency_ms": 500})
    assert isinstance(p, MockProvider)
    r = p.generate("Reply with only the number: 2+2")
    assert r.text == "42"
    assert r.cost_usd > 0
    assert r.latency_ms == 500
    assert r.tokens_per_sec is not None


def test_cli_provider_echo(tmp_path):
    # /bin/echo as a stand-in CLI tool: prompt substituted as a single argv element.
    p = build_provider({"name": "echo", "type": "cli", "command": "/bin/echo {prompt}",
                        "pricing": {"input": 1.0, "output": 1.0}})
    r = p.generate("hello there")
    assert r.text == "hello there"
    assert r.ok and r.error is None
    assert r.input_tokens > 0 and r.output_tokens > 0
    assert r.tokens_exact is False  # estimated, since echo reports no usage


def test_throughput_subtracts_startup_overhead():
    # Speed should measure generation, not process boot: with a fixed startup overhead removed,
    # the same call reports a higher (generation-only) tok/s than the raw wall-clock rate.
    from benchmark.providers.base import GenerationResult
    p = build_provider({"name": "echo", "type": "cli", "command": "/bin/echo {prompt}"})
    res = GenerationResult(text="x", output_tokens=100, latency_ms=2000.0)
    assert res.tokens_per_sec == 50.0          # raw: 100 tok / 2.0s
    p.startup_overhead_ms = 1000.0             # 1s of that 2s was boot
    assert p.throughput(res) == 100.0          # generation-only: 100 tok / 1.0s


def test_throughput_floor_prevents_blowup():
    # Subtracting an overhead >= latency must not divide by ~0 / go negative.
    from benchmark.providers.base import GenerationResult, MIN_ADJUSTED_MS
    p = build_provider({"name": "echo", "type": "cli", "command": "/bin/echo {prompt}"})
    p.startup_overhead_ms = 5000.0
    res = GenerationResult(text="x", output_tokens=10, latency_ms=300.0)
    assert p.throughput(res) == 10 / (MIN_ADJUSTED_MS / 1000.0)


def test_api_provider_has_zero_overhead_so_throughput_is_raw():
    from benchmark.providers.base import GenerationResult
    p = build_provider({"name": "m", "type": "api", "api": "openai", "model": "gpt-4o"})
    res = GenerationResult(text="x", output_tokens=100, latency_ms=1000.0)
    assert p.startup_overhead_ms == 0.0
    assert p.throughput(res) == res.tokens_per_sec == 100.0


def test_calibrate_startup_cli_only_and_skips_dry_run():
    from benchmark.runner import _calibrate_startup
    # A CLI tool that sleeps ~150ms before answering => that's its measured startup floor.
    sleeper = build_provider({"name": "slow", "type": "cli", "shell": True,
                              "command": "sleep 0.15; echo ok"})
    api = build_provider({"name": "api", "type": "api", "api": "openai", "model": "gpt-4o"})
    cfg = {"speed": {"calibrate_startup": True, "calibration_runs": 2}}
    _calibrate_startup([sleeper, api], dry_run=False, scoring_cfg=cfg)
    assert sleeper.startup_overhead_ms >= 140.0   # ~the sleep, give or take scheduling
    assert api.startup_overhead_ms == 0.0          # APIs are never calibrated

    sleeper2 = build_provider({"name": "slow2", "type": "cli", "shell": True,
                               "command": "sleep 0.15; echo ok"})
    _calibrate_startup([sleeper2], dry_run=True, scoring_cfg=cfg)
    assert sleeper2.startup_overhead_ms == 0.0      # dry-run makes no calls


def test_calibrate_startup_respects_disable_flag():
    from benchmark.runner import _calibrate_startup
    sleeper = build_provider({"name": "slow", "type": "cli", "shell": True,
                              "command": "sleep 0.1; echo ok"})
    _calibrate_startup([sleeper], dry_run=False, scoring_cfg={"speed": {"calibrate_startup": False}})
    assert sleeper.startup_overhead_ms == 0.0


def test_display_model_and_derived_tool():
    p = build_provider({"name": "codex", "model": "GPT-5.5", "type": "cli",
                        "command": "codex exec --model gpt-5.5 {prompt}"})
    assert p.name == "codex"             # identity/key unchanged
    assert p.display == "GPT-5.5 (codex)"  # tool derived from the command binary
    assert p.tool == "codex"


def test_display_falls_back_to_name_without_model():
    p = build_provider({"name": "codex", "type": "cli", "command": "codex exec {prompt}"})
    assert p.display == "codex"


def test_display_explicit_tool_overrides_derived():
    p = build_provider({"name": "codex-mini", "model": "GPT-5-mini", "tool": "codex",
                        "type": "cli", "command": "codex exec --model gpt-5-mini {prompt}"})
    assert p.display == "GPT-5-mini (codex)"


def test_display_strips_sh_suffix_from_wrapper():
    p = build_provider({"name": "c", "model": "GPT-5.5", "type": "cli",
                        "command": "./run-codex.sh {prompt}"})
    assert p.display == "GPT-5.5 (run-codex)"


def test_display_skips_leading_shell_assignment_to_find_binary():
    # shell: true commands often start with `OUT=$(mktemp); realcmd ...`; the tool label should
    # still be the binary, not the assignment.
    p = build_provider({"name": "c", "model": "GPT-5.5", "type": "cli", "shell": True,
                        "command": 'OUT=$(mktemp); codex exec --model x "$PROMPT"; cat "$OUT"'})
    assert p.display == "GPT-5.5 (codex)"


def test_display_api_tool_from_api_field():
    p = build_provider({"name": "gpt", "model": "gpt-4o", "type": "api", "api": "openai",
                        "api_key_env": "X"})
    assert p.display == "gpt-4o (openai)"


def test_cli_provider_reports_error_on_bad_command():
    p = build_provider({"name": "nope", "type": "cli", "command": "/bin/false {prompt}"})
    r = p.generate("x")
    assert not r.ok and "exit" in (r.error or "")


def test_cli_shell_mode_passes_prompt_via_env():
    # Shell features (a pipe) plus the prompt delivered through $PROMPT.
    p = build_provider({
        "name": "sh", "type": "cli", "shell": True,
        "command": 'printf %s "$PROMPT" | tr a-z A-Z',
        "pricing": {"input": 1.0, "output": 1.0},
    })
    r = p.generate("hello")
    assert r.ok and r.text == "HELLO"


def test_cli_shell_mode_is_injection_safe():
    # A prompt containing shell metacharacters must be treated as literal data, not code.
    p = build_provider({
        "name": "sh", "type": "cli", "shell": True,
        "command": 'printf %s "$PROMPT"',
    })
    r = p.generate("$(echo pwned) `whoami`")
    assert r.ok and r.text == "$(echo pwned) `whoami`"
