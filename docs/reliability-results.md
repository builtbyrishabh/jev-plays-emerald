# Repeated opening comparison — 22 September 2026

Five fresh hybrid attempts all completed the first rival battle. Two lost a battle, recovered normally, and still completed. No manual rescue or checkpoint reload was used. This meets the five-win acceptance criterion for this suite; it is not a guarantee of future wins.

All 14 attempts used the same executable source, fresh profiles, 900 seconds and 150 accepted choices. The hybrid had eight coaching calls and a 50,000 reported-token stop threshold. Four isolated emulator processes ran concurrently, so wall times are observations, not controlled throughput measurements. RNG and network timing varied.

| Configuration | Rival wins | Known tokens across all attempts | Known tokens per win | Unknown usage events | Mean seconds per win |
| --- | ---: | ---: | ---: | ---: | ---: |
| Jev + Luna coach | 5/5 | 741,404 | 148,281 | 6 | 103.34 |
| Codex Luna alone | 3/3 | 537,868 | 179,289 | 0 | 302.05 |
| Jev alone | 0/3 | 797,448 | — | 2 | — |
| Jev with grounded facts, no coach | 0/3 | 1,289,944 | — | 6 | — |

Both Jev controls exhausted the 150-choice budget. Failures remain in the aggregate. In the report schema, `jev` names the player tier; its model is Luna for the Luna-only arm.

## Hybrid attempts

| Attempt | Jev choices | Luna calls | Known combined tokens | Known Luna tokens | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 60 | 3 | 166,122 | 15,732 | 113.02 |
| 2 | 46 | 3 | 125,337 | 15,821 | 85.63 |
| 3 | 43 | 3 | 120,857 | 15,711 | 83.50 |
| 4 | 43 | 3 | 118,860 | 15,713 | 87.85 |
| 5 | 68 | 6 | 210,228 | 32,010 | 146.68 |

The hybrid used 18 Luna calls across five wins (3.6 per win), versus 116 Luna player calls across three wins (38.7 per win): about 91% fewer Luna calls per completion. Hybrid Luna usage averaged 18,997 tokens per win. Jev retained every non-forced action choice.

Combined reported tokens averaged 148,281 versus 179,289 per completion, about 17.3% lower for the hybrid. Six failed hybrid Jev calls have unknown usage, so this is not exact billing savings. Luna-only varied from about 147k to 243k tokens; the earlier 397k development trial should not be used as the sole baseline. Cached input is already included in input totals.

The evidence supports sparse coaching helping this particular grounded Jev setup escape loops. It does not establish a general model ranking or prove that each hint caused subsequent progress. The intervention log records triggers, advice, whether the suggested action actually started, its executor outcome, and observed story changes. Stale/invalid/error advice remains visible.

## Limits and accounting

`JEV_PLANNER_MAX_CALLS=8` and `JEV_PLANNER_MAX_TOKENS=50000` are configurable per mode instance. The token threshold counts reported input plus output, including stale responses. One in-flight call can cross it; missing usage blocks additional coaching. Jev continues choosing actions when coaching is exhausted. All five live hybrid attempts stayed below the limits; focused tests exercise exhaustion and unknown usage.

Luna ran through saved ChatGPT-authenticated headless Codex, with no separate Luna API fallback. Subscription capacity was consumed. These CLI totals include its prompt overhead and do not measure a bare Luna API implementation. Recorded Jev API estimates across all 14 attempts sum to $0.107337678, excluding unknown usage and engineering-agent usage. This is an estimate at the rate stored in telemetry, not a bill.

## Reproduction and verification

```bash
DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --env-file .env python scripts/benchmark_suite.py \
  --output runs/a-new-comparison-directory --jobs 4
```

Executable source SHA-256: `347867e92751011a6630365b3a63e32f42ddb6799aa519fd9f723f4bd2028489`.
Local evidence: `runs/reliability-20260922/manifest.json`, `report.json`, and each attempt’s decisions, summary, final screenshot and emulator state. The suite exited successfully with `suite_complete: true` and `source_drift: false`. ROMs and raw run artifacts remain ignored.

Validation: 251 Python tests plus seven ROM subtests, TypeScript typecheck, and 18 service tests passed. The final viewer adjustment also passed all 11 viewer tests. Two existing aiohttp deprecation warnings remain. Browser review verified the usage/budget/intervention panels with an explicitly labeled fixture; it was not presented as live gameplay.
