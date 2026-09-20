# Task 1 setup: pinned Emerald runtime

Task 1 targets Apple Silicon macOS and Python 3.13. The bootstrap keeps PokéBot, its native binding, ROM links, profiles, and generated state under the ignored `.cache/` directory.

## Prerequisites

Install [uv](https://docs.astral.sh/uv/) and mGBA 0.10.x, then run the bootstrap from this repository:

```bash
brew install mgba
python3.13 scripts/bootstrap.py
```

The script refuses to replace an existing checkout at another revision, unrelated tracked upstream edits, a changed plugin, or changed native files. It checks out PokéBot Gen3 at `5dd898f830775d448b06db6f5cd65b930540f146`, applies and verifies the narrow patch in `patches/pokebot-custom-trainer-action.patch`, installs the locked Python environment, verifies the native archive before extraction, and probes both the mGBA import and the real upstream plugin loader.

Pinned native artifact:

- Release: `hanzi/libmgba-py` tag `0.2.0-2`
- Archive: `libmgba-py_0.2.0_macos-arm64.zip`
- SHA-256: `abd90c6e4fd98d2a0bffbda16d5b94fa6944654b61353d4592af7b4b4a3484b7`

The ignored upstream checkout retains its GPL-3.0 `LICENSE`. The libmgba-py files retain their MPL-2.0 source notices. No upstream source is copied into this repository.

## Verify and launch

Place a user-supplied ROM in an ignored location such as `roms/pokemon-emerald.gba`. The launcher accepts only the unmodified English Emerald SHA-1 `f3ae088181bf583e55daf962a92bb46f4f1d07b7`.

Validate the ROM and prepare a new local profile without starting the emulator:

```bash
.venv/bin/python -m jev_plays_emerald --rom roms/pokemon-emerald.gba --check
```

Start PokéBot headlessly in the `Jev Emerald` mode:

```bash
uv run --env-file .env python -m jev_plays_emerald --rom roms/pokemon-emerald.gba
```

The `.env` file must define `AI_GATEWAY_API_KEY`. `Jev Emerald` makes model
requests only when two or more semantic actions are legal. It keeps emulator
input neutral while one request is pending, retries only timeouts, HTTP 429,
and HTTP 5xx responses twice, then pauses visibly. Requests and responses are
written to `runs/decisions.jsonl` without HTTP headers or credentials.

Then open <http://127.0.0.1:8888/>, click **Start Video**, and use the upstream viewer. The underlying endpoints are:

- Video: <http://127.0.0.1:8888/stream_video?fps=15>
- Plugin status: <http://127.0.0.1:8888/custom_state>

Chrome may block the multipart video endpoint when it is opened as a top-level page; the root viewer embeds it correctly.

The launcher creates the `jev-emerald` profile only when it does not exist. On later runs it validates its metadata and HTTP configuration and refuses to overwrite differences or any save data. Pass `--profile NAME` to create a separate profile, including an explicitly named checkpoint profile.

`Jev Emerald` clears held buttons while awaiting a decision. Its battle hook returns upstream `BattleAction.CustomAction`. Pinned PokéBot otherwise replaces that result with its default strategy for every trainer battle, so the bootstrap applies a one-condition patch that preserves an explicit custom action. A focused test exercises the real `BattleListener` trainer branch and confirms that it does not enqueue the default fight controller.

## Tests and measured host evidence

Run the focused test after bootstrapping:

```bash
DYLD_LIBRARY_PATH="$(brew --prefix mgba)/lib" .venv/bin/python -m pytest tests/test_observation.py -q
```

Run the tracked, ROM-backed starter executor checks after bootstrapping:

```bash
DYLD_LIBRARY_PATH="$(brew --prefix mgba)/lib" .venv/bin/python -m pytest \
  tests/integration/test_emerald.py::TestEmeraldActionIntegration::test_treecko_executor_acquires_treecko \
  tests/integration/test_emerald.py::TestEmeraldActionIntegration::test_torchic_executor_acquires_torchic \
  tests/integration/test_emerald.py::TestEmeraldActionIntegration::test_mudkip_executor_acquires_mudkip -q
```

Measured on 20 September 2026:

- macOS 26.6.2, Apple Silicon `arm64`
- Python 3.13.4
- Homebrew mGBA 0.10.5_2
- libmgba-py import succeeded from the checked archive
- `modules.libmgba.LibmgbaEmulator` imported successfully
- the upstream plugin loader registered `Jev Emerald`
- the user-supplied ROM matched SHA-1 `f3ae088181bf583e55daf962a92bb46f4f1d07b7`
- the browser showed the real Emerald title screen; 60 streamed frames contained 28 distinct images
- the live HTTP server returned the real game state and namespaced plugin status
- an explicitly labeled upstream overworld checkpoint matched its visible map, position, and empty party
- a real wild encounter remained neutral in `Jev Emerald` with no held input or HP change

See [Task 1 progress](task-1-progress.md) for checkpoint provenance, hashes, and measured state. These checkpoint checks establish the emulator integration; they are not a continuous New Game acceptance run.

See [Task 3 progress](task-3-progress.md) for the bounded real Jev starter and
Birch rescue evidence. Task 3 starts from the labeled upstream pre-bag fixture;
it is not a claim that the current mode autonomously plays from New Game.
