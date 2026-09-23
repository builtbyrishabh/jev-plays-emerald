# Grounded Stuck Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Luna intervene only after Jev is stuck, give a direct hint with an exact observed location, remember verified outcomes across fresh runs, and complete a no-authored-hints run through the Route 103 rival.

**Architecture:** Python owns trusted game evidence, story-stage selection, cross-run JSON memory and the direct brief shown to Jev. A small tracked knowledge file paraphrases the relevant Bulbapedia opening facts. The TypeScript planner receives only the stage-relevant evidence and returns validated structured advice that points to a currently legal destination; Jev still selects every game action.

**Tech Stack:** Python 3.13 dataclasses and JSON, TypeScript 7, Vercel AI SDK 7, Node test runner, pytest, PokéBot Gen3/mGBA.

**Spec:** `docs/superpowers/specs/2026-09-22-grounded-stuck-planner-design.md`

## Global Constraints

- Luna is called only after three repeated meaningful overworld actions without trusted story progress.
- No initial planner call and no planner call merely because story progress changed.
- Jev keeps every legal action and makes the final selection.
- A planner destination must resolve to a currently legal action or observed landmark; invented locations are rejected.
- Only an observed story-progress change may create a verified cross-run lesson.
- Persist memory in `runs/planner-memory.json` using atomic replacement; add no database or dependency.
- The tracked knowledge set covers only the opening through the Route 103 rival and cites Bulbapedia.
- Authored situation hints remain disabled whenever `JEV_PLANNER_MODEL` is set.

## Review Focus

- Corrupt or future-version memory files must be ignored safely and covered by `tests/test_planner_memory.py`.
- Male/female player identity must map to the opposite rival and neighbor house in `tests/test_planner_knowledge.py`.
- A planner response naming an action outside the offered menu must fail transport validation in `tests/test_decision_service.py`.
- Repeated actions on different maps must not share attempt counts in `tests/test_planner.py`.
- A stale planner response arriving after story progress must neither activate advice nor write a lesson in `tests/test_decision_loop.py`.

---

### Task 1: Stage-specific Bulbapedia knowledge

**Files:**
- Create: `knowledge/emerald-opening.json`
- Create: `src/jev_plays_emerald/planner_knowledge.py`
- Create: `tests/test_planner_knowledge.py`

**Interfaces:**
- Consumes: `Observation` from `src/jev_plays_emerald/state.py`.
- Produces: `stage_key(observation: Observation) -> str` and `knowledge_for(observation: Observation) -> tuple[str, ...]`.

- [ ] **Step 1: Write failing stage and identity tests**

```python
from dataclasses import replace

from jev_plays_emerald.planner_knowledge import knowledge_for, stage_key
from jev_plays_emerald.state import MapPosition, Observation, OpeningFlags


def observation(**changes):
    return replace(Observation(
        "ctx", "OVERWORLD", MapPosition((0, 9), (11, 2), "Up", "Littleroot Town"),
        True, "none", "none", (), (), OpeningFlags(False, False, False, True),
        None, (),
    ), **changes)


def test_male_player_gets_may_as_rival_and_neighbor():
    obs = observation(player_gender="male", rival_house_state=0)
    facts = knowledge_for(obs)
    assert stage_key(obs) == "meet_neighbor"
    assert "Brendan is the player; May is the rival." in facts
    assert "May's House is the neighbor's house." in facts


def test_progress_selects_only_the_relevant_stage():
    met_neighbor = observation(rival_house_state=3)
    assert stage_key(met_neighbor) == "rescue_birch"
    assert all("upstairs item" not in fact for fact in knowledge_for(met_neighbor))
```

- [ ] **Step 2: Run the tests and verify the missing-module failure**

Run: `DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q tests/test_planner_knowledge.py`

Expected: FAIL because `jev_plays_emerald.planner_knowledge` does not exist.

- [ ] **Step 3: Add the sourced opening knowledge file**

Create `knowledge/emerald-opening.json` with paraphrased facts rather than copied walkthrough prose:

