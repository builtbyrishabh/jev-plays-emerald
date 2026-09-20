# Reuse decisions — 20 September 2026

## Recommended foundation

Use mGBA/libmgba through a pinned PokéBot Gen3 checkout, with our own plugin and mode. Python 3.13 is the first runtime target; the upstream bootstrap lists 3.11–3.13 and provides an Apple Silicon download. This is source-level compatibility evidence, not proof that the combination has booted on this machine.

Reviewed PokéBot revision: `5dd898f830775d448b06db6f5cd65b930540f146`.

| Need | Reuse first | What remains ours |
| --- | --- | --- |
| GBA emulation and frames | [libmgba wrapper](https://github.com/40Cakes/pokebot-gen3/blob/5dd898f830775d448b06db6f5cd65b930540f146/modules/libmgba.py) | Small launch/configuration bridge |
| Native macOS setup | [requirements.py](https://github.com/40Cakes/pokebot-gen3/blob/5dd898f830775d448b06db6f5cd65b930540f146/requirements.py) | Reproducible bootstrap and compatibility check |
| Custom agent mode | [BotPlugin hooks](https://github.com/40Cakes/pokebot-gen3/blob/5dd898f830775d448b06db6f5cd65b930540f146/modules/plugin_interface.py) | Jev mode, battle delegation, completion checks |
| Location, party, game phase | [player.py](https://github.com/40Cakes/pokebot-gen3/blob/5dd898f830775d448b06db6f5cd65b930540f146/modules/player.py), [pokemon_party.py](https://github.com/40Cakes/pokebot-gen3/blob/5dd898f830775d448b06db6f5cd65b930540f146/modules/pokemon_party.py), [memory.py](https://github.com/40Cakes/pokebot-gen3/blob/5dd898f830775d448b06db6f5cd65b930540f146/modules/memory.py) | Compact immutable observations and decision-context IDs |
| Pathfinding | [walking.py](https://github.com/40Cakes/pokebot-gen3/blob/5dd898f830775d448b06db6f5cd65b930540f146/modules/modes/util/walking.py) | Opening landmarks, explicit door/warp handling, interruption handling |
| Starter selection | [starters.py](https://github.com/40Cakes/pokebot-gen3/blob/5dd898f830775d448b06db6f5cd65b930540f146/modules/modes/starters.py), [upstream starter tests](https://github.com/40Cakes/pokebot-gen3/blob/5dd898f830775d448b06db6f5cd65b930540f146/tests/test_mode_starter.py) | Select Jev's option once; do not invoke the shiny-reset loop |
| Healing and NPC interaction | [higher_level_actions.py](https://github.com/40Cakes/pokebot-gen3/blob/5dd898f830775d448b06db6f5cd65b930540f146/modules/modes/util/higher_level_actions.py) | Verify healing postconditions and preserve the interrupted objective |
| Live browser transport | [HTTP server](https://github.com/40Cakes/pokebot-gen3/blob/5dd898f830775d448b06db6f5cd65b930540f146/modules/web/http.py): `/stream_video`, `/custom_state`, static assets | Small game-first page and safe publication/control integration |
| Jev battle protocol | [anxkhn/JevPlaysPokemon](https://github.com/anxkhn/JevPlaysPokemon) | Emerald legal candidates and direct Python API client |
| Goal/action separation | [milanboers/jev-plays-pokemon](https://github.com/milanboers/jev-plays-pokemon) | Reuse the idea; its Red/PyBoy memory addresses do not apply to Emerald |

The upstream video endpoint already emits multipart PNG frames; try that before building a JPEG/WebSocket/WebRTC pipeline. `/custom_state` can carry the decision panel's data. Use the native input routines rather than driving the desktop window.

PokéBot's `navigate_to` covers connected maps and obstacle handling. It is not proof of arbitrary cross-door navigation. Test the actual house/lab/Center warps and nurse sequence; do not promise that a generic destination alone handles every transition.

## Why these tools

- [mGBA](https://github.com/mgba-emu/mgba) supports GBA, macOS, save states, and scripting. PokéBot adds the Pokémon-specific work we would otherwise have to write.
- [PyBoy](https://github.com/Baekalfen/PyBoy) is a Game Boy/Game Boy Color emulator; Emerald is a GBA game.
- [BizHawk](https://github.com/TASEmulators/BizHawk#macos-legacy-bizhawk) is capable of GBA automation, but its current macOS limitations make it a weaker first choice for this ARM Mac.
- Browser emulators and raw mGBA Lua are alternatives if the integration probe fails. Neither is an improvement for this milestone if it requires replacing existing Emerald state/navigation code.
- Do not use Showdown as the acceptance test: it does not demonstrate the actual Emerald opening.

## Emerald story evidence

Reviewed `pret/pokeemerald` revision: `5eff78649e7170a877b961ef0b3da13b81a16038`.

- [ROM identity](https://github.com/pret/pokeemerald/blob/5eff78649e7170a877b961ef0b3da13b81a16038/rom.sha1): `f3ae088181bf583e55daf962a92bb46f4f1d07b7`.
- [Route 101](https://github.com/pret/pokeemerald/blob/5eff78649e7170a877b961ef0b3da13b81a16038/data/maps/Route101/scripts.inc): Birch's rescue scene and Zigzagoon.
- [Birch's lab](https://github.com/pret/pokeemerald/blob/5eff78649e7170a877b961ef0b3da13b81a16038/data/maps/LittlerootTown_ProfessorBirchsLab/scripts.inc): starter/story progression.
- [Route 103](https://github.com/pret/pokeemerald/blob/5eff78649e7170a877b961ef0b3da13b81a16038/data/maps/Route103/scripts.inc): May/Brendan encounter selected according to player/starter state; post-battle script sets `FLAG_DEFEATED_RIVAL_ROUTE103`.
- [Oldale Center](https://github.com/pret/pokeemerald/blob/5eff78649e7170a877b961ef0b3da13b81a16038/data/maps/OldaleTown_PokemonCenter_1F/scripts.inc): healing destination and nurse script.

Use these as format/symbol/event references. Load game data from the user's ROM through upstream readers; do not bundle graphics, music, ROMs, or saves.

## Integration checks before committing to the runtime

1. Boot the exact ROM on Python 3.13/macOS ARM with upstream bindings.
2. Load our mode through the plugin hook and prove that default battle automation cannot override it.
3. Read starter/menu/task state, one overworld position, and one battle state correctly.
4. Verify browser frames remain responsive during a pending model call.
5. Determine the smallest way to serve our page and status from the existing server; change only the integration seam if necessary.
6. Pin the working dependencies and record reproducible commands.

PokéBot and the FireRed reference carry GPL-3.0 licenses. Preserve their notices and document imported code when implementation begins. No third-party implementation has been copied into this repository by this planning change.
