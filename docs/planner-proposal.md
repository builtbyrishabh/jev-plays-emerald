# LLM assistance while Jev remains the player

Status: experimental implementation. Set `JEV_PLANNER_MODEL` to enable the LLM
planner; leave it unset to use the previous authored hints. The two modes are
explicit: planner errors pause, with no silent fallback to authored advice.

```bash
cd service && pnpm install && cd ..
JEV_PLANNER_MODEL=openai/gpt-5.6-luna uv run --env-file .env python -m jev_plays_emerald \
  --rom "roms/Pokemon - Emerald Version (USA, Europe).gba" --profile planner-demo
```

Use a new profile name for a fresh run. The planner setting automatically enables
the existing TypeScript service. Both models use `AI_GATEWAY_API_KEY`.

## Smallest useful experiment

Keep the legal action menu and executors. Disable situational route hints in an
explicit experimental mode and let Jev reason from game dialogue. Ask the
planner only after three repeated overworld attempts without story progress.
Its correction remains in context until the story advances. Jev continues to
select every non-forced action, including the starter, navigation and combat.
The planner cannot press buttons, remove legal alternatives, or supply Jev's
probability distribution.

Use the existing TypeScript decision service and AI Gateway for the planner
request. Run it on the model worker, never on the emulator frame thread. Keep
the existing pause/context checks when accepting a response. Display planner
advice separately from Jev's action distribution, with model identity and call
count. Persist the actual planner request and response in the decision log.

## What counts as three mistakes

Count finished attempts, including failures and interruptions, not frames. Match
the source map and action ID in the last 24 attempts since accepted advice or a
story change. Local NPC IDs alone are insufficient because they repeat across
maps. Three uses of an unchanged action trigger a correction, even when those
actions succeeded: stairs can succeed on every attempt and still form a loop.
This heuristic can flag useful repeated exploration; the LLM receives the
evidence to decide whether correction is actually necessary.

Do not count forced dialogue or battle actions. Reset the counter when relevant
progress happens: opening flags, rival-house/lab variables or party species.
After an
intervention, require three new non-progress attempts before another call; do
not repeatedly charge for the same history window.

The planner receives the current objective, map and position, dialogue, available
action labels, attempt counts, refusal reasons, local walkthrough facts, verified
past lessons and the last correction. It returns bounded JSON with a short hint,
one currently offered action ID, exact location, an action to avoid, and an
observable success signal. Python rejects destinations outside the current menu.
Code does not execute model-written text or completion predicates.

After Jev takes the exact first action, its old destination is marked completed
instead of remaining a current target. The rest of the hint stays as forward-only
guidance across map transitions until trusted story state changes. This lets a
single intervention express “enter May's House, then go upstairs” without pulling
Jev back to the entrance. Three new non-progress attempts are required before
another planner call.

Evidence lives in versioned, atomic `runs/planner-memory.json`. Failed or
interrupted repeated actions become map-scoped dead ends; only a followed hint
that precedes observed story progress becomes verified. Repeated unverified
hypotheses are merged. A database is unnecessary for one local process and one
ROM, but would be appropriate for concurrent workers or cross-game storage.

Planner mode disables the old repetition-based action suppression: Jev keeps
the same mechanically enumerated menu (still capped at 24 entries). The planner
can advise, but cannot make the corrected action the only legal choice.

The viewer shows model, call count, trigger and latest advice. JSONL
`planner-request`, `planner-response` and `planner-error` events retain the
context, result, staleness disposition, latency and token usage separately from
Jev's events. Planner tokens do not use Jev's price estimate. Calls have a
30-second deadline; an error pauses the run and Resume attempts planning again.

## Evidence to replay first

The 22 September review run exposed two useful cases:

1. May's upstairs room: the hint requests a child absent from the menu, while
   the rival's Poké Ball is available. Jev repeatedly chooses the stairs.
   A planner can propose inspecting that object to trigger the introduction;
   the game outcome must confirm whether that hypothesis worked.
2. Route 101: the bag is already available, but the hint still describes leaving
   the house. Three south-exit attempts encounter `PreventExitSouth`. A planner
   should acknowledge the refusal and focus the objective on the bag nearby.

Compare the current hints with LLM objectives on these recorded decisions,
then run a fresh game. Report time to starter, time to rival, repeated
non-progress decisions, Jev calls, planner calls and failures. One replay can
test a correction's immediate effect; it cannot establish full-run reliability.

## Going beyond Route 103

The current runtime deliberately stops at rival completion and exposes no
overworld actions once that flag is set. Continuing requires changing that
boundary and mission, then checking the next gameplay capabilities. Planning
alone cannot add missing shop/money handling, overworld item use, meaningful
dialogue choices, or healing outside Oldale.

Expand one milestone at a time, beginning with returning to Birch's lab for the
Pokédex. Add capabilities only when the next milestone needs them. Carry forward
observed facts and failed approaches so the planner need not rediscover them.

## Post angle

Jev can already choose a starter and beat the first rival in real Emerald.
The surprising failure is that successful actions can still mean no progress:
walking upstairs and downstairs works perfectly while accomplishing nothing.
Authored hints also go stale: telling Jev to talk to someone absent from its
menu encourages more wandering.

We added occasional LLM coaching: after three repeated attempts without story
progress, the planner reads what happened and proposes a new objective. Jev
still chooses what to do. A fresh run reached a rival victory with 75 Jev
decisions and 17 planner calls, including nine corrections and recovery from a
rival loss. It was slower than our authored-hint baseline, so this is evidence
of recovery, not an efficiency win yet.

The grounded run now verifies doorway transitions, retains what worked, and keeps
multi-step guidance after the first exact action. In a fresh live run, five Luna
calls got Jev through the clock, May's introduction, Birch's rescue, a Treecko,
and to the Route 103 rival. Jev lost that optional battle with Torchic at 3 HP.
This is evidence of sparse recovery through the requested starter milestone, not
a claim that planning makes every decision better.