```json
{
  "schema_version": 1,
  "source": "https://bulbapedia.bulbagarden.net/wiki/Walkthrough:Pok%C3%A9mon_Emerald/Part_1",
  "stages": {
    "meet_neighbor": [
      "Visit the neighbor's house, go upstairs, and inspect the item on the floor so the rival appears.",
      "The male player is Brendan and the rival is May; the female player is May and the rival is Brendan."
    ],
    "rescue_birch": [
      "After meeting the rival, leave Littleroot to the north and reach Route 101 for Birch's rescue.",
      "Choose a starter from Birch's bag during the rescue encounter."
    ],
    "reach_rival": [
      "After Birch's lab scene, cross Route 101 and Oldale Town to reach the rival on Route 103."
    ]
  }
}
```

- [ ] **Step 4: Implement strict stage selection and direct identity facts**

```python
KNOWLEDGE_PATH = Path(__file__).resolve().parents[2] / "knowledge" / "emerald-opening.json"


def stage_key(observation: Observation) -> str:
    if not observation.party:
        return "meet_neighbor" if observation.rival_house_state < 3 else "rescue_birch"
    if not observation.opening_flags.defeated_rival_route103:
        return "reach_rival"
    return "completed"


def knowledge_for(observation: Observation) -> tuple[str, ...]:
    document = json.loads(KNOWLEDGE_PATH.read_text())
    facts = list(document["stages"].get(stage_key(observation), ()))
    if stage_key(observation) == "meet_neighbor":
        if observation.player_gender == "male":
            facts += ["Brendan is the player; May is the rival.", "May's House is the neighbor's house."]
        else:
            facts += ["May is the player; Brendan is the rival.", "Brendan's House is the neighbor's house."]
    return tuple(facts)
```

Reject unsupported `schema_version` and non-string facts with `ValueError`; the mode will surface a planner configuration failure rather than silently feed corrupt knowledge to Luna.

- [ ] **Step 5: Run the focused tests**

Run: `DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q tests/test_planner_knowledge.py`

Expected: PASS.

- [ ] **Step 6: Commit the knowledge unit**

```bash
git add knowledge/emerald-opening.json src/jev_plays_emerald/planner_knowledge.py tests/test_planner_knowledge.py
git commit -m "feat: ground planner in opening knowledge"
```

### Task 2: Atomic cross-run evidence ledger

**Files:**
- Create: `src/jev_plays_emerald/planner_memory.py`
- Create: `tests/test_planner_memory.py`

**Interfaces:**
- Consumes: stage keys, map IDs, action IDs, labels and progress signatures represented as JSON-safe values.
- Produces: `EvidenceLedger(path: Path)`, `summary(stage, map_id)`, `record_dead_end(stage, map_id, action_id, label, reason, count)`, `record_hypothesis(stage, map_id, hint, action_id)`, `reject_hypothesis(stage, action_id)`, and `verify_hypothesis(stage, action_id, evidence)`.

- [ ] **Step 1: Write failing persistence and trust-boundary tests**

```python
def test_verified_lesson_survives_a_fresh_ledger(tmp_path):
    path = tmp_path / "planner-memory.json"
    ledger = EvidenceLedger(path)
    ledger.record_hypothesis("meet_neighbor", (0, 9), "May's House", "walk:0:9:14:8")
    ledger.verify_hypothesis("meet_neighbor", "walk:0:9:14:8", "rival_house_state:0->3")
    assert EvidenceLedger(path).summary("meet_neighbor", (0, 9))["verified"][0]["action_id"] == "walk:0:9:14:8"


def test_unverified_advice_never_becomes_a_fact(tmp_path):
    ledger = EvidenceLedger(tmp_path / "planner-memory.json")
    ledger.record_hypothesis("meet_neighbor", (1, 4), "Try the machine", "interact:10:7")
    summary = EvidenceLedger(ledger.path).summary("meet_neighbor", (1, 4))
    assert summary["verified"] == []
    assert summary["rejected"] == []


def test_corrupt_and_future_memory_start_empty(tmp_path):
    path = tmp_path / "planner-memory.json"
    path.write_text('{"schema_version":999}')
    assert EvidenceLedger(path).summary("meet_neighbor", (0, 9)) == {"verified": [], "rejected": [], "dead_ends": []}
```

- [ ] **Step 2: Run the tests and verify the missing-module failure**

Run: `DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q tests/test_planner_memory.py`

Expected: FAIL because `jev_plays_emerald.planner_memory` does not exist.

