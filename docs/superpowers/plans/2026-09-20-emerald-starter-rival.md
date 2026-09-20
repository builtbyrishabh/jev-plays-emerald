# Emerald Starter and Rival Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. This document is a scoped implementation roadmap; validate the named upstream interfaces at the pinned revision before adapting them.

**Goal:** Watch Jev acquire a starter through normal Emerald gameplay and win the first rival battle on Route 103.

**Architecture:** Add a Jev mode to pinned PokéBot Gen3, using its mGBA engine, state readers, input generators, and local HTTP/video support. Jev chooses semantic goals and battle actions; existing routines execute them and report verified outcomes. Codex is a development tool, not a live planner.

**Tech Stack:** Python 3.13, mGBA/libmgba-py, PokéBot Gen3, Jev structured Choice, existing aiohttp server, plain HTML/CSS/JavaScript.

**Spec:** [Architecture](../../architecture.md). Read [reuse research](../../reuse.md) alongside this plan.

## Global constraints

- Target unmodified English Emerald SHA-1 `f3ae088181bf583e55daf962a92bb46f4f1d07b7` initially; reject mismatches.
- Target the user's macOS ARM machine first; validate native bindings and Python compatibility before UI expansion.
- No runtime Codex, accounts, database, queues, extra backend services, or self-modifying code.
- No party injection, teleporting, story-flag writes, or silent fallback from Jev to a baseline.
- Keep ROMs, saves, keys, upstream caches, and generated recordings out of Git.
- Pin upstream revisions after the emulator probe and preserve applicable license notices.
- Stop scope at the first rival victory. Full Hoenn, gyms, catching, team-building, and general exploration follow only after it works.

Use the fixed opening configuration for name/gender/clock/nickname. Jev owns the starter choice. First validate a pre-starter checkpoint for fast development, then complete the configured New Game opening. Label checkpoint runs explicitly.

## Review focus

Each condition has a check in its owning task:

1. Wrong ROM or incompatible native binding: fail before sending model requests (Task 1).
2. A battle interrupts a walking skill or a moving NPC blocks it: release inputs, record interruption, and reevaluate (Tasks 2 and 4).
3. API timeout, malformed probabilities, or an answer to stale game state: pause or bounded retry without executing an invalid action (Task 3).
4. Rival defeat flag already present, battle loss, or ordinary wild victory: never report a new rival win (Tasks 4 and 6).
5. Browser disconnect or pause during a pending call: no cross-thread emulator access or late button presses (Task 5).

## Intended files

Keep the package small and use upstream classes instead of recreating an emulator abstraction:

```text
scripts/bootstrap.py                 pinned upstream checkout and plugin installation
plugins/jev_emerald.py                BotPlugin registration
src/jev_plays_emerald/
  __main__.py                        launch/check configuration and select mode
  mode.py                            generator loop, interrupts, pause, completion
  state.py                           Emerald observations and stable decision context
  actions.py                         legal goals/battle actions and executor dispatch
  opening.py                         opening flags, landmarks, progression and result checks
  jev.py                             one Choice request and validated response
  telemetry.py                       immutable published status and local JSONL events
web/
  index.html                         game + decisions + pause/resume
  app.js                             read video/status; update UI
  style.css                          small responsive layout
tests/
  test_observation.py
  test_action_interrupts.py
  test_jev.py
  test_opening.py
  test_web_state.py
  integration/test_emerald.py         requires user ROM and local fixtures
docs/
  setup.md                           tested commands after Task 1
  results.md                         measured runs after Task 6
```

Only create each file when its task needs it. The plugin forwards to our package; it must not create another control loop beside PokéBot's frame owner.

### Task 1: Boot Emerald with the existing emulator and see it in a browser

**Files:** Create `scripts/bootstrap.py`, `plugins/jev_emerald.py`, `src/jev_plays_emerald/__main__.py`, `mode.py`, `tests/test_observation.py`, `docs/setup.md`. Update `pyproject.toml` only with dependencies actually required.

**Consumes:** Local ROM path; initial target SHA-1; pinned upstream `5dd898f830775d448b06db6f5cd65b930540f146`.

**Produces:** Reproducible bootstrap, a custom no-input mode, verified native frame/state access, and exact launch commands.

