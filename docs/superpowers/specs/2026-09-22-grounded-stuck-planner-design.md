# Grounded stuck-planner design

## Goal

Jev remains the player. Luna is silent during normal play and intervenes only
after Jev repeats the same meaningful action three times without observed story
progress. The intervention gives Jev one direct hint, the exact reachable
location to try next, and the game-state change that would prove success.

The planner must improve across fresh runs without learning its own
hallucinations as facts.

## Evidence from the failed run

The latest run made 118 Jev choices and 25 Luna calls without obtaining a
starter. Luna alternated between Birch's Lab, the blocked Route 101 exit and the
player's own house. It eventually told a male player to meet Brendan in
Brendan's House, although May is that player's rival.

The current planner receives raw state, up to 24 attempts and only its previous
advice. Old failures fall out of the window, locations and character
relationships are left implicit, and an unverified suggestion can remain active
until story progress. This lets the same rejected theory return later.

Bulbapedia's Emerald walkthrough confirms the missing opening fact: visit the
neighbor's house, go upstairs, inspect the item on the floor, meet the rival,
then enter Route 101 for Birch's rescue. The source is
<https://bulbapedia.bulbagarden.net/wiki/Walkthrough:Pok%C3%A9mon_Emerald/Part_1>.

## Planner trigger

The existing conservative trigger remains the only way to call Luna:

1. Count a meaningful overworld action by `(progress signature, map, action)`.
2. Ignore forced dialogue, setup and battle turns.
3. Call Luna after the third repetition without story progress.
4. Do not call Luna initially or merely because story progress changed.
5. Clear the active hint and repetition evidence when trusted progress changes.
6. If Jev repeats another action three times while following a hint, Luna may
   refine the hint. That refinement is still a stuck-triggered call.

## Direct decision brief for Jev

Jev receives a small `decision_brief` before raw supporting state:

```json
{
  "current_goal": "Meet the neighbor before entering Route 101",
  "confirmed_facts": [
    "Player is Brendan; May is the rival",
    "May has not left for Route 103"
  ],
  "avoid_repeating": [
    "Talk to lab aide: 6 attempts, no story progress",
    "North exit: 9 attempts, blocked by NeedPokemonTrigger"
  ],
  "legal_actions": [
    {
      "label": "Enter May's House",
      "attempts_without_progress": 0,
      "last_result": null
    }
  ],
  "planner_hint": null
}
```

This brief is assembled mechanically from observed flags, character identity,
legal actions and attempt memory. It does not choose Jev's action. Raw dialogue
and observation data remain available after the brief as evidence.

When Luna has intervened, `planner_hint` has a structured shape:

```json
{
  "hint": "Meet May upstairs before trying Route 101 again.",
  "location": {
    "name": "May's House 1F entrance",
    "map": "Littleroot Town",
    "coordinates": [14, 8]
  },
  "avoid": "Do not enter Brendan's House or retry the north exit yet.",
  "success_signal": "rival_left_for_route103 becomes true"
}
```

The location must match a currently legal action or a named landmark present in
the observation. Luna may not invent coordinates. Jev still receives all legal
alternatives and makes the choice.

## Bulbapedia knowledge

Runtime play will not browse the live site on every intervention. That would add
network latency and another failure mode to the emulator loop. Instead, the repo
contains a small, source-linked and paraphrased knowledge file for the supported
opening slice. Entries are retrieved by current map and trusted progress flags
and supplied to Luna only when it is called.

This is game knowledge, not an authored conditional action rule: code retrieves
relevant facts, while Luna reconciles them with live dialogue, legal actions and
failed attempts to produce the hint.

## Cross-run learning

`runs/planner-memory.json` stores a versioned evidence ledger shared by fresh
profiles. JSON is sufficient for the opening slice and keeps the implementation
inspectable. Writes use a temporary file plus atomic replacement. SQLite is the
next step only if the ledger grows large, needs concurrent writers or covers
multiple games.

Each entry is scoped by ROM, story-progress signature and map. It records:

- actions repeatedly attempted without progress;
- planner hypotheses that were later contradicted;
- an action/hint followed by a trusted story-state change;
- counts and the latest supporting run, never secret model reasoning.

Only an observed progress change can promote a lesson to `verified`. A planner
statement alone is never stored as a fact. Failed and retracted hypotheses remain
available so later calls must not repeat them.

## Planner contract

The TypeScript service returns validated structured JSON rather than free-form
text. The prompt requires Luna to:

1. use the supplied walkthrough facts as reference knowledge;
2. treat live state and dialogue as authoritative;
3. reject locations absent from the current legal actions/landmarks;
4. name the failed theory it is replacing;
5. return one immediate destination, not a multi-map walkthrough;
6. provide an observable success signal;
7. avoid every verified dead end for the current stage.

Python rejects malformed, unavailable or invented locations. If a refinement
fails validation, existing valid advice remains active. If the first planner
call fails, play pauses visibly as it does now.

## Testing and acceptance

Focused tests cover:

- no planner call before three repeated attempts;
- a direct brief with attempt counts and character identity;
- exact locations restricted to observed legal destinations;
- story progress clearing the active hint;
- atomic persistence and reload across fresh planner instances;
- unverified advice never becoming a verified lesson;
- a failed hypothesis being supplied to, and avoided by, the next call;
- the male-player opening resolving May as the rival and May's House as the
  neighbor's house.

A fresh live acceptance run must obtain a starter with authored hints disabled.
The report records Jev decisions, Luna calls, planner advice and tokens. Rival
completion remains useful evidence but is not required for this change.

## Scope

The first knowledge set covers the current opening slice through the Route 103
rival. The data structures can accept later sourced stages, but this change does
not ingest a full-game walkthrough, add a vector database or give Luna general
web access.