- [ ] **Step 3: Implement the JSON ledger with atomic writes**

Use immutable JSON records with `schema_version: 1`. Key entries by `stage`, `map_id` and `action_id`. Keep at most 200 entries, merging counts for identical keys.

```python
class EvidenceLedger:
    def __init__(self, path: Path = Path("runs/planner-memory.json")) -> None:
        self.path = path
        self._data = self._load()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n")
        temporary.replace(self.path)

    def summary(self, stage: str, map_id: tuple[int, int] | None) -> dict[str, object]:
        encoded_map = list(map_id) if map_id is not None else None
        relevant = [entry for entry in self._data["entries"] if entry["stage"] == stage]
        return {
            "verified": [entry for entry in relevant if entry["status"] == "verified"],
            "rejected": [entry for entry in relevant if entry["status"] == "rejected"],
            "dead_ends": [entry for entry in relevant if entry["status"] == "dead_end" and entry["map_id"] == encoded_map],
        }
```

`verify_hypothesis` is the sole transition to `verified`. `record_dead_end` stores observation evidence but does not convert the planner's explanation into a fact. Catch `OSError`, `json.JSONDecodeError` and unsupported schemas in `_load`, returning a fresh document.

- [ ] **Step 4: Run focused tests including atomic rewrite behavior**

Run: `DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q tests/test_planner_memory.py`

Expected: PASS and no `.tmp` file remains.

- [ ] **Step 5: Commit the evidence ledger**

```bash
git add src/jev_plays_emerald/planner_memory.py tests/test_planner_memory.py
git commit -m "feat: persist verified planner evidence"
```

### Task 3: Direct decision brief and accumulated attempt counts

**Files:**
- Modify: `src/jev_plays_emerald/planner.py`
- Modify: `tests/test_planner.py`

**Interfaces:**
- Consumes: `EvidenceLedger`, `knowledge_for`, current `Observation`, and legal `Action` values.
- Produces: `decision_brief(observation, actions, advice)`, `planner_context(observation, actions, advice)`, `accept(observation, advice)`, `mark_advice_followed(action_id)`, and the existing boolean `sync_progress(observation)`.

- [ ] **Step 1: Add failing direct-brief tests**

```python
def test_decision_brief_counts_failed_choices_and_keeps_other_maps_separate(tmp_path):
    obs = observation((0, 9), player_gender="male")
    memory = PlannerMemory(ledger=EvidenceLedger(tmp_path / "memory.json"))
    memory.sync_progress(obs)
    north = Action("walk:0:16:10:19", "Travel North into Route101 at (10, 19)", "ctx")
    for _ in range(3):
        memory.record(obs, obs, north, Outcome.INTERRUPTED, "NeedPokemonTrigger")
    brief = memory.decision_brief(obs, (north,), None)
    assert "Brendan is the player; May is the rival." in brief["confirmed_facts"]
    assert "May's House is the neighbor's house." in brief["confirmed_facts"]
    assert brief["legal_actions"][0]["attempts_without_progress"] == 3
    assert brief["legal_actions"][0]["last_result"] == "NeedPokemonTrigger"


def test_brief_includes_exact_validated_planner_destination(tmp_path):
    action = Action("walk:0:9:14:8", "Enter May's House at (14, 8)", "ctx")
    advice = PlannerAdvice(
        hint="Meet May upstairs.", destination_action_id=action.id,
        location="May's House entrance at (14, 8)", avoid="Do not retry Route 101.",
        success_signal="rival_house_state changes", model="test", usage=TokenUsage(), latency_ms=1,
    )
    assert PlannerMemory(ledger=EvidenceLedger(tmp_path / "m.json")).decision_brief(
        observation((0, 9)), (action,), advice,
    )["planner_hint"]["location"] == "May's House entrance at (14, 8)"


def test_brief_reuses_cross_run_dead_ends(tmp_path):
    path = tmp_path / "m.json"
    EvidenceLedger(path).record_dead_end(
        "meet_neighbor", (1, 4), "talk:1", "Talk to Aide", "no story progress", 3,
    )
    memory = PlannerMemory(ledger=EvidenceLedger(path))
    brief = memory.decision_brief(observation((1, 4)), (), None)
    assert brief["avoid_repeating"][0]["action_id"] == "talk:1"
```

