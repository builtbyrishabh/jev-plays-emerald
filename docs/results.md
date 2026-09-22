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

## Open-world decisions

Every overworld choice is enumerated from the current map's exits, people and signs, and Jev picks one. Only character creation, the clock menu and mandatory dialogue stay deterministic. This became the default on 21 September 2026, replacing an if/else route through Littleroot, and the `goal:` action kind it needed was deleted with it.

Measured on 21 September 2026, three consecutive fresh New Game runs behind the flag that then became the default, each driven through PokéBot's own loop by a local bounded harness rather than the `__main__` launcher.

| Run | Result | Jev decisions | Deterministic decisions | Model calls | Input tokens | Estimated USD |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| fix7 | Rival win, 59.5 s | 55 | 22 | 56 | 108,100 | 0.004540 |
| fix8 | Rival win, 100.2 s | 89 | 22 | 90 | 199,605 | 0.008383 |
| fix9 | Rival win, 116.2 s | 102 | 22 | 103 | 234,881 | 0.009865 |

All three ended with a `Won` callback against the Route 103 rival trainer and a false-to-true `DEFEATED_RIVAL_ROUTE103` flag, with the starter acquired during the run. Every battle in all three runs was won, and no executor action failed. Deciding every doorway costs roughly ten times the model calls the deleted route needed for the same milestone.

Before the fixes below, no flagged run had ever reached a starter. Each was found by reading the decision log of a real run:

- The truck's three doors are dynamic warps, so upstream named their destination from the save block and told Jev the exit led to Petalburg City. An exit with no recorded destination now claims none.
- The pre-clock instruction described a house while Jev was still in the moving truck.
- A map edge targeted the middle of the neighbouring map. Route 103's middle is across water, so "travel north" out of Oldale could not be pathed at all and was suppressed as impossible. Edges now target the tile just across the border.
- An interruption reported only "unexpected menu: script", so a cutscene and a refusal looked identical. It now names the script, which is how `NeedPokemonTrigger` became visible: Littleroot blocks the north exit until the neighbour has been met.
- Objects the game was not tracking were dropped from the menu, so the rival - eighteen tiles from where you arrive on Route 103 - was not offered at all. Present-but-distant objects are now offered as a walk to their tile, and objects hidden by a story flag are left out.
- Spent PP alone counted as an injury, so a full-health starter was told to heal and went hunting for a nurse it cannot walk up to.
- The observation carried the map as a pair of numbers and no name, so Jev stood on Route 103 and walked back to Oldale to look for Route 103.

At the time of these runs there was no planner tier. Jev still oscillated when nothing on the menu looked like progress; the winning runs each spent several decisions walking up and down the neighbour's stairs. Those runs used twelve recent action outcomes. The menu is capped at 24 entries, ordered exits first and then by distance.

## Fresh review run — 22 September 2026

The current default open-world path completed a fresh New Game-to-Route-103-rival
run in 47.2 seconds using the existing bounded PokéBot-loop harness. Jev chose
Torchic. The run recorded 43 model decisions, 22 deterministic decisions and
zero failed executor actions. This is accelerated emulator wall time, not
normal-speed gameplay. The harness reported verified rival completion; no
planner model or manual gameplay intervention was used. Local evidence is in
`.cache/openworld-review-20260922/decisions.jsonl` and its final screenshot/save.

Two recurring decision errors remain despite completion:

- In the neighbour's upstairs room, the hint says to talk to a child who is
  absent from the offered objects. The menu offers the rival's Poké Ball;
  Jev instead traverses the stairs repeatedly while the neighbour state stays 2.
- Already on Route 101, Jev still receives the instruction to leave the house
  and head north. It tries the south exit three times, receives
  `Route101_EventScript_PreventExitSouth`, then chooses Birch's bag.

These are planning/context failures, not failed navigation: an executed action
can succeed mechanically while making no story progress. The experimental LLM
intervention is described in [planner behavior](planner-proposal.md); this
baseline run did not use it.

Current checks: 161 Python tests plus 7 ROM subtests pass; TypeScript typecheck
and all 9 service tests pass.

## LLM planner experiment — 22 September 2026

