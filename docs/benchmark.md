# Measure the player and coach

`scripts/benchmark.py` runs a fresh New Game using the production observation,
legal-action, executor and listener loop. Each run requires a new output folder,
has a time/decision budget, and saves its final state, screenshot, summary and
raw JSONL. It never loads a checkpoint to claim completion.

The default target is `rival`, preserving the original comparison. Use
`--target first-gym` for the Stone Badge mission and larger explicit budgets;
see [first-gym verification](first-gym.md). The harness records milestone images
and checkpoints without loading them during the run; Luna is called only after
the normal stall triggers.

```bash
DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --env-file .env python scripts/benchmark.py \
  --variant hybrid --output .cache/benchmark-hybrid-1 --seconds 900 --max-decisions 150
```

Repeat with `--variant jev-only` and `--variant luna-only`, each in its own new
folder. Jev-only runs without Luna. Hybrid uses Jev plus stuck-only Luna.
Luna-only replaces the player for comparison using the same legal menu plus its
legacy opening reference facts, and does not invent probability
distributions. The normal application always keeps Jev as the player.

All arms use the same high-level mission without an authored route and start with
empty planner memory. All arms disable failed-action suppression, so repeated failures
do not silently remove choices in only one configuration. The hybrid's grounded
brief includes live legal actions and attempt counts; Jev-only is explicitly an
uncoached ablation, not an identical-prompt model comparison. Luna-only gets the
opening reference facts. Fresh runs
share the mission and mechanics but not exact RNG or network timing.

Use `--variant jev-grounded` for the stronger control: it gives Jev the same
grounded brief and attempt counts as the
hybrid, but never calls Luna. This helps separate context improvements from
coaching. Luna-only's actual input with added reference facts is also retained in
`baseline-requests.jsonl` by current harness versions.

```bash
uv run python -m jev_plays_emerald.report \
  .cache/benchmark-hybrid-1/decisions.jsonl \
  .cache/benchmark-luna-1/decisions.jsonl --json
```

Count `response` and `planner-response` events, including stale responses.
`decision` events duplicate selected usage and must not be added again. Unknown
usage and unresolved requests are reported separately. Cached input is already
included in input, not additional tokens. Luna usage is not priced using Jev's
rate. Suites recorded before September 23 ran Luna through the Codex CLI, whose
token totals include CLI prompt overhead and are not comparable to API runs.

Compare rival completion, losses/recovery, elapsed time, model calls, known input
and output tokens, and unknown usage. Record failed and budget-limited runs too.
Repeat each arm before claiming reliability or a general savings percentage.

## Repeated comparison suite

```bash
DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --env-file .env python scripts/benchmark_suite.py \
  --output .cache/comparison-2026-09-22 --jobs 3
```

The defaults schedule five hybrid runs and three runs of each control, all fresh,
with 900 seconds, 150 decisions, and coaching limits of eight calls/50,000 known
tokens per run. Each attempt has a separate console log and result folder.
`--hybrid-runs`, `--control-runs`, `--seconds`, `--max-decisions`,
`--max-planner-calls` and `--max-planner-tokens` override these fixed suite settings.

By default, at most three child processes run concurrently; `--jobs` changes
that limit (`--jobs 1` runs serially). Attempt
order in `report.json` follows the manifest schedule rather than finish order;
five successful hybrid attempts establish the five-win criterion only when the
suite is complete. Concurrent runs share local CPU and provider capacity, so their
latency is observational, not a controlled throughput comparison. RNG and network
timing are not fixed across fresh games.

The immutable `manifest.json` records git HEAD, a hash of current executable source
including dirty files, the ROM hash, runtime versions, models, budgets and job
count. Secrets, environment files, dependencies and ROM contents are excluded from
the source hash. Do not edit executable source while the suite runs: drift stops
new model runs, lets already-started attempts finish, and marks the aggregate
`source_drift: true`. An existing suite directory is rejected; there is no resume
or overwrite path.

`report.json` updates after every attempt and retains failures in known tokens per
successful completion. It separates missing usage from known totals, reports
per-tier counters, stop reasons, the longest hybrid success streak, battle losses
and runs that completed after a loss. A generic battle win never counts as rival
completion; only the production mode's explicit `run-end` completion does. A crash
without a run-end remains a failed attempt. Partial-suite streaks are provisional.