- [ ] **Step 2: Run the focused tests and verify constructor/field failures**

Run: `DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q tests/test_planner.py`

Expected: FAIL because `PlannerMemory` does not accept a ledger and `PlannerAdvice` lacks structured fields.

- [ ] **Step 3: Replace free-text advice with the structured dataclass**

```python
@dataclass(frozen=True)
class PlannerAdvice:
    hint: str
    destination_action_id: str
    location: str
    avoid: str
    success_signal: str
    model: str
    usage: TokenUsage
    latency_ms: float

    @property
    def text(self) -> str:
        return (
            f"Hint: {self.hint} Exact location: {self.location}. "
            f"Avoid: {self.avoid} Success looks like: {self.success_signal}."
        )
```

- [ ] **Step 4: Aggregate attempts instead of exposing a raw rolling dump**

Keep the existing 24-entry history for telemetry, but add a stage-scoped counter that survives the window. `decision_brief` annotates every current legal action with `attempts_without_progress` and `last_result`; `planner_context` contains knowledge, the ledger summary, rejected hypotheses and only the relevant aggregated attempts.

```python
def decision_brief(self, observation, actions, advice):
    stage = stage_key(observation)
    saved = self.ledger.summary(stage, observation.position.map_id if observation.position else None)
    return {
        "current_goal": stage.replace("_", " "),
        "confirmed_facts": list(knowledge_for(observation)),
        "avoid_repeating": saved["dead_ends"] + saved["rejected"],
        "legal_actions": [self._action_summary(observation, action) for action in actions],
        "planner_hint": asdict(advice) if advice is not None else None,
    }

def planner_context(self, observation, actions, advice):
    brief = self.decision_brief(observation, actions, advice)
    return {
        "trigger": self.reason(observation),
        "walkthroughKnowledge": brief["confirmed_facts"],
        "deadEnds": brief["avoid_repeating"],
        "legalActions": brief["legal_actions"],
        "previousAdvice": brief["planner_hint"],
    }
```

On the third repeated attempt, call `ledger.record_dead_end(stage, map_id, action.id, action.label, reason, count)`. `accept(observation, advice)` records the new hypothesis and rejects any replaced unverified hypothesis. `sync_progress` verifies a followed active hypothesis before clearing counters and returning `True`.

Add `mark_advice_followed(action_id: str)` and record that bit only when Jev
actually selects the advised destination. A later story change verifies the
hypothesis only when that bit is true; unrelated progress must not promote it.

- [ ] **Step 5: Run planner tests**

Run: `DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q tests/test_planner.py tests/test_planner_memory.py tests/test_planner_knowledge.py`

Expected: PASS.

- [ ] **Step 6: Commit the direct brief**

```bash
git add src/jev_plays_emerald/planner.py tests/test_planner.py
git commit -m "feat: give Jev a direct decision brief"
```

### Task 4: Validated structured Luna response

**Files:**
- Modify: `service/src/planner.ts`
- Modify: `service/src/planner.test.ts`
- Modify: `service/src/protocol.ts`
- Modify: `src/jev_plays_emerald/service.py`
- Modify: `tests/test_decision_service.py`

**Interfaces:**
- Consumes: the planner state containing `decision_brief` and `planner_context`, plus the legal action map.
- Produces: a `plan` response with `hint`, `destinationActionId`, `location`, `avoid`, `successSignal`, `model`, `usage`, and `latencyMs`.

- [ ] **Step 1: Write failing TypeScript validation tests**

```typescript
const options = { 'walk:0:9:14:8': "Enter May's House at (14, 8)" }

test('planner advice must name an offered destination', () => {
  const valid = validatePlannerAdvice(JSON.stringify({
    hint: 'Meet May upstairs.',
    destinationActionId: 'walk:0:9:14:8',
    location: "May's House entrance at (14, 8)",
    avoid: 'Do not retry Route 101.',
    successSignal: 'rival_house_state changes',
  }), false, options)
  assert.equal(valid.destinationActionId, 'walk:0:9:14:8')
  assert.throws(() => validatePlannerAdvice(JSON.stringify({
    ...valid, destinationActionId: 'walk:invented',
  }), false, options), /offered legal action/)
})
```

- [ ] **Step 2: Run the service test and verify the signature failure**

