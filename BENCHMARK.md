# benchmark — quality + speed + cost scoring for LLMs

A small Python harness (`benchmark/`) that runs your **own** private test suite
(`datasets/*.yaml`) across multiple models and reports **three independent 0–100 scores —
quality, speed, cost — plus an equal-weighted overall (33/33/33)**, saving every run for
later comparison.

It exists because public benchmarks can be gamed, and because promptfoo (kept here for
unit-test-style runs) gives only pass/fail and reports **$0 cost for CLI tools** — it can't
see token usage from a subprocess. This harness measures latency and tokens on every call,
so cost and speed are first-class results, and turns graded assertions into a comparable
rating instead of a pass/fail.

## Quick start

```bash
pipenv install --dev                                   # one-time: create the venv
pipenv run python -m benchmark run --dry-run           # no spend; exercises the whole pipeline
pipenv run python -m benchmark run                     # full suite across all configured models
pipenv run python -m benchmark run --by-domain         # + per-domain quality table
```

Coverage selection mirrors the dataset metadata (`domain`, `tier`):

```bash
pipenv run python -m benchmark run --filter tier=core
pipenv run python -m benchmark run --filter domain=code_generation
pipenv run python -m benchmark run --models codex,antigravity --sample 8
pipenv run python -m benchmark run --no-cache          # force fresh measurements
```

Inspect and compare saved runs:

```bash
pipenv run python -m benchmark leaderboard             # latest run's table (terminal)
pipenv run python -m benchmark compare runs/<A>/run.json runs/<B>/run.json
pipenv run python -m benchmark run --open              # run + open the HTML report
pipenv run python -m benchmark report --open           # (re)build + open the latest report
pipenv run python -m benchmark dashboard               # cross-run Streamlit leaderboard (see below)
```

### Per-run report vs. cross-run dashboard

Two distinct views, deliberately separated:

- **Per-run report** (HTML + JSON) — a **point-in-time snapshot** of one run with **fixed-anchor**
  0–100 scores, so a run is self-contained and reproducible.
- **Cross-run dashboard** (`python -m benchmark dashboard`, Streamlit) — pools every `runs/*.json`
  and ranks models with a **relative** methodology: raw **and** baseline-relative speed/cost,
  per-dimension sortable tables, **weight sliders** for a live composite re-rank, trends over runs,
  and a per-run head-to-head view. All aggregation lives in `benchmark/leaderboard.py` (pure +
  unit-tested); the dashboard is a thin UI. Set a reference model with `baseline:` in
  `benchmark.yaml` (falls back to the cheapest/fastest model in the pool).

### HTML report

Every run writes a **self-contained HTML report** (`runs/<timestamp>__<label>.html`) — open it
in any browser, no server needed; it works offline and can be shared or committed. It has the
leaderboard with score bars (incl. `vs fastest` and `vs cheapest` multipliers), a radar chart per
model across quality/speed/cost, a per-domain quality heatmap, a **head-to-head** section for
open-ended cases (per-model win-rate + the pairwise win matrix), and click-to-expand **per-case
drill-in** — the model's output, every assertion's score, the **per-judge ensemble breakdown** with
its disagreement spread, and the case's pairwise win-weight. Charts are inline SVG and all output is
HTML-escaped. Regenerate one from a saved run with `python -m benchmark report [run.json]`.

## How the three scores work

| Score | From | Default normalization (fixed anchors, set in `benchmark.yaml`) |
|-------|------|----------------------------------------------------------------|
| **Quality** | mean of each case's assertion scores (0–1), ×100 | none — it's already 0–100 |
| **Speed** | tokens/sec (output ÷ *startup-adjusted* latency) | linear vs `speed.anchor_tps` (50 tok/s → 100), capped |
| **Cost** | average USD/call | **linear & absolute**: free → 100, dropping evenly to 0 at `cost.zero_score_usd_per_call` |
| **Overall** | weighted mean of the three | equal weights → the 33/33/33 average |

Anchors are **fixed, not relative to the other models in a run**, so scores stay comparable
across runs as new models arrive. Raw metrics (latency, tok/s, $/call, tokens) are always
saved alongside the scores as the ground truth.

### Assertions → quality (graded, not pass/fail)
Reuses your existing `datasets/*.yaml` assertion types:
- **Deterministic** (`equals`, `regex`, `contains`, `contains-all`, `is-json`, `contains-json`)
  → 0 or 1.
- **`javascript`** → runs the snippet in Node via a small bridge, so code-generation cases
  that *execute* the generated function work unchanged. Requires `node` on PATH.
- **`llm-rubric`** → a **blind judge ensemble** (each judge sees only the output + rubric, never
  which model produced it), returning 0–1. Quality = **mean** of the judges' scores, which
  de-biases any single grader; the per-judge spread is recorded as a disagreement signal.
  Configure the ensemble with `judges:` in `benchmark.yaml`.

A case's quality is the **mean** of its gradeable assertions. A model that errors scores 0 on
that case; `latency`-type asserts are ignored (the harness measures speed itself).

### Hybrid quality: objective vs. open-ended cases
Every case has a **`kind`** — auto-detected, overridable via `metadata.kind: objective | open_ended`:
- **objective** (no `llm-rubric`) → graded right/wrong as above.
- **open_ended** (has an `llm-rubric`, or tagged) → graded **two** ways. (1) The ensemble **rubric
  mean** is the absolute quality number used per-run *and* on the cross-run leaderboard. (2) The
  models' answers are also compared **pairwise** ("better/worse", not "right/wrong") by the same
  ensemble — shown blind as "Answer 1/2", with presentation order swapped across the ensemble to
  cancel position bias. Each model's **average win-weight** becomes a per-run head-to-head /
  win-rate. Pairwise is *relative* to the models in that run, so it's a per-run signal only — never
  the cross-run quality. (All-pairs ensemble battles multiply judge calls; the cache and
  `--sample`/`--filter` keep iteration cheap.)