- [ ] Add a ROM validation check before any model setup. Test an incorrect small file without needing a real game:
  ```python
  def test_wrong_rom_is_rejected(tmp_path):
      rom = tmp_path / "wrong.gba"
      rom.write_bytes(b"not the supported Emerald ROM")
      with pytest.raises(ValueError, match="ROM"):
          verify_rom(rom)
  ```
  Define `verify_rom(path: Path) -> None` in `__main__.py`; use `hashlib.sha1(path.read_bytes()).hexdigest()` and the target hash above.
- [ ] Pin Python 3.13 and bootstrap upstream into ignored `.cache/pokebot-gen3`; record native archive tag/checksum and working dependencies. Preserve upstream license notices.
- [ ] Register a `BotPlugin.get_additional_bot_modes()` mode whose `run()` yields without issuing input. Use the upstream CLI/profile mechanism to load it.
- [ ] Boot the supplied ROM and capture a title-screen frame. Reach/load an explicit local overworld checkpoint and check map/position/party against the screen.
- [ ] Use upstream `/stream_video?fps=15` for the first visible browser test. Confirm `/custom_state` can publish our status. Do not build a custom streaming pipeline unless this fails.
- [ ] Verify the no-input mode stays in control when a battle begins; document how the battle listener/strategy delegates to our mode.
- [ ] Run `pytest tests/test_observation.py -q`; document the live smoke result and bootstrap command in `docs/setup.md`.
- [ ] Commit the verified integration as `feat: boot Emerald with Pokebot integration`.

**Exit criterion:** This Mac shows real Emerald frames and trustworthy state with our mode in control. If bindings fail, investigate the existing arm64 setup first; evaluate direct mGBA scripting only after documenting the incompatibility. Do not spend time on model strategy or UI polish before this passes.

### Task 2: Reuse actions and make interruptions explicit

**Files:** Create `state.py`, `actions.py`, `opening.py`, `tests/test_action_interrupts.py`; extend `mode.py` and `tests/integration/test_emerald.py`.

**Consumes:** PokéBot frame ownership, `get_game_state()`, player/party readers, navigation and action generators.

**Produces:** Coherent observations, legal semantic actions, bounded execution, and context validation.

- [ ] Represent executable choices as small immutable records. Keep executors in a dispatch table outside model data:
  ```python
  @dataclass(frozen=True)
  class Action:
      id: str
      label: str
      context_id: str

  class Outcome(StrEnum):
      SUCCESS = "success"
      INTERRUPTED = "interrupted"
      FAILED = "failed"
  ```
  Define these in `actions.py`. A context ID changes at a meaningful decision boundary, not every animation frame.
- [ ] Build observations only on the emulator owner thread. Include map/position, controllability, menu/battle phase, party, inventory, opening flags, and recent outcomes. Publish immutable copies.
- [ ] Wrap upstream `navigate_to`, `talk_to_npc`, starter input routines, and battle menu generators. Use their supported data types; avoid broad generic adapter interfaces.
- [ ] Track the selected action until success or interruption. Cancel held inputs on battle, unexpected menu, pause, or timeout. Replan a blocked route locally at most twice, then pause with an explicit failure.
- [ ] Preserve the interrupted goal, but recheck its legality after a battle rather than resuming old button sequences.
- [ ] Test that an encounter interrupts navigation without a subsequent stale movement input:
  ```python
  def test_battle_interrupts_navigation(navigation_fixture):
      run = navigation_fixture.begin_walk()
      navigation_fixture.enter_battle()
      assert next(run) is Outcome.INTERRUPTED
      assert navigation_fixture.held_buttons == set()
  ```
  Implement `navigation_fixture` as a small fake frame/input boundary in `tests/test_action_interrupts.py`; integration tests exercise the same interruption against a real local save.
- [ ] Exercise an actual doorway and a blocked tile in Littleroot/Oldale. Confirm returned coordinates and map IDs, not just elapsed frames.
- [ ] Run `pytest tests/test_action_interrupts.py -q` and the relevant ROM integration checks; commit as `feat: execute Emerald actions with interruption handling`.

**Exit criterion:** Code reliably executes a selected action and stops when the situation changes. No model is needed to debug these mechanics.

### Task 3: Let Jev choose the starter and battle actions

**Files:** Create `jev.py`, `telemetry.py`, `tests/test_jev.py`; extend `actions.py`, `mode.py`, `tests/integration/test_emerald.py`.

