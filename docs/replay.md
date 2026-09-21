# Replaying recorded decisions

Runs are long and the model is stochastic, so "that instruction change helped"
is an impression unless it is measured. A decision log already stores the whole
request - state, menu and instructions - so a situation Jev has been in can be
put to it again with the wording changed, and the two answers compared.

```bash
uv run --env-file .env python -m jev_plays_emerald.replay runs/decisions.jsonl \
    --saying "new neighbour" --target walk:0:9:14:8 --variant mission --verbose
```

The recorded answer is the baseline and costs nothing; only the variants call
the gateway. The header prints the number of calls and an estimate based on the
recorded token sizes, and `--dry-run` stops there and shows one rewrite.

## Variants

| Spec | What it asks |
| --- | --- |
| `recorded` | the logged answer, for comparison |
| `current` | recompute the instructions from the recorded state with today's code - "did my edit help?" |
| `mission` | the base mission with every situational hint removed |
| `hint:<text>` | the mission plus this hint instead of the recorded one |
| `drop:<phrase>` | the recorded instructions minus any sentence mentioning the phrase |

`--variant` repeats. Filters narrow the corpus to the situations a question is
about: `--map 0,9`, `--kind walk`, `--saying <text in the instructions>`,
`--limit`. Repeats of the same situation collapse to one call by default -
a stuck run records the same spot dozens of times - and `--all` keeps them.

`--target <id prefix>` scores the mean probability mass on the actions you
consider correct, which is a steadier signal than the single chosen action.

## What it cannot answer

The state replays exactly as recorded, including the twelve recent outcomes.
So this measures wording against wording, holding history fixed. A question
about what Jev would do *after different history* - "would it still get there
if it had already been refused twice?" - needs a live run, because no recorded
state carries a history that never happened.

## Findings so far

Measured 21 September 2026 over the open-world logs.

**The Littleroot gate hint is worth about eleven times fewer decisions.** The
town refuses to let you north until you have met the neighbour. Replaying the
six distinct situations that carried that hint, with history held fixed:

| Instructions | Chose the neighbour's door | p(door) |
| --- | --- | ---: |
| Recorded (names the neighbour's house) | yes | 1.00 |
| The previous hint ("head north to Birch's bag") | no, walked into the blocked exit | 0.00 |
| Mission only, no hint | no, went to the lab | 0.10 |

A live run with that hint removed still won the rival, so the hint is not
load-bearing - but it took 466 decisions to reach a starter against 41 with it,
absorbing 96 refusals from the blocked exit on the way, and cost USD 0.034
against 0.0045. Naming the script that refuses ("NeedPokemonTrigger") is enough
for Jev to eventually escape the gate, and not enough for it to work out what
the game wants.
