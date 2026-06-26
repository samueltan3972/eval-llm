# eval-llm — LLM cost/speed/quality comparison (promptfoo)

Runs the **same multi-domain test suite across multiple LLMs** and reports **cost, speed
(latency), and output quality** side-by-side. Built on [promptfoo](https://www.promptfoo.dev/)
so you get a working benchmark (and a GUI) without building a harness from scratch.

What it demonstrates:

- **Multi-model, side-by-side** — providers are columns, test cases are rows.
- **Cost** and **speed** — surfaced per call automatically.
- **Reference-based scoring** — deterministic assertions (`equals`, `contains`, `regex`,
  `is-json`/`contains-json` with schemas, and `javascript` that *executes* generated code).
- **LLM-as-judge** — `llm-rubric` assertions graded by a configurable model. The grader is
  **blind** (never sees which provider produced the answer) and **swappable** (one line in
  `defaultTest.options.provider`).

## Prerequisites

- **Node.js 18+** (promptfoo runs via `npx`; nothing to install globally).
- **An Anthropic API key.** The suite uses two Anthropic models, so a single key runs
  everything. A full run makes many real API calls (see cost note below).

### API keys via a `.env` file (no manual `export`)

promptfoo automatically loads a `.env` file from this directory, so you can store keys in a file
instead of exporting them each session. Copy the template and fill it in:

```bash
cp .env.example .env
# then edit .env:  ANTHROPIC_API_KEY=sk-ant-...
```

That's it — every `npx promptfoo` / `npm run` command below picks it up automatically. `.env` is
gitignored, so your real keys are never committed.

- Custom location? `npx promptfoo@latest eval --env-file path/to/keys.env ...`
- Prefer exporting instead? `export ANTHROPIC_API_KEY=sk-ant-...` still works and takes
  precedence over `.env`.

## Run it

```bash
# comprehensive — all domains (~63 cases x each model)
npx promptfoo@latest eval -c promptfooconfig.yaml -o runs/results.html -o runs/results.json

# then open the GUI dashboard
npx promptfoo@latest view
```

Or use the npm convenience scripts (`npm install` first to get the pinned promptfoo):

```bash
npm run eval         # comprehensive
npm run eval:core    # balanced core subset
npm run eval:sample  # quick random 8-case smoke test
npm run view         # open the GUI
```

`runs/results.json` holds the raw data (per-cell token usage, cost, latency, assertion scores);
`runs/results.html` is a standalone report.

## Coverage levels (choose at run time)

The suite is one set of files; you select how much to run with `--filter-metadata`. Every test
case is tagged `metadata.domain` and `metadata.tier` (`core` | `extended`).

| Goal | Command |
|------|---------|
| **Comprehensive** (all ~63 cases) | `npx promptfoo@latest eval` |
| **Balanced core** (~32 cases) | `npx promptfoo@latest eval --filter-metadata tier=core` |
| **Single domain** | `npx promptfoo@latest eval --filter-metadata domain=code_generation` |
| **Core of one domain** | `npx promptfoo@latest eval --filter-metadata tier=core --filter-metadata domain=math` |
| **Quick smoke** (random N) | `npx promptfoo@latest eval --filter-sample 8` |

## Test domains (`datasets/*.yaml`)

| Domain | What it tests | Scoring |
|--------|---------------|---------|
| `math` | arithmetic, word problems, unit conversion | reference (exact/regex) |
| `logic_reasoning` | deduction, sequences, trick questions, commonsense | hybrid |
| `closed_book_qa` | stable factual knowledge | reference |
| `reading_comprehension` | extractive + inferential over a passage | hybrid |
| `classification` | sentiment, topic, intent, NLI | reference |
| `extraction_structured` | text → JSON validated against a schema | reference (`is-json`) |
| `summarization` | faithful summaries with length/format limits | judge + `javascript` |
| `instruction_following` | exact format/length/forbidden-word constraints | reference (`javascript`/`regex`) |
| `code_generation` | writes a function that is **executed** against test inputs | `javascript` + judge |
| `code_understanding` | explain / debug / predict output / complexity | hybrid |
| `faithfulness_grounding` | answer only from context; admit "I don't know" | hybrid |
| `creative_writing` | quality + constraint adherence | judge |
| `translation_multilingual` | translate; respond in a target language | judge + keyword |
| `safety_refusal` | refuses harmful AND doesn't over-refuse benign | judge |
| `long_context_retrieval` | needle-in-a-haystack over a long passage (extended only) | reference |

Add your own cases by editing any file (a YAML list of `{description, vars.question, assert,
metadata}`), or add a new `datasets/<name>.yaml` — it's picked up automatically by the glob.

## GUI / web dashboard

promptfoo includes a web GUI — no extra setup. After a run:

```bash
npx promptfoo@latest view     # or: npm run view
```

It shows the **providers × tests grid** (cost, latency, pass/fail, judge grades per cell),
**charts** (pass rates, score distributions, head-to-head scatter), **filter/search**, per-cell
**drill-in** (full output, manual scores, comments), a **history of past runs**, and a
**run-to-run diff**. You can edit-and-re-run an eval from the UI, and `share` a URL. New evals are
authored in `promptfooconfig.yaml` + `datasets/*.yaml`.

## Cost note

A comprehensive run is ~63 cases × each model, plus a grader call for every `llm-rubric`/judge
case. To keep iteration cheap: use `--filter-metadata tier=core` or `--filter-sample`, and/or set
the grader to a cheaper model (`anthropic:messages:claude-haiku-4-5` in
`defaultTest.options.provider`). promptfoo caches results, so re-runs of unchanged cells are free.

## Add a third model / another provider

Edit `promptfooconfig.yaml`, uncomment the OpenAI provider, and add its key to `.env`:

```bash
# in .env
OPENAI_API_KEY=sk-...
```

promptfoo supports 60+ providers (OpenAI, Google/Gemini, Hugging Face, local/OpenAI-compatible
servers like GLM, Sakana, vLLM, Ollama) plus custom provider plugins — so adding a model is a
config change, not a rewrite.

## Not yet built (next steps)

- **Judge ensemble** — combine multiple graders: add several `llm-rubric` assertions, each with a
  different `provider`, and average the scores.
- **Baseline win/tie/loss** vs. a standard model — use the `select-best` pairwise assertion plus
  a small script over `runs/results.json`.
- See `~/.claude/plans/i-want-to-have-quirky-feather.md` for the full design and the alternative
  paths (Python-native inspect-ai/DeepEval, or a from-scratch custom harness).