**Consumes:** Immutable observation, current `list[Action]`, and server-side TypeSafe credentials.

**Produces:** Validated decisions with the source context, raw model distribution, usage, and request latency.

- [ ] Use one structured `Choice` request over dynamic action IDs. Prefer the official Python client if its supported version fits upstream; otherwise use the already-present HTTP client for the single endpoint. Do not introduce an agent framework.
- [ ] In `jev.py`, define `validate_choice(payload: dict, legal_ids: set[str]) -> str`. Validate the chosen ID, complete finite nonnegative probability distribution with sum within 0.01 of one, and any returned confidence range. Never renormalize invalid data into a claimed real response.
- [ ] Add focused response tests:
  ```python
  def test_illegal_choice_never_executes():
      with pytest.raises(ValueError):
          validate_choice(
              {"choice": "teleport", "probabilities": {"teleport": 1.0}},
              {"move_0", "move_1"},
          )
  ```
  Also cover missing probability labels, NaN, timeout, and a valid choice whose context became stale.
- [ ] Hold neutral inputs while a request is pending. Run the request off the emulator thread, keep only one in flight, and reject late responses after pause or context change. Retry transient errors at most twice; then pause visibly.
- [ ] Do not call Jev for singleton actions. Log `source=deterministic` for those; never synthesize model probabilities.
- [ ] Offer all three starter choices at the actual starter-selection screen. Execute the choice once and confirm the acquired species; do not use upstream shiny-reset behavior.
- [ ] Complete Birch's Zigzagoon rescue fight with legal move choices. Model move descriptions use Gen-3 mechanics from upstream data, not a hardcoded winning move.
- [ ] Log request/response excluding credentials, selected action, executor outcome, latency, and provider usage. Cost is an estimate using an explicit rate unless billing data is available.
- [ ] Run `pytest tests/test_jev.py -q`; run a real-key starter/rescue checkpoint and show the returned distribution. Commit as `feat: let Jev select Emerald starter and battle actions`.

**Exit criterion:** A real Jev request chooses the starter and available battle actions, and the real game executes them correctly. Missing credentials produce setup guidance, never a silent mock.

### Task 4: Connect the opening through the Route 103 rival

**Files:** Extend `opening.py`, `actions.py`, `mode.py`; create `tests/test_opening.py` and extend ROM integration checks.

**Consumes:** Existing navigation/healing/dialogue skills, current story flags, chosen starter, legal-action loop.

**Produces:** Complete opening route with visible recovery and truthful success detection.

- [ ] Add only the opening maps/landmarks through upstream map enums, objects and warps: Littleroot houses, Route 101 bag, Birch's lab, Oldale Center, and Route 103 rival.
- [ ] Implement configured New Game setup and mandatory initial cutscenes, including clock and rival-house interaction if required by the observed story state. Do not ask Jev to type the player name or advance every ordinary text box.
- [ ] Use opening flags to expose relevant goals, not a fixed sequence of directional inputs. Offer healing or continuing in injured overworld states when both are legal.
- [ ] Reuse `heal_in_pokemon_center` for Oldale. Verify HP/PP/status restoration and return control to Jev; test with legitimately injured checkpoint data.
- [ ] Handle wild encounters during travel through the same battle loop. Permit running only where game rules allow it. Do not offer a party switch with no legal alternative.
- [ ] Recognize the Route 103 rival through actual battle identity. Define in `opening.py`:
  ```python
  def rival_completed(
      *, starter_acquired: bool, saw_rival_win: bool,
      flag_before: bool, flag_after: bool,
  ) -> bool:
      return starter_acquired and saw_rival_win and not flag_before and flag_after
  ```
  Connect `saw_rival_win` to the upstream battle-ended outcome callback for the specific encounter, not the last arbitrary battle.
- [ ] Pin the false-positive case:
  ```python
  def test_loaded_completed_save_is_not_a_new_win():
      assert not rival_completed(
          starter_acquired=True, saw_rival_win=False,
          flag_before=True, flag_after=True,
      )
  ```
  Also test a wild win and rival loss. Validate the flag/outcome sequence against the actual ROM.
- [ ] Run `pytest tests/test_opening.py -q`; finish the opening from the New Game setup with inputs only, recording any failure/recovery. Commit as `feat: complete Emerald opening through the first rival`.

**Exit criterion:** The agent acquires a starter, navigates the opening, and records a real rival victory. A loss may trigger visible recovery, but cannot count as completion.

