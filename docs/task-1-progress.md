# Task 1 progress: live emulator gate passed

The user supplied `roms/Pokemon - Emerald Version (USA, Europe).gba` in the main repository. It is 16,777,216 bytes and matches the required SHA-1 `f3ae088181bf583e55daf962a92bb46f4f1d07b7`. No ROM was downloaded or added to Git.

## Verified on this host

Running `python3.13 scripts/bootstrap.py` on macOS 26.6.2/arm64 with Python 3.13.4 produced:

```text
PokéBot revision: 5dd898f830775d448b06db6f5cd65b930540f146
libmgba-py archive: 0.2.0-2/libmgba-py_0.2.0_macos-arm64.zip (abd90c6e4fd98d2a0bffbda16d5b94fa6944654b61353d4592af7b4b4a3484b7)
libmgba-py=<ignored checkout>/mgba/__init__.py
pokebot-emulator=modules.libmgba.LibmgbaEmulator
plugin-mode=Jev Emerald
```

The negative ROM test was observed failing before `verify_rom` was implemented, then passing. The no-input battle test was observed failing while the inherited hook returned `None`, then passing after the mode returned `BattleAction.CustomAction`. An actual `BattleListener` trainer regression test then failed because upstream rewrote that action to `Fight`; it passed after the pinned bootstrap patch preserved explicit custom actions. The custom-state publication test likewise failed before the plugin published its namespaced status. Profile metadata also has a focused test proving legal ROM filenames containing spaces, `#`, `:`, and newlines survive YAML serialization.

The first native import exposed the missing `libmgba.0.10.dylib`. Following the pinned upstream macOS instructions, `brew install mgba` installed mGBA 0.10.5_2. The import then passed with `/opt/homebrew/lib` on `DYLD_LIBRARY_PATH`. The bootstrap records this loader path for its probes, and the launcher passes it to PokéBot.

## Verified live checks

The protected-profile check completed before starting the emulator:

```text
ROM verified; profile ready: jev-emerald
```

The real ROM booted through mGBA 0.10.5 and reached `TITLE_SCREEN`. Capturing 60 images from `/stream_video?fps=15` produced 28 unique SHA-256 hashes, demonstrating advancing frames. The title-screen capture is stored locally at the ignored path `screenshots/task1-title-screen.png` with SHA-256 `08fa65b427f6de334262264dd9b78ee58eb66fc797d8066034f2b23ef035eeab`. The upstream root viewer also showed the title screen after clicking **Start Video**. `/custom_state` returned:

```json
{"jev_emerald": {"mode": "Jev Emerald", "status": "idle"}}
```

For the first state comparison, the run used pinned upstream fixture `tests/states/emerald/new_game_inside_player_house.ss1`, SHA-256 `25156e988b41fdf98d1618d0d03b6ab0bc67dc2f2c65c3d46887cd13cf62c75e`, copied into a separate profile named `jev-task1-overworld-upstream`. This was a labeled checkpoint run, not a continuous New Game run. Owner-thread HTTP reads reported:

```text
game_state=OVERWORLD
map=[1, 2] Littleroot Town, May’s House (1F)
position=[5, 6], facing=Right
party=[]
input=[]
```

The captured room frame visibly matched the indoor map and player position. It is stored locally at `screenshots/task1-overworld-upstream-checkpoint.png`, SHA-256 `1e680a1b4993fae9a467945d17cb7facc7345a37080842ab21f66b1117b0750f`.

For the battle-control check, the run used pinned upstream fixture `tests/states/emerald/in_tall_grass_after_receiving_pokeballs.ss1`, SHA-256 `9fe28d54b3b32dcedbf6829f1d6c3c9fbbf6c6543d80026256cb0db3cc8129f2`, in separate profile `jev-task1-battle-upstream`. Manual mode supplied five ordinary left/right grass steps. On `BATTLE_STARTING`, input was released and the mode was changed to `Jev Emerald`. Before and after a five-second observation window:

```text
game_state=BATTLE
bot_mode=Jev Emerald
input=[]
Torchic Lv6 HP=23
Wurmple Lv2 HP=14
```

The stable battle frame is stored locally at `screenshots/task1-no-input-battle.png`, SHA-256 `3702f5461cb0b53a4e6455c0f5a591fc6f29193b4c365bf669562723228fd214`. This live result agrees with the focused real-`BattleListener` regression test: no default battle strategy took over.

Task 1's live integration gate is passed. The fixture runs do not claim the later continuous configured New Game acceptance run. There is no model setup or model request path in Task 1, so wrong-ROM validation necessarily happens before any future model call.