## Cost: score vs. raw vs. multiplier

Cost shows up three ways on the leaderboard, so the bounded score never hides the real price:

- **`Cost` (0–100)** — **linear and absolute**: a free model = 100, dropping evenly to 0 at
  `cost.zero_score_usd_per_call` (default **$1.00/call**). Cheaper always scores strictly higher,
  the same price always gives the same score (comparable across runs), and it's bounded so it can
  be weighted equally in the overall. At $1 most real models sit ~99–100, so this column is a
  coarse guard — lower `zero_score_usd_per_call` if you want it to separate models sooner.
- **`$/call`** — the **raw, absolute** average cost (unbounded). This is the direct "A is 3×
  pricier than B" number, and it's what stays comparable across runs.
- **`vs <baseline>`** — a **within-run multiplier**. In the terminal it's "vs cheapest"
  (cheapest = `1.00×`); in the **HTML report** the baseline is a **dropdown** — pick any model and
  every multiplier recomputes live (selected model = `1.00×`, a free baseline shows `–`).

**Speed mirrors this.** The HTML report adds a **`vs fastest`** column (fastest model = `1.00×`),
and the cross-run dashboard expresses both speed and cost as **baseline-relative** ratios vs the
configured `baseline:` model (falling back to the fastest/cheapest in the pool).

### Cost for CLI tools (the important caveat)

CLI tools expose no token usage, so for them token counts are **estimated** with a tokenizer
(`tiktoken`, falling back to a character heuristic) and cost = estimated tokens × your
per-model `pricing`. Estimated cost is marked with `~` everywhere. **Set `pricing` in
`benchmark.yaml` to each model's real prices** so the cost comparison is meaningful.
HTTP API models return real usage, so their cost is exact.

**Speed for CLI tools** would otherwise be unfairly low because the wall-clock latency includes
fixed process startup (CLI boot, auth, handshake) that dominates short answers. To compare CLI
tools fairly with APIs, the runner **calibrates** each CLI provider's startup once per run — a
couple of trivial warmup calls, the **minimum** latency taken as the startup floor
(`_calibrate_startup`) — and `Provider.throughput` subtracts it from latency before computing
tokens/sec, so speed reflects *generation*, not boot (APIs have an overhead of 0). Raw latency
is still stored unmodified, and the HTML report tags an adjusted rate with `−Nms`. Control it
with `speed.calibrate_startup` (default `true`) and `speed.calibration_runs` (default 2); you can
still also tune `speed.anchor_tps` or switch `speed.metric: latency`.

## Configuration

Everything lives in `benchmark.yaml`: the `models` (each `type: cli` or `type: api`, with
`pricing`), the `judges:` ensemble (a list; a single `judge:` is still accepted for back-compat),
an optional `baseline:` reference model for the dashboard, the `scoring` weights/anchors, and the
suite glob. API keys come from `.env` (loaded automatically).

**Naming a model.** Each entry has:
- `name` — the stable **internal id/key**. It's what `--models` selects, what the response
  cache keys on, and what `compare` matches across runs. Keep it unique (so you can run several
  models through one CLI tool, e.g. `codex-55` and `codex-mini`).
- `model` — the **real model** being benchmarked, shown in the leaderboard/report as
  `model (tool)`, e.g. `GPT-5.5 (codex)`. If omitted, the report falls back to `name`.
- `tool` *(optional)* — the driver label; defaults to the command's binary (`codex`, `agy`) or,
  for APIs, the `api` value (`openai`/`anthropic`).

Because reports key on `name`, you can relabel `model` later without breaking `compare` against
older runs.

**CLI commands are inlined directly — no external `.sh` wrapper required.** Two forms:

- **Plain command** (default): runs *without* a shell; `{prompt}` is substituted as a single
  argument, so a prompt's quotes/backticks/newlines can't break quoting.
  ```yaml
  command: "agy --model gemini-3.1-pro --prompt {prompt}"
  ```
- **Shell mode** (`shell: true`): for pipes, redirects, `;`, or reading a file. The prompt is
  passed via the `$PROMPT` env var (configurable with `prompt_env`) and is **never**
  interpolated into the command string, so it stays injection-safe even with a shell:
  ```yaml
  shell: true
  # Stream codex's final message to stdout via fd 3 (chatter -> /dev/null): no temp file, so
  # it's race-free per process and keeps file I/O out of the timed path (no speed bias).
  command: 'codex exec --model gpt-5.5 --output-last-message /dev/fd/3 "$PROMPT" 3>&1 >/dev/null 2>&1'
  ```

The `run-*.sh` wrappers still work if you prefer them (`command: "./run-codex.sh {prompt}"`).

## Output files

Each run gets its own timestamped folder under `runs/`:

- `runs/<timestamp>__<label>/run.json` — per-case results (incl. model output, per-assertion
  reasons, per-judge ensemble breakdown, and each case's pairwise win-weight), per-model aggregates
  + scores + win-rate, the `head_to_head` matrix, the `judges`/`baseline` used, the suite hash, and
  the resolved config. The cross-run dashboard reads these.
- `runs/<timestamp>__<label>/report.html` — self-contained HTML report (see above), beside its JSON.
- `runs/<timestamp>__<label>/leaderboard.md` — that run's table plus a run-history index, written
  **inside the run's folder** so each run is a complete, immutable snapshot (nothing at the `runs/`
  root is overwritten between runs).
- `.cache/` — response + judge cache (gitignored); identical re-runs are near-instant.

## Tests

```bash
pipenv run pytest
```
