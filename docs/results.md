# Emerald opening results

Measured locally on 20 September 2026 using the supported English Emerald ROM, Apple Silicon macOS, the pinned PokéBot/mGBA runtime, and real `typesafe-ai/jev` requests through Vercel AI Gateway.

## Continuous gameplay

Fresh profiles start New Game and use normal inputs only. No party/story/RNG writes, save reloads, or manual gameplay inputs were used in these continuous runs. Fixed setup uses the male player JEV, the default clock, and no nickname. Jev chooses the starter, battle actions, and healing versus continuing; mandatory progression and dialogue are deterministic.

| Run | Added delay per frame | Result | Jev decisions | Deterministic decisions | Mean request latency | Input / output tokens | Estimated USD |
| --- | --- | --- | ---: | ---: | ---: | --- | ---: |
| development3 | 0 ms | Rival win, 28.99 s | 17 | 37 | 605.9 ms | 25,249 / 829 | 0.001060458 |
| acceptance2 | 0.1 ms | Rival win, 24.69 s | 9 | 37 | 602.3 ms | 12,979 / 395 | 0.000545118 |
| acceptance3 | 0.3 ms requested | Loss, automatic recovery, then rival win; 316.04 s | 30 | 43 | 627.0 ms | 45,736 / 1,518 | 0.001920912 |

Times are accelerated emulator wall times, not normal-speed play times. Input sampling/RNG and network latency differ across runs; elapsed time is not a model-quality score. The first successful run finished with Treecko level 7 at 8/24 HP. All three wins were checked against the actual Route 103 rival trainer identity, the `Won` callback, and a false-to-true `DEFEATED_RIVAL_ROUTE103` flag, with a starter acquired during the run.

Costs use the explicit catalog rate recorded in telemetry: USD 0.042 per million input tokens and zero per output token, checked 2026-09-20 at the [Vercel model catalog](https://ai-gateway.vercel.sh/v1/models). These are estimates, not billed amounts. Earlier starter-checkpoint calls and unsuccessful development runs are separate from the table; see [Task 3](task-3-progress.md).

The three accepted runs total 56 model decisions and 117 deterministic decisions, 83,964 input / 2,742 output tokens, and USD 0.003526488 estimated cost. They recorded 122 successful actions and 51 expected interruptions (dialogue, map transitions, or battles), with no failed executor actions or provider retries. Interrupted navigation is reevaluated rather than blindly resumed.

## Failures and recovery

The final real-launcher check also exposed an uninitialized game state before the first frame. The reader now returns a neutral observation until Emerald initializes, without decoding uninitialized RAM; a focused regression guards this boot boundary.

Two early fresh development attempts paused on implementation bugs: doorway animations were mistaken for blocked movement, and Birch's mandatory repeated Yes/No dialogue was not accepted. Both were corrected before `development3`. The logs retain these failures; the successful runs do not include reloads or manual rescue.

The third timing variation naturally lost its first rival battle. The `Lost` callback left completion false and the rival flag unset; normal whiteout recovery returned the player home. The same run continued without reloads or manual help, chose Oldale healing, and won the rival rematch with Treecko level 7 at 10/24 HP. All three continuous runs ultimately completed; the third did not win on its first rival attempt. macOS sleep scheduling made its nominal 0.3 ms frame delay substantially longer, so its wall time is not directly comparable to the unthrottled run.

## Focused checks and baseline

- All three starter executors acquire the correct species from the same labeled pre-selection checkpoint using normal inputs.
- A real doorway reaches the expected map/coordinates; a blocked tile fails safely; a controlled-seed wild encounter interrupts movement and releases inputs. That seeded test is separate from continuous runs.
- The naturally injured `development3` checkpoint reaches Oldale Center using the reused healing helper, returns outside, and restores HP, PP, and status.
- Completion tests reject wild wins, unrelated trainers, rival losses, and a rival flag already present in a loaded save.
- Model-response validation and the actual mode loop cover invalid distributions, timeouts, stale responses, and pause during pending requests. A real-emulator pre-starter check with an explicitly simulated HTTP 401 paused without acquiring a starter or pressing inputs during 20 subsequent frames; this is injected provider failure, not a live gateway outage.
- An explicitly labeled deterministic baseline completed the same upstream pre-bag starter/Birch checkpoint used by Task 3. Its declared policy selects Treecko, heals below half HP when available, otherwise follows the current story goal, and chooses the highest listed move base power. In the matched checkpoint it selected Pound, won in three turns, and finished at 16/19 HP. This narrow checkpoint exercises starter and battle choices, not the baseline healing rule or a general damage simulator. It made no model calls or invented distributions. This small comparison establishes that the same input skills work with either decision source; it does not establish Jev superiority.

Local evidence is under ignored `.cache/task4-*` directories and logs. ROMs, save states, recordings, and raw decision logs are not committed. Tracked tests skip ROM/checkpoint checks with a reason when the required local assets are unavailable.

## Scope limits

This is a guided opening slice ending at the first rival victory. It does not implement general exploration, gyms, catching, or a persistent model-authored plan. Each request receives the current structured game state, the mission, available actions, and up to 12 recent action outcomes. Mechanical navigation and mandatory story progression remain code-driven.

## Final verification

The final local command `DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest tests -q` passed **87 tests and 7 ROM subtests** in 8.51 seconds. Two aiohttp deprecation warnings came from a test helper using a bare function. The final bootstrap also passed its pinned native import and plugin registration probes.

The real local server served `/jev/index.html`, streamed actual game frames, and accepted queued pause/resume controls with observed state changes. Wide (1440×1000) and narrow (560×1100) screenshots were checked against a clearly labeled completed checkpoint. Pending/probability and error presentation also have labeled fixture checks; those fixture screenshots are not gameplay evidence.