Run: `pnpm --dir service test`

Expected: FAIL because `validatePlannerAdvice` still returns a string and accepts two arguments.

- [ ] **Step 3: Update the planner prompt and parser without adding a dependency**

Keep `generateText` and request JSON-only output. Parse with `JSON.parse`, validate each required field as a non-empty string, require `destinationActionId in options`, and cap each prose field at 40 words.

The system prompt must say:

```text
You are called only after three repeated attempts without story progress.
Use walkthroughKnowledge as reference and liveState as authority.
Do not repeat any rejectedHypothesis or deadEnd.
Return one immediate reachable destination from legalActions.
Copy its action ID exactly and describe its exact named location/coordinates.
State the observable success signal. Return JSON only.
```

- [ ] **Step 4: Update the protocol and Python transport**

Change the TypeScript `plan` response type to structured fields. In `DecisionService.plan`, validate that all five strings exist, that `destinationActionId` is a current option, and construct `PlannerAdvice` with keyword arguments.

```python
required = ("hint", "destinationActionId", "location", "avoid", "successSignal")
if response.get("type") != "plan" or any(not isinstance(response.get(key), str) for key in required):
    raise ValueError("invalid planner advice")
if response["destinationActionId"] not in options:
    raise ValueError("planner destination is not a legal action")
return PlannerAdvice(
    hint=response["hint"], destination_action_id=response["destinationActionId"],
    location=response["location"], avoid=response["avoid"],
    success_signal=response["successSignal"], model=response["model"],
    usage=TokenUsage(usage.get("inputTokens"), usage.get("outputTokens")),
    latency_ms=response.get("latencyMs", 0),
)
```

Add a Python transport test whose fake child returns a valid exact location, plus a parametrized rejection test for missing fields and an out-of-menu destination.

- [ ] **Step 5: Run both sides of the transport contract**

Run: `pnpm --dir service check && pnpm --dir service test && DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q tests/test_decision_service.py`

Expected: PASS.

- [ ] **Step 6: Commit the structured contract**

```bash
git add service/src/planner.ts service/src/planner.test.ts service/src/protocol.ts src/jev_plays_emerald/service.py tests/test_decision_service.py
git commit -m "feat: require grounded planner locations"
```

### Task 5: Integrate evidence, advice and Jev decisions

**Files:**
- Modify: `src/jev_plays_emerald/mode.py`
- Modify: `src/jev_plays_emerald/opening.py`
- Modify: `tests/test_decision_loop.py`
- Modify: `tests/test_open_world.py`
- Modify: `web/app.js`
- Modify: `web/index.html`
- Modify: `tests/test_web_state.py`

**Interfaces:**
- Consumes: `PlannerMemory.decision_brief`, `PlannerMemory.planner_context`, and structured `PlannerAdvice`.
- Produces: Jev requests with `state["decision_brief"]`, stuck-only planner requests, verified/rejected ledger updates, and a viewer panel with hint, exact location and success signal.

- [ ] **Step 1: Write failing loop tests for the complete policy**

Add these concrete tests beside the existing planner-loop tests:

