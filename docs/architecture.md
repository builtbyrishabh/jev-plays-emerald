# Live Pokemon agent architecture

## Product goal

Build a watchable Pokemon agent in which Jev makes the decisions that change the run. The application supplies reliable perception, world knowledge, navigation, and controller skills. Codex is used while developing those capabilities, not while the published run is playing.

The first milestone is a multi-map FireRed slice: Pallet Town, Route 1, and Viridian City. The agent must handle dialogue, wild battles, low party health, healing at the Pokemon Center, visiting the Poke Mart, and returning to Professor Oak.

## Runtime boundary

The live runtime has five parts:

1. **Emulator adapter** reads FireRed RAM and advances mGBA frames. It exposes stable observations rather than screenshots to the decision layer.
2. **World model** identifies the current map and coordinates, builds a graph of map transitions and landmarks, and keeps short run memory.
3. **Candidate generator** derives legal, meaningful goals from current state. It never offers an action the executor cannot perform.
4. **Jev policy** chooses among those goals or among legal battle actions. Its returned distribution is recorded and displayed as model preference, not win probability.
5. **Skill executor** turns the selected goal into verified emulator inputs using pathfinding and menu routines.

The live frontend reads the emulator frame and agent status from the same local process. The game remains the main visual element.

## Jev's role

Jev owns decisions where more than one option can reasonably advance the run:

- continue the story, explore, train, shop, or heal;
- return to a Pokemon Center, consume an item, or accept the risk of continuing;
- select a move, switch, use an item, catch, or run in battle;
- choose a reachable landmark, exit, or NPC when several are relevant;
- recover from an unexpected encounter, failed route, or changed party state.

Jev is not called for frames, individual walking inputs, deterministic dialogue advancement, or singleton action lists.

## World knowledge and navigation

The application maintains a version-specific FireRed world index derived from legal metadata sources and the user-provided ROM at runtime. It contains map identifiers, collision data, warp connections, landmark types, and interaction positions. It does not contain ROM data or copyrighted game assets.

The current RAM state supplies map ID, player coordinates, facing direction, party health and status, inventory, badges, story flags, battle state, and menu state. A map-graph search finds a route between landmarks; tile-level A* finds the path inside each map. Every transition is verified against the new RAM state before execution continues.

For example, when party health is low, the candidate generator can offer:

- continue toward the current story objective;
- use a carried healing item;
- heal at the nearest reachable Pokemon Center.

The state sent to Jev includes health, route distance, known risks, recent failures, and those legal goals. If Jev chooses healing, the executor routes to the selected Center, enters it, interacts with the nurse, and verifies that the party was restored before returning control to Jev.

## Planning without a live Codex hero

The game already exposes finite progression through badges, inventory, event flags, and reachable maps. A deterministic progression frontier turns those facts into currently possible goals. Jev selects which goal to pursue and can change course when the state changes.

An expensive planner is not part of the normal loop. A planner may later be evaluated as a rare recovery mechanism when the agent has repeated failures and the existing skills cannot produce a useful goal. Any such intervention must be visible in telemetry and excluded when measuring Jev-only performance.

## Skill contract

Each skill has the same conceptual contract:

- a precondition that determines when it is legal;
- a semantic description shown to Jev;
- an executor that performs deterministic inputs;
- a postcondition that proves success;
- a bounded failure result that returns control to the policy.

Examples are `navigate_to_landmark`, `cross_warp`, `talk_to_npc`, `heal_party`, `use_overworld_item`, `choose_move`, and `switch_party_member`.

Skills are ordinary application code written and tested during development. The live agent does not modify its own source code.

## First vertical slice

The first slice is complete when a single local command starts the emulator bridge and frontend, and the browser shows a continuous run through Pallet Town, Route 1, and Viridian City. From varied health and position save states, the agent must:

- traverse multiple maps and doors;
- handle dialogue and the parcel objective;
- complete wild battles;
- recognize low party health;
- choose whether to heal or continue;
- navigate to and use the Pokemon Center when selected;
- show Jev's choices, probability distribution, latency, recent decisions, and progress live.

## Evaluation

Every scenario runs with the same observation and skill layers under two policies:

- Jev policy;
- deterministic priority baseline.

We record completion, recovery rate, invalid choices, decisions, latency, token usage, model cost, elapsed game time, and party outcome. Jev earns its place only if it improves meaningful choices or recovery enough to justify its calls.

## Scope limits

The first slice excludes the complete campaign, online self-modifying skills, authentication, a database, cloud deployment, multiplayer, and item-complete strategy. Those are considered only after the first multi-map run is reliable.