### Task 5: Turn the working loop into a watchable local app

**Files:** Create `web/index.html`, `web/app.js`, `web/style.css`, `tests/test_web_state.py`; extend `telemetry.py`, plugin setup, and `mode.py`.

**Consumes:** Existing upstream video stream, published decision status, pause/resume state.

**Produces:** One localhost page with the game and decision panel; no extra service.

- [ ] Serve our static files through the upstream static directory in its ignored checkout. Use `/stream_video?fps=15` for the game and `/custom_state` for agent data if Task 1 confirms the paths.
- [ ] Keep status JSON free of credentials and executor functions. Never allow the HTTP worker to run an emulator action directly.
- [ ] Display current goal, action distribution, latency, HP, six recent decisions, and starter/rescue/rival progress. Keep the game visually dominant.
- [ ] Connect pause/resume through the verified upstream control seam or a minimal local handler. Pausing invalidates the pending decision context; resuming produces a fresh observation.
- [ ] Add a focused test proving a late result after pause cannot enqueue input:
  ```python
  def test_pause_invalidates_pending_choice(agent_fixture):
      pending = agent_fixture.request_choice()
      agent_fixture.pause()
      agent_fixture.deliver_choice(pending, "move_0")
      assert agent_fixture.executed_actions == []
  ```
  Use the actual mode's response-acceptance logic with a fake API completion, not an unrelated copy.
- [ ] Open the browser and verify a full sequence with pending/error/paused states. Resize to a narrow window and confirm game/probabilities remain visible. Browser disconnect must not crash the agent.
- [ ] Run `pytest tests/test_web_state.py -q`; commit as `feat: show live Emerald gameplay and Jev decisions`.

**Exit criterion:** The same real run can be watched and paused in a browser. UI state comes from real observations/results; no invented explanations.

### Task 6: Prove the slice is repeatable and document how to run it

**Files:** Extend `tests/integration/test_emerald.py`, `docs/setup.md`, `README.md`; create `docs/results.md`. Keep actual ROM/save/video files ignored.

**Consumes:** The complete live app and local checkpoints from normal gameplay.

**Produces:** Reproducible run instructions, a small acceptance report, and a clear next-scope decision.

- [ ] Document one launch command that verifies configuration and starts the application/viewer. Document ROM hash, Python/native versions, API configuration, pause/resume, and explicit checkpoint usage.
- [ ] Exercise all three starter-selection executors from the same pre-selection checkpoint. Verify species after normal acquisition rather than writing party RAM.
- [ ] Run focused real-game scenarios: injured party → Center; walk → wild encounter → reevaluate; new rival win; rival loss; wrong ROM; API failure. ROM-dependent tests skip with a clear reason when assets are absent.
- [ ] Complete three continuous New Game-to-rival runs with varied frame timing. Record all failures and manual interventions. Do not describe the slice as repeatable if only checkpoint continuations succeeded.
- [ ] Compare a small matched checkpoint set with an explicit baseline using the same input skills: healing below a declared threshold, otherwise current story goal, highest estimated immediate battle damage. Keep baseline runs separate and labeled.
- [ ] Report completion, decisions by source, latency, actual usage, estimated/provider cost, action failures, and outcome. Separate a working integration claim from any claim that Jev outperforms rules.
- [ ] Run the focused suite once after final changes and check the continuous viewer evidence. Commit as `docs: record Emerald starter and rival verification`.

**Exit criterion:** The user can follow documented commands, see Jev acquire a starter, and watch a verified first rival win. No extension into gyms or the full region before this passes.

## Final handoff — 20 September 2026

Tasks 1–5 are implemented. Three continuous New Game-to-rival runs succeeded, including a real rival loss followed by automatic recovery, healing, and a rematch win. The final local suite passed 86 tests and 7 ROM subtests. See [results](../../results.md), [setup](../../setup.md), and [viewer](../../viewer.md) for evidence and commands.

Task 6's baseline comparison is deliberately narrower than the original proposal: one matched pre-bag starter/rescue checkpoint, with highest listed move base power rather than a general expected-damage calculator. No claim of model superiority is made. The original checklist above remains the design record; the results report is the authoritative account of what was actually verified.

Implementation is on `feat/emerald-boot` in the isolated local worktree. ROMs, keys, saves, and recordings remain ignored.