```python
def test_jev_receives_attempt_counts_before_raw_observation(mode_runtime):
    mode, reader, _, worker, _, telemetry = mode_runtime
    mode._planner = PlannerMemory(ledger=EvidenceLedger(telemetry._path.parent / "memory.json"))
    overworld = replace(reader.observation, game_state="OVERWORLD", menu_phase="none",
                        position=MapPosition((1, 4), (8, 9), "Up", "Birch's Lab"))
    action = Action("walk:1:4:6:12", "Leave Birch's Lab at (6, 12)", overworld.context_id)
    mode._latest_observation = overworld
    mode._available_actions = (action,)
    mode._planner.sync_progress(overworld)
    for _ in range(2):
        mode._planner.record(overworld, overworld, action, Outcome.SUCCESS, None)
    mode._start_model_request((action,), attempt=1)
    request = next(json.loads(line) for line in telemetry._path.read_text().splitlines()
                   if json.loads(line)["event"] == "request")
    [leave] = request["state"]["decision_brief"]["legal_actions"]
    assert leave["action_id"] == action.id
    assert leave["attempts_without_progress"] == 2
    assert mode.planner_view["calls"] == 0
    assert len(worker.futures) == 1


def test_luna_is_called_only_on_the_third_repeat(mode_runtime):
    mode, reader, actions, worker, _, telemetry = mode_runtime
    mode._planner = PlannerMemory(ledger=EvidenceLedger(telemetry._path.parent / "memory.json"))
    overworld = replace(reader.observation, game_state="OVERWORLD", menu_phase="none",
                        position=MapPosition((1, 4), (8, 9), "Up", "Birch's Lab"))
    leave = Action("walk:1:4:6:12", "Leave Birch's Lab at (6, 12)", overworld.context_id)
    mode._planner.sync_progress(overworld)
    mode._planner.record(overworld, overworld, leave, Outcome.SUCCESS, None)
    mode._planner.record(overworld, overworld, leave, Outcome.SUCCESS, None)
    assert mode._planner.reason(overworld) is None
    mode._planner.record(overworld, overworld, leave, Outcome.SUCCESS, None)
    assert mode._planner.reason(overworld) == "three repeated attempts without story progress"


def test_story_progress_verifies_active_hint_and_clears_it(mode_runtime):
    mode, reader, _, _, _, telemetry = mode_runtime
    ledger_path = telemetry._path.parent / "memory.json"
    mode._planner = PlannerMemory(ledger=EvidenceLedger(ledger_path))
    mode._planner.sync_progress(reader.observation)
    mode._advice = PlannerAdvice(
        hint="Meet May.", destination_action_id="starter:treecko",
        location="Current starter menu", avoid="Do not leave.",
        success_signal="party changes", model="test", usage=TokenUsage(), latency_ms=1,
    )
    mode._planner.accept(reader.observation, mode._advice)
    mode._planner.mark_advice_followed("starter:treecko")
    reader.observation = replace(reader.observation, party=(PartyMember(
        "Treecko", 5, 20, 20, "Healthy", (),
    ),))
    run = mode.run()
    try:
        next(run)
        assert mode.planner_view["advice"] is None
        assert EvidenceLedger(ledger_path).summary("meet_neighbor", (0, 16))["verified"]
    finally:
        run.close()


def test_stale_advice_writes_no_cross_run_lesson(mode_runtime):
    mode, reader, _, worker, _, telemetry = mode_runtime
    ledger_path = telemetry._path.parent / "memory.json"
    mode._planner = _stuck_planner(reader.observation)
    mode._planner.ledger = EvidenceLedger(ledger_path)
    run = mode.run()
    try:
        next(run)
        reader.observation = replace(reader.observation, rival_house_state=3)
        worker.futures[0].set_result(PlannerAdvice(
            hint="Meet May.", destination_action_id="starter:treecko",
            location="Current menu", avoid="None", success_signal="state changes",
            model="test", usage=TokenUsage(), latency_ms=1,
        ))
        next(run)
        assert EvidenceLedger(ledger_path).summary("meet_neighbor", None)["verified"] == []
    finally:
        run.close()
```

- [ ] **Step 2: Run the focused loop tests and verify they fail**

Run: `DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q tests/test_decision_loop.py tests/test_open_world.py tests/test_web_state.py`

Expected: FAIL because requests do not contain `decision_brief` and the mode does not connect advice to the ledger.

- [ ] **Step 3: Add the brief to every non-forced Jev request**

In `_start_model_request`, add:

```python
if self._planner is not None:
    state["decision_brief"] = self._planner.decision_brief(observation, actions, self._advice)
```

Continue to pass the base mission with authored hints disabled. Update `decision_instructions` so structured advice is rendered after the direct brief and says that Jev—not Luna—chooses the action.

- [ ] **Step 4: Send compact planner context and validate accepted advice**

Replace the raw `attempts: list(self._planner.history)` payload with `self._planner.planner_context(observation, actions, self._advice)`. Before accepting advice, require its destination action ID in `pending.actions`; otherwise treat it as a planner validation error.

```python
state["planner_context"] = self._planner.planner_context(observation, actions, self._advice)

destination_is_legal = any(
    action.id == advice.destination_action_id for action in pending.actions
)
if not destination_is_legal:
    raise ValueError("planner destination is not in the pending legal actions")
self._planner.accept(pending.observation, advice)
self._advice = advice
```

