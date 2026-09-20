# Task 4: configured New Game through the first rival

The mode now plays the opening through normal game input. The configured setup
is a boy named JEV, the initial 10:00 AM clock position, and no starter nickname.
It uses pinned PokéBot map enums, navigation, NPC interaction, battle menus, and
Oldale healing. The opening rules expose the next mandatory landmark from
observed flags/maps and the measured rival-house state. Singleton setup,
cutscene, and dialogue actions are explicitly deterministic.

Jev chooses the starter, usable battle moves, legal wild escape attempts, and
healing versus continuing when both are available. The request includes the
current first-rival mission, observations, available goals, and recent outcomes.
There is no runtime Codex, injected party, written story flag, teleport, shiny
reset, or fallback policy. Navigation stops at scripts and encounters, and the
next decision rechecks the current state. Door-animation frames no longer count
as a blocked walking route.

Rival identity requires a trainer battle on Route 103 with one of the six IDs
from the pinned Emerald source: 520, 523, 526, 529, 532, or 535. The reader reads
`gTrainerBattleOpponent_A` on the emulator owner thread. Completion additionally
requires a starter acquired by this mode, the exact candidate's Won callback,
and the rival flag changing from false to true. An already-completed checkpoint,
ordinary wild win, or rival loss does not count. A guard prevents a lingering
battle animation from rearming a candidate after its outcome callback.

On 20 September 2026, the first accepted continuous run started with a fresh
emulator, entered New Game, acquired Treecko through a real Jev response, won
Birch's rescue, handled two wild battles, and defeated rival trainer 532. It
finished with Treecko level 7 at 8/24 HP. The run used 17 real model decisions and
37 deterministic decisions, 25,249 input and 829 output tokens, and an estimated
USD 0.001060458 at the recorded catalog rate. Mean request latency was 605.9 ms.
Its 28.99-second wall time used unthrottled emulation and is not normal game speed.
There were 38 successful actions, 16 expected interruptions, no failed actions,
and no manual input, save reload, or provider retry during this run.

Two earlier development attempts paused visibly: the first counted a door
animation as a blocked walk; the second repeatedly declined Birch's mandatory
request to meet the rival. Both were fixed before the accepted run. Earlier
manual exploration and local checkpoints were development aids, not acceptance
runs. Their history remains in ignored `.cache/task4-run*.log` files.

The real ROM healing check loads the naturally injured accepted-run checkpoint,
uses `heal_in_pokemon_center(PokemonCenter.OldaleTown)`, returns to Oldale, and
asserts full HP, full PP for every move, and Healthy status. The test skips with a
clear message if that local checkpoint is absent. Unit checks cover loaded
completion flags, wild wins, rival losses, post-outcome animation, legal running,
and whether healing is relevant. Two more continuous runs under different frame timing also completed. The slower
run lost its first rival attempt, returned home after whiteout, resumed normal
travel, chose Oldale healing through Jev, and won the rematch. No manual input or
reload was used. Across the three accepted runs: 56 real model decisions, 117
deterministic decisions, 83,964 input and 2,742 output tokens, and USD 0.003526488
estimated cost. Mean request latency was 616.6 ms; no provider retries occurred.
A real-emulator check with an explicitly simulated 401 failure paused at starter
selection and verified 20 further neutral frames without selecting a Pokémon.

A small labeled baseline reused the same pre-bag checkpoint as Task 3. Choosing
Treecko and the move with the highest listed base power won Birch's rescue in
three Pound turns, matching Jev's basic checkpoint success. This is one simplified
baseline comparison, not evidence that either policy is better. The healing rule
was not exercised in that comparison. See [results](results.md) for the combined
acceptance report; ignored `.cache/task4-report.md` holds the local evidence paths.

The runtime intentionally stops after the first rival. Later routes, catching,
gyms, and general exploration are outside this slice. Checkpoint loading does
not recreate this mode's in-memory proof of starter acquisition or rival victory.
