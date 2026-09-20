# Jev Plays Emerald

Watch Jev play a real Pokémon Emerald game. Jev chooses meaningful goals and battle actions; existing emulator and bot routines execute the inputs.

## First milestone

**Choose a starter → rescue Professor Birch → visit his lab → travel through Oldale Town → win the first rival battle on Route 103.**

The browser will show the live game beside Jev's available actions, selected action, model probabilities, latency, recent decisions, and progress. Jev can choose Treecko, Torchic, or Mudkip and decide whether to heal or continue when those choices are available.

## Current status

Repository setup and planning only. The emulator, agent loop, and frontend are not implemented or verified yet.

- [Implementation plan](docs/superpowers/plans/2026-09-20-emerald-starter-rival.md)
- [Architecture and acceptance criteria](docs/architecture.md)
- [Existing tools and reuse decisions](docs/reuse.md)

## Intended stack

- **mGBA + PokéBot Gen3** for emulation, Emerald RAM reading, maps, pathfinding, and input routines. Validate the macOS ARM setup first.
- **Python**, to reuse that ecosystem without a separate Node service.
- **Jev's structured Choice API** for starter selection, local goals, and battle choices.
- **One local server and a small HTML/JavaScript page** for frames, status, and pause/resume controls.

No accounts, database, queues, or live Codex planner. Codex helps develop skills; the shipped gameplay loop runs existing skills selected by Jev.

## Inputs needed before live testing

- A user-provided, unmodified English Emerald ROM matching the supported revision. Initial target SHA-1: `f3ae088181bf583e55daf962a92bb46f4f1d07b7`.
- A TypeSafe API key for real Jev calls.
- Local save states generated during testing. ROMs, saves, credentials, and recordings stay outside Git.

The eventual run command and dependency versions will be documented after the emulator compatibility check passes. There is no claimed working demo command yet.

## What counts as success

A continuous visible run from the configured New Game opening acquires a starter through normal gameplay and wins the Route 103 rival battle, with real Jev decisions recorded. Tests may start from labeled checkpoints; they do not substitute for the continuous run. Completion is verified from battle outcome and Emerald's story flag, not from generated narration.

This proves the control-loop concept. The short opening sequence alone will not establish that Jev is a stronger player than deterministic rules. We will compare selected checkpoint runs using the same executor and a simple baseline.