With `JEV_PLANNER_MODEL=openai/gpt-5.6-sol`, a separate fresh run completed the
rival battle in 330.9 seconds. Jev chose Treecko and all non-forced actions;
the planner supplied advice only. Authored situational hints and the old
repetition-based action suppression were disabled for this run. It lost the
first rival battle, recovered through normal gameplay and won the rematch.
No manual gameplay input, checkpoint reload or scripted combat policy was used.

| Fresh run | Result | Jev decisions | Deterministic decisions | Planner calls | Repetition corrections | Failed executor actions |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Authored hints (`review-20260922`) | Rival win, 47.2 s | 43 | 22 | 0 | — | 0 |
| LLM advice (`planner-20260922-v3`) | Rival loss, recovery, win; 330.9 s | 75 | 23 | 17 | 9 | 7 |

These are individual stochastic runs with different starters, battles and
timing, not a controlled model-quality comparison. The planner integration
works, but these results do **not** establish fewer mistakes, lower cost or
faster completion than authored hints.

The planner run used 144,689 Jev input tokens / 7,533 output tokens and 59,832
planner input tokens / 4,922 output tokens, reported separately. Planner costs
are not estimated using Jev's token rate. Its 17 accepted calls comprise the
initial objective, seven story updates and nine repetition-triggered corrections.
Local evidence is `.cache/openworld-planner-20260922-v3/decisions.jsonl`,
`final.png` and `final.ss1` in the same directory.

Repeated doorway attempts exposed a pre-existing executor limitation: a walk
can report success at the target doorway tile without leaving the map. The
planner eventually suggested repositioning through the stairs, and the run
continued. Seven failed executor attempts also included NPC targeting/path
errors. Better verification of the effect promised by an action is a concrete
next step; it should not be replaced with more confident planner instructions.

Two development attempts are separate from the successful run: the first
paused safely when a protocol bug routed a planner request as a Jev request
(fixed and covered by a transport test); the second was stopped after 69.6 s
to replace exact-action advice with persistent objectives and conditional
steps. These attempts are not completion evidence.

The normal launcher was also started with a separate fresh profile,
`planner-viewer-20260922`. The real video, LLM advice panel, call count and Jev
choices were inspected in the browser. That ongoing viewer run is separate
from the completed bounded harness run above.
It paused at May's house when the AI SDK rejected a Jev choice that was not a
highest-probability option. Resume requested a fresh decision; that intervention
is viewer testing, not part of the uninterrupted completion evidence above.

Final checks: 172 Python tests plus 7 ROM subtests, TypeScript typecheck, and
all 10 service tests pass. Two existing aiohttp test-helper warnings remain.

## Scope limits

This is an opening slice ending at the first rival victory. It does not implement gyms or general Hoenn progression. Catching is exposed when balls and a wild opponent are available, but is not exercised by these opening runs. Jev receives the current structured game state, mission, menu and up to 12 recent outcomes. The optional LLM planner also receives 24 map-scoped attempts and its previous advice; without it the authored hints remain active. Mechanical navigation and mandatory dialogue remain code-driven. See [replaying decisions](replay.md) for the effect of authored hints.

## Final verification

The final local command `DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest tests -q` passed **87 tests and 7 ROM subtests** in 8.51 seconds. Two aiohttp deprecation warnings came from a test helper using a bare function. The final bootstrap also passed its pinned native import and plugin registration probes.

The real local server served `/jev/index.html`, streamed actual game frames, and accepted queued pause/resume controls with observed state changes. Wide (1440×1000) and narrow (560×1100) screenshots were checked against a clearly labeled completed checkpoint. Pending/probability and error presentation also have labeled fixture checks; those fixture screenshots are not gameplay evidence.

After the boot fix, a separate fresh run through the real PokéBot main loop and HTTP server was observed in the browser at `/jev/index.html`. It passed setup, let Jev choose Treecko, rescued Birch, and reached travel/wild battles. The screenshot `.cache/task5-fresh-live-decision.png` shows a real pending decision and model history, confirming the normal launcher → plugin → HTTP → browser path. This additional live launch is separate from the three completed acceptance runs in the table.
