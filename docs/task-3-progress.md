# Task 3 progress: Jev starter and Birch rescue

Task 3 connects the validated Jev Gateway adapter to the PokéBot owner-frame
loop for the starter choice and Birch rescue battle. The emulator thread takes
immutable observations, offers legal semantic choices, and remains neutral
while one process-wide worker performs the HTTP request. A response executes
only if its context, action set, and pause generation still match.

The mode retries timeouts, HTTP 429, and HTTP 5xx responses at most twice.
Authentication and validation failures pause immediately. Pausing invalidates
the pending choice, and closing the mode explicitly closes any suspended input
generator. Battle dialogue advances deterministically; Jev chooses every move
when more than one usable move is available.

Observations expose exact party and opponent HP, status, and the active
Pokémon's known move type, PP, power, accuracy, description, and usability.
They do not expose future RNG or the opponent's unobserved move set. Starter
selection verifies the acquired species and does not perform shiny resets.

`runs/decisions.jsonl` stores sanitized request and response evidence, accepted
decisions, action outcomes, and the battle callback result. Successful stale
responses retain their usage and distribution with disposition `stale` but
never execute. Status and decision records are frozen values. Cost is an
estimate using the Vercel catalog rate and includes its source and checked
date; no credential or HTTP authorization value is logged.

On 2026-09-20, a bounded run from PokéBot's labeled
`in_front_of_starter_pokemon_bag.ss1` fixture used real Jev choices to select
Treecko and use Pound for three turns. The game reported `BattleOutcome.Won`,
set `RESCUED_BIRCH`, and left Treecko level 5, Healthy, at 17/20 HP. The run
finished in 34.00 seconds and a visible Route 101 frame was captured 15 neutral
frames after the win fade. Across the diagnostic and two completed runs, ten
real model requests used 8,390 input and 462 output tokens, with no provider
errors or retries. At the catalog rate checked that day, the aggregate estimate
is USD 0.00035238.

This is checkpoint evidence, not a continuous New Game run. The tracked ROM
integration suite independently resets the same upstream fixture and proves
that the real executor acquires Treecko, Torchic, and Mudkip. The paid rescue
runner is ignored and requires an explicit `TASK3_ALLOW_PAID=1` opt-in; its
historical evidence was not rewritten after telemetry fields were added.
