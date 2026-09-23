# Jev Plays Emerald: first playable slice

The runtime now defaults to the first-gym target, with progression and controls
described in [first-gym design and evidence](first-gym.md). The opening design
below is historical; its rival stopping point remains available with
`--target rival`. Normal-input execution, model ownership and completion-proof
requirements still apply.

Current implementation (22 September): Jev chooses gameplay actions, Python
owns observations and normal-input execution, and optional stuck-only Luna
coaching runs through the local TypeScript service and AI Gateway. See
[planner behavior](planner-proposal.md) and [measurement workflow](benchmark.md).
The original design below predates this opt-in coaching path.

## Goal

Run the real Emerald game locally and watch Jev choose its first Pokémon and win the first rival battle. Keep the game prominent, the decision panel small, and dependencies limited to what the existing emulator integration needs.

The required route is New Game/Littleroot setup, Route 101 starter choice and Birch rescue, Birch's lab, Oldale Town, then the rival on Route 103. Birch's rescue battle against Zigzagoon is distinct from the rival battle. The player-name, gender, clock, and nickname defaults are setup configuration; starter selection and battle choices belong to Jev.

## Runtime

```text
mGBA / PokéBot Gen3
  → coherent Emerald observation
  → legal semantic actions
  → Jev Choice when multiple actions exist
  → verified, bounded input routine
  → next decision point

Emulator frames + immutable agent status → local HTTP server → browser
```

Use one application process initially. A single owner advances the emulator and performs all emulator reads/writes. The HTTP thread reads published frames/status and submits a pause/resume flag; it never accesses emulator memory. A background model request allows frames and status to remain responsive while inputs are held neutral. Discard an answer if the action context changed before execution.

## What to reuse

Prefer a pinned PokéBot Gen3 plugin/custom mode over a new emulator framework. Its Emerald-aware RAM, map, navigation, starter, healing, and battle routines are the first reuse targets. Existing automatic battle policies and shiny-reset modes must not silently replace Jev's choices. A short integration probe determines whether the plugin entrypoint can use the built-in HTTP/video support directly or needs a thin local server. See [reuse decisions](reuse.md).

Python remains the application language because these dependencies are Python. Use plain HTML/CSS/JavaScript for the initial viewer; introduce no frontend build system unless the existing viewer requires one. Do not build a second service merely to call Jev.

## Observations and world knowledge

Read the current map ID, tile coordinates, facing, controllability, menus/tasks, party HP/status/moves/PP, items, battle phase, legal targets, and the few opening story flags. Use upstream map/warp/collision readers and object IDs for landmarks in Littleroot, Routes 101/103, Oldale, Birch's lab, and Oldale's Pokémon Center. Do not construct a full-region atlas for this milestone.

Map knowledge and mechanical facts are supplied to the model explicitly. This is structured-state play with known map data; it is not screenshot-only exploration. Do not send hidden opponent move sets or future RNG. Available battle information should match what the run has observed, with exact HP from RAM labeled as such.

Maintain only an in-memory current objective, interrupted objective, recent actions/results, visited landmarks, and failure counts. A local JSONL log captures completed decisions and outcomes. No database is needed.

## Jev and deterministic skills

Jev selects among Treecko/Torchic/Mudkip, useful reachable goals, healing versus continuing, and legal battle actions. Model distributions are preferences over offered labels, not win probabilities. The first battle can have few choices; the UI must not imply that a forced input is a model decision.

Code establishes legal actions from game facts and executes the selected one. Singleton actions and ordinary animation/text advancement bypass Jev and are labeled deterministic. Dialogue confirmation must distinguish ordinary text from a meaningful choice menu. Avoid embedding a complete winning route in the policy; the small opening progression rules expose currently relevant landmarks and the model picks among valid alternatives.

Reused skills include navigate to a reachable map/tile, face/interact with an NPC or bag, choose starter, execute a legal battle action, and heal at Oldale's Center. Each stops on success, battle interruption, new meaningful menu, loss of control, or a bounded failure. Arrival alone does not prove healing; verify party HP/PP/status after the nurse interaction.

If a wild encounter interrupts navigation, switch to the battle decision loop and reevaluate after it ends. A whiteout is recorded as failure/recovery, never a victory or an invisible reload. Repeated movement failures cause a local path recomputation; after a small configured retry bound, pause visibly with the failed action. Do not call Codex to repair the live run.

## Frontend

Serve the emulator image and status on localhost. Prefer upstream streaming if usable; otherwise poll the latest JPEG around 10 times per second and status around 4 times per second. Neither rate controls Jev's decision cadence.

Show game, run/pause state, current goal/action, full available-action distribution, latency, party/opponent HP, recent choices, and milestone progress. Clearly mark deterministic actions, pending calls, unavailable probabilities, API failures, and checkpoint starts. Do not generate a second LLM explanation for every decision. Pause/resume controls take effect without allowing a late model response to press buttons.

## Milestones and evidence

1. Verify emulator integration on this Mac with the matching Emerald ROM, a frame, and live RAM reads.
2. Acquire a starter and complete Birch's rescue using Jev-selected choices.
3. Travel through Oldale and complete the Route 103 rival encounter, including healing and wild-battle interruption.
4. Demonstrate the entire configured opening continuously in the browser.

Completion requires a recorded rival battle win followed by `FLAG_DEFEATED_RIVAL_ROUTE103`, plus a normally acquired starter in the party. Tests can replay explicit local checkpoints but do not count as a fresh run. A preexisting completion flag must not produce a new success event.

Run focused checks at starter selection, in an injured overworld state, during an encounter interrupt, and before the rival. Test the starter executor for all three choices; test at least three continuous runs with varied timing before calling the slice repeatable. Compare Jev with a deterministic legal-action policy on a small matched checkpoint set. Report model calls, tokens/cost when available, failures, and outcome; avoid performance claims from a single lucky win.

## Constraints

- Target unmodified English Emerald SHA-1 `f3ae088181bf583e55daf962a92bb46f4f1d07b7` initially; reject mismatches.
- Target the user's macOS ARM machine first; validate native bindings and Python compatibility before UI expansion.
- No runtime Codex, accounts, database, queues, extra backend services, or self-modifying code.
- No party injection, teleporting, story-flag writes, or silent fallback from Jev to a baseline.
- Keep ROMs, saves, keys, upstream caches, and generated recordings out of Git.
- Pin upstream revisions after the emulator probe and preserve applicable license notices.
- Stop scope at the first rival victory. Full Hoenn, gyms, catching, team-building, and general exploration follow only after it works.

## Initial integration gate (historical)

This is a design, not a verified runtime. The native mGBA/PokéBot integration is the first technical gate. No matching ROM or live API connection has been validated for this project yet.
