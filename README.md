# Fighter ICE

A live Pokemon agent where Jev makes consequential gameplay decisions and deterministic code handles emulator control, navigation, menus, and verification.

The first vertical slice is:

> Pallet Town -> Route 1 -> Viridian City -> Pokemon Center -> Poke Mart -> return to Professor Oak

It will include wild battles and health-aware recovery so Jev must decide whether to continue, use an item, or return to a Pokemon Center.

## Status

The repository is initialized and the architecture is documented. No ROM or Nintendo game assets are included. See [the architecture](docs/architecture.md) for the runtime boundaries and first milestone.

## Principles

- Jev decides what meaningful goal or battle action to take.
- Deterministic skills execute movement, menus, dialogue, and emulator inputs.
- Codex helps build and test skills during development; it is not part of the live gameplay loop.
- The browser shows the real emulator feed, Jev's available choices, probabilities, latency, recent decisions, and progress.
- Every model decision is compared with a deterministic baseline so Jev's contribution is measurable.

