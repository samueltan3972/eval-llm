# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Two ways to evaluate LLMs over **one shared test suite** (`datasets/*.yaml`):

1. **promptfoo** (`promptfooconfig.yaml`) — declarative, unit-test-style runs; providers = columns, cases = rows. Good for pass/fail checks and the promptfoo GUI. **Limitation:** with `exec:` CLI providers it reports **$0 cost** (it can't see token usage from a subprocess) and only gives pass/fail, not a comparable rating.

2. **`benchmark/`** (Python harness, see `BENCHMARK.md`) — built to fix exactly that. Measures latency + tokens on every call and produces **three 0–100 scores (quality, speed, cost) + an equal-weighted overall**, saved per-run for comparison. This is the path for ranking models on cost/speed/quality together.

The shared `datasets/*.yaml` suite is the user's **private** test set (deliberately not a public benchmark, to avoid gaming). Both runners reuse it and the `run-*.sh` CLI wrappers.

## Commands

Requires Node.js 18+ and an `ANTHROPIC_API_KEY` (in `.env`; copy from `.env.example`). Both runners auto-load `.env`.

### benchmark harness (Python — the quality/speed/cost scorer)

```bash
pipenv install --dev                                    # one-time: create the venv
pipenv run python -m benchmark run --dry-run            # no spend; exercises the whole pipeline
pipenv run python -m benchmark run --filter tier=core   # mirrors the dataset metadata filters
pipenv run python -m benchmark run --models codex,antigravity --sample 8 --by-domain
pipenv run python -m benchmark leaderboard              # latest run's table
pipenv run python -m benchmark compare runs/<A>/run.json runs/<B>/run.json
pipenv run python -m benchmark dashboard                # cross-run Streamlit leaderboard
pipenv run pytest                                       # unit tests
```

See `BENCHMARK.md` for the full design. Config is `benchmark.yaml` (models, `judges:` ensemble,
`baseline:`, scoring anchors).

### promptfoo (Node — unit-test-style runs)

```bash
npm install                 # gets pinned promptfoo (or use npx promptfoo@latest)
npm run eval                # comprehensive — all ~63 cases x each provider
npm run eval:core           # balanced core subset (~32 cases)
npm run eval:sample         # quick random 8-case smoke test
npm run view                # open the GUI dashboard after a run
```

Selecting coverage at run time (every case is tagged `metadata.domain` and `metadata.tier`):

```bash
npx promptfoo@latest eval --filter-metadata tier=core
npx promptfoo@latest eval --filter-metadata domain=code_generation
npx promptfoo@latest eval --filter-metadata tier=core --filter-metadata domain=math
npx promptfoo@latest eval --filter-sample 8        # random N
```

Run output lands in `runs/results.json` (raw per-cell tokens/cost/latency/scores) and `runs/results.html` (standalone report). promptfoo caches cells, so re-running unchanged cases is free.

## Architecture

**Single prompt, sent verbatim to every provider.** `prompts:` in `promptfooconfig.yaml` is just `"{{question}}"`. Each test case carries its entire instruction (and any passage/context) in its `question` var, so cases are self-contained and the comparison is apples-to-apples.

**Providers** are configured in `promptfooconfig.yaml` (mostly commented templates you uncomment). Two kinds:
- Native API providers (`anthropic:messages:...`, `openai:gpt-4o`, OpenRouter, etc.) — keyed via `.env`.
- **CLI tools via the `exec:` provider** — `run-codex.sh`, `run-agy.sh`, `run-claude.sh`. Each is a `#!/bin/bash` wrapper that takes the prompt as `$1` and shells out to a CLI (`codex exec`, `agy`, `claude -p`). To add/swap a CLI tool, edit or add one of these scripts and reference it as `exec:./run-foo.sh`. Match how each CLI expects the prompt and ensure it prints **only** the answer to stdout (e.g. `run-codex.sh` streams Codex's final message to stdout via a spare fd — `--output-last-message /dev/fd/3 ... 3>&1 >/dev/null 2>&1` — sending all chatter to `/dev/null`. No temp file, so it's race-free per process and keeps file I/O out of the timed path, which would otherwise corrupt answers under parallel runs or bias the speed measurement).

**Tests** come from `tests: file://datasets/*.yaml` — the glob auto-discovers every file, so adding `datasets/<new>.yaml` needs no config change. Each file is one task domain and is a YAML list of cases shaped:
```yaml
- description: "..."
  vars: { question: "..." }
  assert: [ ... ]
  metadata: { domain: <name>, tier: core | extended }
```

**Scoring / assertions** mix three styles, often combined on one case:
- *Reference (deterministic)* — `equals`, `regex`, `contains`, `is-json`/`contains-json` with schemas. Prompts deliberately say "Reply with only the number/JSON" so deterministic matches work.
- *`javascript`* — runs real code over `output`. In `code_generation.yaml` it strips code fences, `new Function(...)`-instantiates the generated function, and executes it against test inputs. In `summarization.yaml`/`instruction_following.yaml` it enforces length/format/forbidden-word constraints.
- *`llm-rubric` (LLM-as-judge)* — graded by the model in `defaultTest.options.provider`. The judge is **blind** (sees only output + rubric, never which provider produced it) and **swappable** in one line (set it to a cheap model like `anthropic:messages:claude-haiku-4-5` to cut judge cost on big runs).

`defaultTest.assert` also adds a `latency` threshold (60s) to every cell — informational by default; tighten thresholds to turn cost/latency into hard gates.

### benchmark harness architecture (`benchmark/`)

A `config → providers → assertions → scoring → persistence` pipeline; entry point `python -m benchmark`. Key design points (full detail in `BENCHMARK.md`):

- **Providers** (`benchmark/providers/`) share one `GenerationResult` (text + tokens + latency + cost + `tokens_exact`). `cli.py` is the primary path: commands are **inlined in `benchmark.yaml`** (no `.sh` needed) in two forms — a plain command (`{prompt}` substituted as a single argv element, no shell) or `shell: true` (for pipes/redirects/`;`, with the prompt passed via the `$PROMPT` env var, never interpolated, so it's injection-safe). `api.py` reads exact usage from OpenAI/Anthropic responses; `mock.py` backs `--dry-run`.
- **Identity vs. display.** A model's `name` is the stable **key** (runner `by_model`, cache, `compare`, HTML `id="m-..."` anchors). The optional `model` field is the real model and feeds a **display-only** label `display = "model (tool)"` (tool auto-derived from the command binary / `api`, overridable via `tool:`), computed in `Provider.__init__` (`benchmark/providers/base.py`, `_derive_tool`/`_make_display`). The label flows `ModelScore.display` → run JSON `models[].display` / details `model_display` → all reports; reports show `display` but key everything on `name`, so relabeling never breaks `compare`.
- **Cost for CLI tools is the central problem this solves.** CLIs expose no token usage, so `tokens.py` *estimates* tokens (tiktoken, with a char-heuristic fallback) and cost = estimated tokens × per-model `pricing` from `benchmark.yaml`. Estimated cost is flagged `tokens_exact=False` and shown with `~`. API models get exact cost.
- **Assertions** (`benchmark/assertions/`) reuse the dataset types but grade **0–1** (not pass/fail): deterministic in Python, `llm-rubric` via a **blind judge ensemble** (`judge.py` `evaluate_rubric_ensemble` → mean + per-judge breakdown + disagreement), and `javascript` via a Node bridge (`run_js.js`) so code-gen cases that execute generated code work unchanged. A case's quality = mean of its assertion scores. Each case has a **`kind`** (`suite.py` `detect_kind`): `open_ended` if it has an `llm-rubric` or is tagged `metadata.kind: open_ended`, else `objective`. Open-ended cases additionally get **pairwise** judging (`pairwise.py` `battle`, blind + order-swapped) in the runner's two-phase flow → a per-run `head_to_head` matrix + per-model `win_rate` (relative, per-run only — *not* the cross-run quality).
- **Scoring** (`scoring.py`) → three independent 0–100 scores. Quality is the mean case score; speed (tokens/sec) vs a fixed `anchor_tps`; **cost is linear & absolute** — `max(0, 100 × (1 − avg_cost/Z))`, free→100, 0 at `cost.zero_score_usd_per_call` (Z, default $1/call), bounded so it can weight equally in the overall. All normalizations use **fixed config anchors** (not peer-relative) so runs stay comparable. Overall = weighted mean (default equal = 33/33/33). The unbounded cost truth lives in raw `$/call` and a **within-run `vs <baseline>` multiplier** (`cost_vs_cheapest` stored per model; the HTML report makes the baseline a live dropdown via a tiny inline JS that recomputes from each cell's `data-cost`). Speed mirrors this with `speed_vs_fastest` (a `vs fastest` report column). Raw metrics are always stored too.
- **Cross-run leaderboard** (`leaderboard.py`, pure/unit-tested + `dashboard.py`, thin Streamlit) is the **relative** counterpart to the fixed-anchor per-run report: it pools `runs/*.json`, aggregates per model (quality = ensemble rubric mean; speed/cost raw **and** baseline-relative vs `run["baseline"]`, falling back to fastest/cheapest), offers per-dimension sort + **weight sliders** (each dim normalized 0–100 within the pool for a live composite re-rank) + trends + per-run head-to-head. Launch with `python -m benchmark dashboard` (`__main__.py` shells out to `streamlit run`). Keep aggregation logic in `leaderboard.py` so it's testable without a browser.
- **Persistence** (`persistence.py`) saves each run to its own folder `runs/<ts>__<label>/` (`run.json` + `report.html` + `leaderboard.md`, all per-run so nothing at the `runs/` root is overwritten between runs) with a **suite hash** (so `compare` can warn when two runs aren't comparable), and caches model+judge responses in `.cache/` (keyed by provider identity + prompt) so re-runs are near-instant. `list_runs` globs `runs/*/run.json`.
- **HTML report** (`report_html.py`) renders a run to a **self-contained** `runs/<ts>__<label>.html` (inline CSS + SVG, no CDN/JS framework, native `<details>` for drill-in) — written automatically each run and rebuildable via `python -m benchmark report`. All dynamic text is HTML-escaped.

CLI latency includes process startup (CLI boot, auth, handshake), which would otherwise depress CLI tokens/sec on short answers. The runner **calibrates** this per CLI provider — a couple of trivial warmup calls at run start, the min latency taken as the startup floor (`runner.py` `_calibrate_startup`) — and `Provider.throughput` subtracts it from latency before computing speed, so CLI tools compare fairly with APIs (overhead 0). Raw latency is still stored as ground truth and the report tags adjusted tok/s with `−Nms`. Toggle with `scoring.speed.calibrate_startup` / `calibration_runs`; or tune `scoring.speed.anchor_tps` / `metric: latency`.

## Conventions when editing

- Keep each test case's prompt self-contained in `question`; don't rely on system prompts or shared context across cases.
- When a case needs a deterministic assertion, constrain the output format in the prompt itself ("Reply with only ...") rather than loosening the matcher.
- Tag every new case with both `domain` and `tier` so the `--filter-metadata` coverage levels keep working. Optionally set `metadata.kind: objective | open_ended` to override the auto-detected case kind.
- A comprehensive run makes many real API calls; with a judge **ensemble** each rubric case costs one call **per judge**, and each open-ended case adds all-pairs pairwise battles (#judges × model-pairs). Prefer `--filter-metadata tier=core` or `--filter-sample` while iterating; the `.cache` makes unchanged re-runs near-free.