When `sync_progress` reports a change, its internal evidence transition has already verified a followed active hypothesis; the mode then clears advice. When Jev selects the advised destination, call `mark_advice_followed` before execution.

```python
if self._planner.sync_progress(self._latest_observation):
    self._advice = None

# In the accepted Jev-choice branch:
if self._planner is not None and self._advice is not None:
    self._planner.mark_advice_followed(action.id)
```

- [ ] **Step 5: Show direct advice in the viewer**

Expose structured advice from `planner_view` and render four labeled values: hint, exact location, avoid, and success signal. When no intervention has occurred, show `Waiting until Jev repeats an action three times` instead of `Waiting for the first objective`.

```javascript
text("planner-advice", planner.advice?.hint ?? "Waiting until Jev repeats an action three times")
text("planner-location", planner.advice?.location ?? "—")
text("planner-avoid", planner.advice?.avoid ?? "—")
text("planner-success", planner.advice?.success_signal ?? "—")
```

- [ ] **Step 6: Run focused integration tests**

Run: `DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q tests/test_decision_loop.py tests/test_open_world.py tests/test_web_state.py && node --check web/app.js`

Expected: PASS.

- [ ] **Step 7: Commit the integrated planner flow**

```bash
git add src/jev_plays_emerald/mode.py src/jev_plays_emerald/opening.py tests/test_decision_loop.py tests/test_open_world.py web/app.js web/index.html tests/test_web_state.py
git commit -m "feat: connect grounded hints to Jev"
```

### Task 6: Full verification and measured rival run

**Files:**
- Modify: `README.md`
- Modify: `docs/planner-proposal.md`
- Modify: `docs/decision-service.md`
- Modify: `docs/results.md`
- Modify: `.cache/planner-pr-body.md` (ignored PR-body workspace file)

**Interfaces:**
- Consumes: the completed feature and live launcher.
- Produces: reproducible launch docs, measured gameplay evidence, and an updated PR.

- [ ] **Step 1: Run the complete automated verification before gameplay**

Run:

```bash
DYLD_LIBRARY_PATH=/opt/homebrew/lib .venv/bin/python -m pytest -q
pnpm --dir service check
pnpm --dir service test
node --check web/app.js
git diff --check
```

Expected: all commands exit 0; record exact test counts.

- [ ] **Step 2: Start one fresh no-authored-hints acceptance run**

Run:

```bash
JEV_PLANNER_MODEL=openai/gpt-5.6-luna JEV_AUTHORED_HINTS=0 \
  uv run --env-file .env python -m jev_plays_emerald \
  --rom 'roms/Pokemon - Emerald Version (USA, Europe).gba' \
  --profile grounded-luna-rival-20260922
```

Observe the live viewer and `runs/decisions.jsonl`. Stop on verified rival victory or a reproducible blocker. Do not manually choose game actions.

- [ ] **Step 3: Compare the new run with the failed 25-call run**

Record: starter/rescue/rival outcome, Jev and deterministic decisions, Luna calls, planner tokens, repeated actions, rejected hypotheses, verified persisted lessons, and the exact stop reason. The primary comparison is whether the lab/Route 101/wrong-house oscillation disappears; do not claim general model superiority from one run.

- [ ] **Step 4: Prove cross-run improvement**

Start a second fresh profile with the first run's `runs/planner-memory.json` intact. Verify the direct brief includes the first run's verified lesson and that Luna does not repeat a rejected wrong-house hypothesis. Stop after the starter/rescue milestone unless continuing is needed to diagnose a regression.

- [ ] **Step 5: Update documentation and PR body with measured facts**

Document the stuck-only trigger, exact-location contract, local knowledge source, JSON ledger path and extension boundary beyond the rival. Add the two run results without claiming the full game is supported.

- [ ] **Step 6: Re-run final verification after documentation changes**

Run the five commands from Step 1 again. Read every exit code and retain the output for the handoff.

- [ ] **Step 7: Commit, push and update PR #4**

```bash
git add README.md docs/planner-proposal.md docs/decision-service.md docs/results.md
git commit -m "docs: record grounded planner results"
git push origin feat/open-world-action-kinds
gh pr edit 4 --body-file .cache/planner-pr-body.md
```

Register PR #4 with the thread after updating it and confirm the remote head matches local `HEAD`.
