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

## Open-world decisions (flagged)

With `JEV_OPEN_WORLD_SPIKE=1` the hardcoded route is gone: every overworld choice is enumerated from the current map's exits, people and signs, and Jev picks one. Only character creation, the clock menu and mandatory dialogue stay deterministic.

Measured on 21 September 2026, three consecutive fresh New Game runs, each driven through PokéBot's own loop by a local bounded harness rather than the `__main__` launcher.

| Run | Result | Jev decisions | Deterministic decisions | Model calls | Input tokens | Estimated USD |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| fix7 | Rival win, 59.5 s | 55 | 22 | 56 | 108,100 | 0.004540 |
| fix8 | Rival win, 100.2 s | 89 | 22 | 90 | 199,605 | 0.008383 |
| fix9 | Rival win, 116.2 s | 102 | 22 | 103 | 234,881 | 0.009865 |

All three ended with a `Won` callback against the Route 103 rival trainer and a false-to-true `DEFEATED_RIVAL_ROUTE103` flag, with the starter acquired during the run. Every battle in all three runs was won, and no executor action failed. The open-world path costs roughly ten times the scripted route's model calls for the same milestone, because it decides every doorway.

Before the fixes below, no flagged run had ever reached a starter. Each was found by reading the decision log of a real run:

- The truck's three doors are dynamic warps, so upstream named their destination from the save block and told Jev the exit led to Petalburg City. An exit with no recorded destination now claims none.
- The pre-clock instruction described a house while Jev was still in the moving truck.
- A map edge targeted the middle of the neighbouring map. Route 103's middle is across water, so "travel north" out of Oldale could not be pathed at all and was suppressed as impossible. Edges now target the tile just across the border.
- An interruption reported only "unexpected menu: script", so a cutscene and a refusal looked identical. It now names the script, which is how `NeedPokemonTrigger` became visible: Littleroot blocks the north exit until the neighbour has been met.
- Objects the game was not tracking were dropped from the menu, so the rival - eighteen tiles from where you arrive on Route 103 - was not offered at all. Present-but-distant objects are now offered as a walk to their tile, and objects hidden by a story flag are left out.
- Spent PP alone counted as an injury, so a full-health starter was told to heal and went hunting for a nurse it cannot walk up to.
- The observation carried the map as a pair of numbers and no name, so Jev stood on Route 103 and walked back to Oldale to look for Route 103.

Remaining: there is no planner tier. Jev still oscillates when nothing on the menu looks like progress - the winning runs each spent several decisions walking up and down the neighbour's stairs - and the only memory is the last twelve action outcomes. The menu is capped at 24 entries, ordered exits first and then by distance.

## Scope limits

This is an opening slice ending at the first rival victory. It does not implement gyms, catching, or a persistent model-authored plan. The default route is hardcoded; open-world enumeration is behind `JEV_OPEN_WORLD_SPIKE`. Each request receives the current structured game state, the mission, available actions, and up to 12 recent action outcomes. Mechanical navigation and mandatory story progression remain code-driven.

## Final verification

The final local command `DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest tests -q` passed **87 tests and 7 ROM subtests** in 8.51 seconds. Two aiohttp deprecation warnings came from a test helper using a bare function. The final bootstrap also passed its pinned native import and plugin registration probes.

The real local server served `/jev/index.html`, streamed actual game frames, and accepted queued pause/resume controls with observed state changes. Wide (1440×1000) and narrow (560×1100) screenshots were checked against a clearly labeled completed checkpoint. Pending/probability and error presentation also have labeled fixture checks; those fixture screenshots are not gameplay evidence.

After the boot fix, a separate fresh run through the real PokéBot main loop and HTTP server was observed in the browser at `/jev/index.html`. It passed setup, let Jev choose Treecko, rescued Birch, and reached travel/wild battles. The screenshot `.cache/task5-fresh-live-decision.png` shows a real pending decision and model history, confirming the normal launcher → plugin → HTTP → browser path. This additional live launch is separate from the three completed acceptance runs in the table.
