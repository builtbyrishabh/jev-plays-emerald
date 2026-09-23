# First-gym mission

**Status:** Implementation and control tests are complete; an autonomous first
badge is **not proven**. At the user's stopping point, fresh runs had reached
Petalburg, and a separate ROM probe had completed Wally's tutorial. Further
live gameplay testing is stopped, with all run logs and checkpoints preserved.

The app targets the Stone Badge in Rustboro. Jev chooses the actions and Luna
can coach after repeated actions or sustained decisions without story progress.
It is not called merely because a milestone or gym was reached. `--target rival`
retains the original benchmark objective.

## Run

With the existing bootstrap, ROM and gateway key:

```sh
JEV_PLANNER_MODEL=openai/gpt-5.6-luna \
  uv run --env-file .env python -m jev_plays_emerald \
  --rom "roms/Pokemon - Emerald Version (USA, Europe).gba" \
  --profile first-gym-run --target first-gym
```

Open <http://127.0.0.1:8888/jev/index.html>. A new profile preserves previous
saves. The first-gym coaching defaults are 32 calls and 200,000 reported tokens;
override with `JEV_PLANNER_MAX_CALLS` and `JEV_PLANNER_MAX_TOKENS`. Missing usage
stops further coaching rather than counting it as free. Jev continues choosing.

## Capabilities

- Progress continues after the rival through the Pokédex, Wally's tutorial,
  Petalburg Woods and Rustboro. Nearby map exits remain distinct even when they
  reach different parts of the same named map.
- Local healing covers Oldale, Petalburg and Rustboro, including entering a
  center yourself. Shop purchases and field healing use normal menu inputs.
- Meaningful yes/no, fainted replacements, move replacement and evolution
  decisions have legal actions. Wally's demonstration stays game-controlled.
- A training action seeks an encounter in reachable grass. Jev then chooses
  battle actions or catches through the existing battle controls.
- Both models receive bounded Gen III species types, effectiveness, upcoming
  moves and level evolutions from pinned PokéBot data. This is reference
  knowledge, not access to hidden opponent moves or future random outcomes.

The viewer leads with the mission, milestones, party HP, badge progress and
current action. Token counts and action preferences are expandable. Submitted
manual actions count calls through the agent controls; direct emulator inputs
are not observable by that counter. Pause/resume is not a gameplay action.

## Proof and reproduction

A badge counts as completed only after observing the first Roxanne battle win
in Rustboro Gym and the Stone Badge becoming set. Loading a badge checkpoint
is labeled separately and cannot produce a new win. The scripts establish
[`BADGE01_GET`](https://github.com/pret/pokeemerald/blob/master/data/maps/RustboroCity_Gym/scripts.inc),
[`PETALBURG_GYM_STATE >= 2`](https://github.com/pret/pokeemerald/blob/master/data/maps/PetalburgCity_Gym/scripts.inc)
and [`PETALBURG_WOODS_STATE >= 1`](https://github.com/pret/pokeemerald/blob/master/data/maps/PetalburgWoods/scripts.inc).

For a bounded fresh run that records requests, outcomes, screenshots, milestone
checkpoints and a final summary:

```sh
DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run --env-file .env python scripts/benchmark.py \
  --variant hybrid --target first-gym --output .cache/first-gym-new-run \
  --seconds 1500 --max-decisions 1000 --max-planner-calls 32 \
  --max-planner-tokens 200000
```

The benchmark's default target remains `rival` for comparison with earlier
results. `luna-only` remains an explicit benchmark variant, never a silent
fallback player. Failed or budget-limited runs remain in their own directories.

## Evidence

Development runs are preserved under `.cache/first-gym-hybrid-20260922-*`:

| Run | Observed outcome | Fix prompted by the run |
| --- | --- | --- |
| 1 | Starter acquired; training lookup error after 44 choices | Reject temporary coordinates outside the current map; do not cache transient failures. |
| 2 | Rival beaten and Pokédex received; stopped after 179 choices | Replace unreachable midpoint border targets with reachable crossings; stop retaining completed destinations as current advice. |
| 3 | Rival beaten and Pokédex received; ordinary trainer battle stalled after 71 choices | Upstream trainer-approach dialogue must release control when a custom battle starts. |
| 4 | Starter acquired; nickname screen stalled after 27 choices | Match the actual starter script and confirm the species default if a naming screen is already open. |
| 5 | Rival beaten, Pokédex received, route trainers beaten, Petalburg reached and party healed; stopped after 160 choices | Rank nearby people alongside exits and read loaded NPC positions, so distant gym doors do not hide the relocated Norman. |
| 6 | Stopped after 12 choices while the loaded-NPC fix was being verified | Superseded development attempt; not evidence for the final controls. |
| 7 | Paused after 86 choices when the provider returned an inconsistent choice/probability result | Malformed provider responses now receive at most two fresh retries; rejected choices are never executed. |
| 8 | Stopped at the user's stopping point after 28 choices | Final controls loaded; this short attempt does not establish later-game progress. |

These runs are incomplete and were not resumed to claim fresh-game success.
Run 3 also wrote a misleading Woods milestone image during an early map
transition. It is excluded from progress evidence: the captured scene is the
player's house. The reader now preserves coherent story data during map
transitions, and milestone capture waits for a normal state and saves before
advancing the emulator frame.

On 23 September, the final no-authored-route configuration received one fresh
120-decision rival verification run with no manual actions or runtime errors. It
reached May's house but did not meet her, exhausted the eight-call Luna budget,
and stopped at the decision limit. This is a failed completion attempt and shows
that the opening is not yet reliable with the current general prompt.

A separate ROM probe from run 5's checkpoint verifies Norman's relocated
position, talking to him, Wally's battle, and returning to the gym with the
tutorial flag set. This required 12,188 emulator frames and is a control test,
not an autonomous fresh-run result. The previous 12,000-frame test budget was
too short; the tutorial control itself did not require a change.

## Final checks and next step

- Python: **334 tests and 14 ROM subtests passed**; two existing aiohttp warnings.
- TypeScript checking and **19 service tests passed**.
- Desktop/mobile viewer fixtures were inspected; these are UI evidence, not a
  completed gameplay recording.
- The exact prior native runtime patch upgrades successfully in an isolated
  clone, and the local launcher accepts the installed pinned runtime.

The next useful experiment is one bounded first-gym run with these final
controls, measuring milestone progress and route loops. Do not claim a badge,
full-game completion, or a reliability percentage from the development runs.
