from concurrent.futures import Future
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import sys
from threading import Event, Thread
from types import MappingProxyType, SimpleNamespace

import pytest

from jev_plays_emerald.actions import Action, Outcome
from jev_plays_emerald.jev import (
    JevChoice,
    JevGatewayError,
    JevResponseError,
    JevTimeoutError,
    TokenUsage,
)
from jev_plays_emerald.planner import PlannerAdvice, PlannerMemory
from jev_plays_emerald.planner_memory import EvidenceLedger
from jev_plays_emerald.state import (
    ActiveBattler,
    MapPosition,
    MapObject,
    MoveState,
    Observation,
    OpeningFlags,
    PartyMember,
)


POKEBOT_ROOT = Path(__file__).parents[1] / ".cache" / "pokebot-gen3"


def _observation(
    context_id: str = "starter-context",
    *,
    actions: int = 3,
) -> Observation:
    if actions == 3:
        return Observation(
            context_id=context_id,
            game_state="CHOOSE_STARTER",
            position=MapPosition((0, 16), (7, 14), "Up"),
            controllable=False,
            menu_phase="starter",
            battle_phase="none",
            party=(),
            inventory=(),
            opening_flags=OpeningFlags(False, False, False),
            active_battler=None,
            recent_outcomes=(),
        )
    move = MoveState(
        "Pound",
        35,
        35,
        type="Normal",
        power=40,
        accuracy=1.0,
        description="Pounds the foe with forelegs or tail.",
    )
    return Observation(
        context_id=context_id,
        game_state="BATTLE",
        position=MapPosition((0, 16), (7, 14), "Up"),
        controllable=False,
        menu_phase="battle",
        battle_phase="action",
        party=(PartyMember("Treecko", 5, 20, 20, "Healthy", (move,)),),
        inventory=(),
        opening_flags=OpeningFlags(False, False, False),
        active_battler=ActiveBattler(0, (move,)),
        recent_outcomes=(),
    )


class FakeReader:
    def __init__(self, observation: Observation) -> None:
        self.observation = observation

    def read(self, recent_outcomes=()) -> Observation:
        return self.observation


class FakeActionExecutor:
    def __init__(self) -> None:
        self.current_action = None
        self.interrupted_action = None
        self.last_reason = None
        self.executed: list[Action] = []
        self.close_count = 0

    def execute(self, action: Action):
        self.current_action = action
        self.executed.append(action)
        try:
            yield None
            self.current_action = None
            yield Outcome.SUCCESS
        finally:
            self.current_action = None
            self.close_count += 1

    def revalidate_interrupted(self, actions):
        return None


class ManualWorker:
    def __init__(self) -> None:
        self.futures: list[Future[JevChoice]] = []

    def submit(self, function, /, *args, **kwargs):
        future: Future[JevChoice] = Future()
        self.futures.append(future)
        return future


class FakeEmulator:
    def __init__(self) -> None:
        self.reset_count = 0
        self.pressed: list[str] = []

    def reset_held_buttons(self) -> None:
        self.reset_count += 1

    def press_button(self, button: str) -> None:
        self.pressed.append(button)


def _choice(action_id: str) -> JevChoice:
    return JevChoice(
        choice=action_id,
        probabilities=MappingProxyType(
            {
                "starter:treecko": 0.6,
                "starter:torchic": 0.25,
                "starter:mudkip": 0.15,
            }
        ),
        confidence=0.8,
        usage=TokenUsage(100, 20),
        latency_ms=12.5,
    )


def _advice(
    hint: str = "Meet May upstairs.",
    destination_action_id: str = "starter:treecko",
) -> PlannerAdvice:
    return PlannerAdvice(
        hint=hint,
        destination_action_id=destination_action_id,
        location="Current starter menu",
        avoid="Do not repeat the blocked route.",
        success_signal="story progress changes",
        model="test",
        usage=TokenUsage(40, 10),
        latency_ms=8,
    )


def _stuck_planner(reader: FakeReader, ledger_path: Path) -> PlannerMemory:
    reader.observation = replace(
        reader.observation, game_state="OVERWORLD", controllable=True,
        menu_phase="none", battle_phase="none",
        objects=tuple(MapObject(index, (index, 3), f"EventScript_Person{index}") for index in (1, 2, 3)),
    )
    observation = reader.observation
    memory = PlannerMemory(ledger=EvidenceLedger(ledger_path))
    memory.sync_progress(observation)
    overworld = replace(
        observation,
        game_state="OVERWORLD",
        menu_phase="none",
        position=MapPosition((1, 4), (8, 9), "Up", "Birch's Lab"),
    )
    action = Action("walk:1:4:6:12", "Leave Birch's Lab", observation.context_id)
    for _ in range(3):
        memory.record(overworld, overworld, action, Outcome.SUCCESS, None)
    return memory


@pytest.fixture
def mode_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    sys.path.insert(0, str(POKEBOT_ROOT))
    from modules.context import context
    from jev_plays_emerald.mode import JevEmeraldMode
    from jev_plays_emerald.telemetry import DecisionTelemetry

    previous_emulator = context.emulator
    emulator = FakeEmulator()
    context.emulator = emulator
    reader = FakeReader(_observation())
    actions = FakeActionExecutor()
    worker = ManualWorker()
    telemetry = DecisionTelemetry(tmp_path / "decisions.jsonl")
    mode = JevEmeraldMode(
        observation_reader=reader,
        executor=actions,
        gateway=SimpleNamespace(),
        model_worker=worker,
        telemetry=telemetry,
    )
    try:
        yield mode, reader, actions, worker, emulator, telemetry
    finally:
        context.emulator = previous_emulator
        sys.path.remove(str(POKEBOT_ROOT))


def test_one_model_request_stays_neutral_until_its_choice_is_ready(mode_runtime):
    mode, _, actions, worker, emulator, telemetry = mode_runtime
    run = mode.run()

    assert next(run) is None
    assert len(worker.futures) == 1
    assert actions.executed == []
    [request_event] = [
        event
        for event in map(json.loads, telemetry._path.read_text().splitlines())
        if event["event"] == "request"
    ]
    assert request_event["context_id"] == "starter-context"
    assert request_event["attempt"] == 1
    assert request_event["state"]["game_state"] == "CHOOSE_STARTER"
    assert request_event["questions"]["action"]["criteria"] == {
        "starter:treecko": "Choose Treecko",
        "starter:torchic": "Choose Torchic",
        "starter:mudkip": "Choose Mudkip",
    }
    reset_while_started = emulator.reset_count

    assert next(run) is None
    assert len(worker.futures) == 1
    assert actions.executed == []
    assert emulator.reset_count > reset_while_started


    worker.futures[0].set_result(_choice("starter:treecko"))
    assert next(run) is None

    assert [action.id for action in actions.executed] == ["starter:treecko"]
    response_event = next(
        event
        for event in map(json.loads, telemetry._path.read_text().splitlines())
        if event["event"] == "response"
    )
    assert response_event["disposition"] == "accepted"
    assert response_event["usage"] == {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": None}
    assert response_event["cost"] == {
        "estimated_usd": 0.0000042,
        "input_usd_per_token": 0.000000042,
        "output_usd_per_token": 0.0,
        "source": "https://ai-gateway.vercel.sh/v1/models",
        "checked_on": "2026-09-20",
    }
    assert telemetry.snapshot.last_decision is not None
    assert telemetry.snapshot.last_decision.source == "model"


@pytest.mark.parametrize("unavailable_state", ["UNKNOWN", "CHANGE_MAP", "OVERWORLD"])
def test_first_gym_mode_waits_for_loaded_story_before_labeling_checkpoint(mode_runtime, monkeypatch, unavailable_state):
    from jev_plays_emerald.mode import JevEmeraldMode

    _, reader, actions, worker, _, telemetry = mode_runtime
    monkeypatch.setenv("JEV_TARGET", "first-gym")
    mode = JevEmeraldMode(reader, actions, gateway=SimpleNamespace(), model_worker=worker, telemetry=telemetry)
    position = reader.observation.position
    reader.observation = replace(reader.observation, game_state=unavailable_state, menu_phase="none",
                                 position=None, controllable=False)
    runner = mode.run()
    next(runner)
    assert not mode.checkpoint
    reader.observation = replace(reader.observation, game_state="OVERWORLD", controllable=True, position=position,
                                 opening_flags=OpeningFlags(True, True, True, stone_badge=True))
    next(runner)
    assert mode.checkpoint and mode.paused
    assert not mode.completed
    assert not mode.available_actions
    assert not worker.futures
    runner.close()


def test_model_request_includes_dialogue_jev_has_read(mode_runtime):
    mode, reader, _, _, _, telemetry = mode_runtime
    reader.observation = replace(
        reader.observation,
        recent_dialogue=("PROF. BIRCH is in trouble!", "Choose a POKEMON."),
    )
    run = mode.run()
    try:
        next(run)
        request = next(
            event
            for event in map(json.loads, telemetry._path.read_text().splitlines())
            if event["event"] == "request"
        )
        assert request["state"]["recent_dialogue"] == [
            "PROF. BIRCH is in trouble!",
            "Choose a POKEMON.",
        ]
    finally:
        run.close()


def test_requested_name_is_confirmed_by_jev_without_a_planner_call(mode_runtime):
    from dataclasses import replace
    from jev_plays_emerald.planner import PlannerMemory

    mode, reader, actions, worker, _, telemetry = mode_runtime
    reader.observation = replace(reader.observation, game_state="NAMING_SCREEN", menu_phase="none")
    mode._planner = PlannerMemory()
    run = mode.run()
    try:
        next(run)
        assert actions.executed == []
        assert mode.planner_view["calls"] == 0
        request = next(e for e in map(json.loads, telemetry._path.read_text().splitlines()) if e["event"] == "request")
        assert request["questions"]["action"]["criteria"] == {"setup:name": "Confirm your requested player name: Jev"}
        worker.futures[0].set_result(JevChoice("setup:name", {"setup:name": 1.0}, None, TokenUsage(), 1))
        next(run)
        assert actions.executed[0].id == "setup:name"
        assert telemetry.snapshot.last_decision.source == "model"
    finally:
        run.close()


def test_planner_advice_replaces_hints_and_jev_keeps_all_choices(mode_runtime):
    mode, reader, actions, worker, emulator, telemetry = mode_runtime
    mode._planner = _stuck_planner(reader, telemetry._path.parent / "memory.json")
    run = mode.run()
    try:
        next(run)
        assert mode.planner_view["pending"]
        assert actions.executed == []
        next(run)
        assert len(worker.futures) == 1
        assert emulator.reset_count > 0
        worker.futures[0].set_result(_advice("Speak to the person you prefer.", destination_action_id="talk:1"))
        next(run)
        next(run)
        events = list(map(json.loads, telemetry._path.read_text().splitlines()))
        request = next(event for event in events if event["event"] == "request")
        assert "Speak to the person you prefer." in request["questions"]["action"]["instructions"]
        assert "Expect the rival" not in request["questions"]["action"]["instructions"]
        assert len(request["questions"]["action"]["criteria"]) == 3
        worker.futures[1].set_result(_choice("talk:3"))
        next(run)
        assert actions.executed[0].id == "talk:3"
        assert mode.planner_view["calls"] == 1
        assert mode.planner_view["advice"]["hint"] == "Speak to the person you prefer."
    finally:
        run.close()


def test_minimal_planner_mode_starts_with_dialogue_and_no_authored_hint(mode_runtime):
    mode, reader, _, _, _, telemetry = mode_runtime
    mode._planner = PlannerMemory()

    run = mode.run()
    try:
        next(run)
        request = next(
            event for event in map(json.loads, telemetry._path.read_text().splitlines())
            if event["event"] == "request"
        )
        instructions = request["questions"]["action"]["instructions"]
        assert instructions.startswith("Goal: defeat your rival on Route 103.")
        assert "This is your starter choice" not in instructions
        assert "confirmed_facts" not in request["state"]["decision_brief"]
        assert "current_goal" not in request["state"]["decision_brief"]
        assert "May's House" not in json.dumps(request)
        assert mode.planner_view["calls"] == 0
    finally:
        run.close()


def test_jev_receives_attempt_counts_before_raw_observation(mode_runtime):
    mode, reader, _, worker, _, telemetry = mode_runtime
    mode._planner = PlannerMemory(
        ledger=EvidenceLedger(telemetry._path.parent / "memory.json")
    )
    overworld = replace(
        reader.observation,
        game_state="OVERWORLD",
        menu_phase="none",
        position=MapPosition((1, 4), (8, 9), "Up", "Birch's Lab"),
    )
    action = Action(
        "walk:1:4:6:12", "Leave Birch's Lab at (6, 12)", overworld.context_id
    )
    mode._latest_observation = overworld
    mode._available_actions = (action,)
    mode._planner.sync_progress(overworld)
    for _ in range(2):
        mode._planner.record(overworld, overworld, action, Outcome.SUCCESS, None)

    mode._start_model_request((action,), attempt=1)

    request = next(
        json.loads(line)
        for line in telemetry._path.read_text().splitlines()
        if json.loads(line)["event"] == "request"
    )
    [leave] = request["state"]["decision_brief"]["legal_actions"]
    assert leave["action_id"] == action.id
    assert leave["attempts_without_progress"] == 2
    assert mode.planner_view["calls"] == 0
    assert len(worker.futures) == 1


def test_luna_is_called_only_on_the_third_repeat(mode_runtime):
    mode, reader, _, _, _, telemetry = mode_runtime
    mode._planner = PlannerMemory(
        ledger=EvidenceLedger(telemetry._path.parent / "memory.json")
    )
    overworld = replace(
        reader.observation,
        game_state="OVERWORLD",
        controllable=True,
        menu_phase="none",
        position=MapPosition((1, 4), (8, 9), "Up", "Birch's Lab"),
    )
    leave = Action(
        "walk:1:4:6:12", "Leave Birch's Lab at (6, 12)", overworld.context_id
    )
    mode._planner.sync_progress(overworld)
    for _ in range(2):
        mode._planner.record(overworld, overworld, leave, Outcome.SUCCESS, None)
    assert mode._planner.reason(overworld) is None

    mode._planner.record(overworld, overworld, leave, Outcome.SUCCESS, None)

    assert mode._planner.reason(overworld) == "three repeated attempts without story progress"


def test_mode_keeps_all_legal_actions_after_an_immediate_map_loop(
    mode_runtime, monkeypatch
):
    from jev_plays_emerald import mode as mode_module

    mode, reader, _, worker, _, telemetry = mode_runtime
    memory = PlannerMemory(
        ledger=EvidenceLedger(telemetry._path.parent / "memory.json")
    )
    downstairs = replace(
        reader.observation,
        game_state="OVERWORLD",
        controllable=True,
        menu_phase="none",
        battle_phase="none",
        position=MapPosition((1, 2), (2, 3), "Up", "May's House 1F"),
    )
    upstairs = replace(
        downstairs,
        position=MapPosition((1, 3), (1, 2), "Down", "May's House 2F"),
    )
    go_up = Action("walk:1:2:2:2", "Go upstairs", upstairs.context_id)
    go_down = Action("walk:1:3:1:1", "Go downstairs", upstairs.context_id)
    inspect = Action("talk:16", "Inspect the rival's Poke Ball", upstairs.context_id)
    memory.sync_progress(downstairs)
    memory.record(downstairs, upstairs, go_up, Outcome.SUCCESS, None)
    memory.record(upstairs, downstairs, go_down, Outcome.SUCCESS, None)
    mode._planner = memory
    reader.observation = upstairs
    monkeypatch.setattr(
        mode_module, "legal_actions", lambda observation, suppress_futile: (go_down, inspect)
    )

    run = mode.run()
    try:
        next(run)
        assert mode.available_actions == (go_down, inspect)
        assert len(worker.futures) == 1
    finally:
        run.close()


@pytest.mark.parametrize("limit", ["max_calls", "max_tokens"])
def test_exhausted_coach_budget_keeps_jev_playing_without_another_luna_call(mode_runtime, limit):
    mode, reader, _, worker, _, telemetry = mode_runtime
    mode._planner = _stuck_planner(reader, telemetry._path.parent / "memory.json")
    setattr(mode._coaching, limit, 0)
    run = mode.run()
    try:
        next(run)
        assert not mode.paused
        assert mode.planner_view["budget"]["exhausted"]
        assert mode.planner_view["calls"] == 0
        assert mode._pending_plan is None
        assert mode._pending_decision is not None
        assert len(worker.futures) == 1
    finally:
        run.close()


def test_intervention_is_logged_when_jev_executes_the_hint(mode_runtime):
    mode, reader, _, worker, _, telemetry = mode_runtime
    mode._planner = _stuck_planner(reader, telemetry._path.parent / "memory.json")
    run = mode.run()
    try:
        next(run)
        worker.futures[0].set_result(_advice(destination_action_id="talk:1"))
        next(run)
        next(run)
        worker.futures[1].set_result(_choice("talk:1"))
        next(run)
        next(run)  # The bounded executor finishes on its second frame.
        entry = mode.planner_view["interventions"][0]
        assert entry["selected"]
        assert entry["action_outcome"] == "success"
        events = [json.loads(line) for line in telemetry._path.read_text().splitlines()]
        assert any(e["event"] == "planner-intervention" and e["selected"] for e in events)
    finally:
        run.close()


def test_story_progress_verifies_active_hint_and_clears_it(mode_runtime):
    mode, reader, _, _, _, telemetry = mode_runtime
    ledger_path = telemetry._path.parent / "memory.json"
    mode._planner = PlannerMemory(ledger=EvidenceLedger(ledger_path))
    mode._planner.sync_progress(reader.observation)
    mode._advice = _advice()
    mode._planner.accept(reader.observation, mode._advice)
    mode._planner.mark_advice_followed("starter:treecko")
    reader.observation = replace(
        reader.observation,
        party=(PartyMember("Treecko", 5, 20, 20, "Healthy", ()),),
    )

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
    mode._planner = _stuck_planner(reader, ledger_path)
    run = mode.run()
    try:
        next(run)
        reader.observation = replace(reader.observation, rival_house_state=3)
        worker.futures[0].set_result(_advice(destination_action_id="talk:1"))
        next(run)
        assert EvidenceLedger(ledger_path).summary("meet_neighbor", None)["verified"] == []
    finally:
        run.close()


def test_mode_rejects_planner_destination_outside_pending_menu(mode_runtime):
    mode, reader, _, worker, _, telemetry = mode_runtime
    mode._planner = _stuck_planner(
        reader, telemetry._path.parent / "memory.json"
    )
    run = mode.run()
    try:
        next(run)
        worker.futures[0].set_result(_advice(destination_action_id="walk:invented"))
        next(run)

        assert mode.paused
        assert "pending legal actions" in telemetry.snapshot.last_error
        assert mode.planner_view["advice"] is None
        assert telemetry.snapshot.planner_usage.input_tokens == 40
        assert telemetry.snapshot.planner_usage.pending == 0
        assert not mode.planner_view["budget"]["unknown_usage"]
    finally:
        run.close()


def test_completed_hint_action_returns_control_to_jev_until_another_stall(
    mode_runtime,
):
    mode, reader, _, worker, _, telemetry = mode_runtime
    ledger_path = telemetry._path.parent / "memory.json"
    mode._planner = PlannerMemory(ledger=EvidenceLedger(ledger_path))
    lab = replace(
        reader.observation,
        game_state="OVERWORLD",
        menu_phase="none",
        position=MapPosition((1, 4), (6, 12), "Down", "Birch's Lab"),
    )
    mode._planner.sync_progress(lab)
    mode._advice = _advice(
        "Reconsider which recent conversation is still unfinished.",
        "walk:1:4:6:12",
    )
    mode._planner.accept(lab, mode._advice)
    mode._planner.mark_advice_followed("walk:1:4:6:12")
    town = replace(
        lab,
        position=MapPosition((0, 9), (10, 9), "Up", "Littleroot Town"),
    )
    may_house = Action("walk:0:9:14:8", "Enter May's House", town.context_id)
    mode._latest_observation = town
    mode._available_actions = (may_house,)

    mode._start_model_request((may_house,), attempt=1)

    request = next(
        json.loads(line)
        for line in telemetry._path.read_text().splitlines()
        if json.loads(line)["event"] == "request"
    )
    assert request["state"]["decision_brief"]["planner_hint"] is None
    assert "planner_follow_up" not in request["state"]["decision_brief"]
    instructions = request["questions"]["action"]["instructions"]
    assert "Reconsider which recent conversation" not in instructions
    assert "Do not repeat the blocked route" not in instructions
    assert "Exact location" not in instructions
    assert mode.planner_view["advice"] is None
    assert len(worker.futures) == 1


def test_unfollowed_hint_expires_when_jev_leaves_by_another_action(mode_runtime):
    mode, reader, _, worker, _, telemetry = mode_runtime
    mode._planner = PlannerMemory(
        ledger=EvidenceLedger(telemetry._path.parent / "memory.json")
    )
    lab = replace(
        reader.observation,
        game_state="OVERWORLD",
        menu_phase="none",
        position=MapPosition((1, 4), (6, 12), "Down", "Birch's Lab"),
    )
    mode._planner.sync_progress(lab)
    mode._advice = _advice("Use the south door.", "walk:1:4:6:12")
    mode._planner.accept(lab, mode._advice)
    town = replace(
        lab,
        position=MapPosition((0, 9), (10, 9), "Up", "Littleroot Town"),
    )
    may_house = Action("walk:0:9:14:8", "Enter May's House", town.context_id)
    mode._latest_observation = town
    mode._available_actions = (may_house,)

    mode._start_model_request((may_house,), attempt=1)

    request = next(
        json.loads(line)
        for line in telemetry._path.read_text().splitlines()
        if json.loads(line)["event"] == "request"
    )
    assert "planner_follow_up" not in request["state"]["decision_brief"]
    assert mode.planner_view["advice"] is None
    assert len(worker.futures) == 1


def test_story_progress_discards_active_advice_without_calling_planner(mode_runtime):
    mode, reader, _, _, _, telemetry = mode_runtime
    mode._planner = PlannerMemory()
    mode._planner.sync_progress(reader.observation)
    mode._advice = _advice("Old recovery hint.")
    reader.observation = replace(reader.observation, rival_house_state=3)

    run = mode.run()
    try:
        next(run)
        request = next(
            event for event in map(json.loads, telemetry._path.read_text().splitlines())
            if event["event"] == "request"
        )
        assert "Old recovery hint" not in request["questions"]["action"]["instructions"]
        assert mode.planner_view["calls"] == 0
    finally:
        run.close()


@pytest.mark.parametrize("invalidate", ["pause", "story"])
def test_late_planner_advice_cannot_survive_pause_or_story_change(mode_runtime, invalidate):
    mode, reader, actions, worker, _, telemetry = mode_runtime
    mode._planner = _stuck_planner(reader, telemetry._path.parent / "memory.json")
    run = mode.run()
    try:
        next(run)
        if invalidate == "pause":
            mode.set_paused(True)
            mode.set_paused(False)
        else:
            reader.observation = replace(reader.observation, rival_house_state=3)
        worker.futures[0].set_result(_advice("stale advice", destination_action_id="talk:1"))
        next(run)
        assert mode.planner_view["advice"] is None
        assert actions.executed == []
        events = list(map(json.loads, telemetry._path.read_text().splitlines()))
        assert next(e for e in events if e["event"] == "planner-response")["disposition"] == "stale"
    finally:
        run.close()


def test_planner_failure_pauses_without_a_silent_hint_fallback(mode_runtime):
    mode, reader, actions, worker, _, telemetry = mode_runtime
    mode._planner = _stuck_planner(reader, telemetry._path.parent / "memory.json")
    run = mode.run()
    try:
        next(run)
        worker.futures[0].set_exception(JevTimeoutError("deadline"))
        next(run)
        assert mode.paused
        assert telemetry.snapshot.last_error == "Planner: deadline"
        assert actions.executed == []
        assert mode.planner_view["advice"] is None
    finally:
        run.close()


def test_failed_planner_refresh_keeps_existing_advice_and_play_continues(mode_runtime):
    mode, reader, actions, worker, _, telemetry = mode_runtime
    mode._planner = _stuck_planner(reader, telemetry._path.parent / "memory.json")
    mode._advice = _advice("Keep leaving the lab.", destination_action_id="talk:1")
    run = mode.run()
    try:
        next(run)
        worker.futures[0].set_exception(JevTimeoutError("deadline"))
        next(run)

        assert not mode.paused
        assert mode.planner_view["advice"]["hint"] == "Keep leaving the lab."
        assert telemetry.snapshot.last_error is None

        next(run)
        assert len(worker.futures) == 2
        assert actions.executed == []
    finally:
        run.close()


def test_context_change_discards_late_model_choice(mode_runtime):
    mode, reader, actions, worker, _, telemetry = mode_runtime
    run = mode.run()
    next(run)

    reader.observation = _observation("different-context")
    worker.futures[0].set_result(_choice("starter:treecko"))
    next(run)

    assert actions.executed == []
    assert telemetry.snapshot.last_error == "discarded stale model response"
    response_event = next(
        event
        for event in map(json.loads, telemetry._path.read_text().splitlines())
        if event["event"] == "response"
    )
    assert response_event["disposition"] == "stale"
    assert response_event["choice"] == "starter:treecko"
    assert dict(response_event["probabilities"])["starter:treecko"] == 0.6


@pytest.mark.parametrize("coach", [False, True])
def test_moving_npc_keeps_same_semantic_choice_valid(mode_runtime, coach):
    mode, reader, actions, worker, _, telemetry = mode_runtime
    memory = _stuck_planner(reader, telemetry._path.parent / "memory.json")
    mode._planner = memory if coach else None
    run = mode.run()
    try:
        next(run)
        reader.observation = replace(reader.observation, objects=(
            replace(reader.observation.objects[0], coordinates=(15, 3)),
            *reader.observation.objects[1:],
        ))
        worker.futures[0].set_result(_advice("Talk to the person.", destination_action_id="talk:1") if coach else _choice("talk:1"))
        next(run)
        assert telemetry.snapshot.last_error is None
        if coach:
            assert mode.planner_view["advice"]["destination_action_id"] == "talk:1"
        else:
            assert actions.executed[0].id == "talk:1"
            assert "(15, 3)" in actions.executed[0].label
    finally:
        run.close()


def test_pause_generation_discards_late_model_choice(mode_runtime):
    mode, _, actions, worker, _, telemetry = mode_runtime
    run = mode.run()
    next(run)

    mode.set_paused(True)
    worker.futures[0].set_result(_choice("starter:treecko"))
    next(run)

    assert actions.executed == []
    assert mode.paused is True
    assert telemetry.snapshot.phase == "paused"


@pytest.mark.parametrize("response", ["starter:treecko", "not-an-option", JevTimeoutError("late timeout")])
def test_pause_resume_keeps_old_request_single_and_discards_its_result(mode_runtime, response):
    mode, _, actions, worker, _, _ = mode_runtime
    run = mode.run()
    try:
        next(run)
        mode.set_paused(True)
        next(run)
        mode.set_paused(False)
        next(run)
        assert len(worker.futures) == 1
        if isinstance(response, Exception):
            worker.futures[0].set_exception(response)
        else:
            worker.futures[0].set_result(_choice(response))
        next(run)
        assert actions.executed == []
        assert mode.paused is False
        assert len(worker.futures) == 1
        next(run)
        assert len(worker.futures) == 2
    finally:
        run.close()


def test_pause_during_selection_clears_choice_and_resume_requests_again(
    mode_runtime, monkeypatch: pytest.MonkeyPatch
):
    mode, _, actions, worker, _, telemetry = mode_runtime
    original_selected = telemetry.selected

    def pause_while_recording(**kwargs):
        original_selected(**kwargs)
        mode.set_paused(True)

    monkeypatch.setattr(telemetry, "selected", pause_while_recording)
    run = mode.run()
    next(run)
    worker.futures[0].set_result(_choice("starter:treecko"))
    next(run)

    assert mode.paused is True
    assert actions.executed == []
    assert telemetry.snapshot.phase == "paused"

    mode.set_paused(False)
    next(run)

    assert len(worker.futures) == 2
    assert actions.executed == []


def test_pause_while_claiming_choice_never_executes_a_cleared_action(
    mode_runtime, monkeypatch: pytest.MonkeyPatch
):
    from jev_plays_emerald.actions import ActionExecutor, ExecutionPlan
    from jev_plays_emerald.mode import _ModeFrameBoundary

    mode, _, _, worker, emulator, _ = mode_runtime
    paused = Event()

    def pause():
        mode.set_paused(True)
        paused.set()

    pause_thread = Thread(target=pause)
    original_setattr = type(mode).__setattr__

    def pause_at_claim(self, name, value):
        original_setattr(self, name, value)
        if self is mode and name == "_last_started_action" and value is not None:
            pause_thread.start()
            # Let the pause complete here if the claim does not hold the lock.
            # Otherwise it completes once the owner finishes claiming the action.
            if not mode._state_lock.locked():
                assert paused.wait(2)

    def input_frames():
        emulator.press_button("A")
        yield

    executor = ActionExecutor(
        _ModeFrameBoundary(mode),
        dispatch={
            "starter": lambda action: ExecutionPlan(
                input_frames, frozenset({"CHOOSE_STARTER"}), frozenset({"starter"})
            )
        },
    )
    original_execute = executor.execute

    def execute_after_pause(action):
        # This generator runs outside the claim, just as the real executor does.
        assert paused.wait(2)
        yield from original_execute(action)

    monkeypatch.setattr(type(mode), "__setattr__", pause_at_claim)
    monkeypatch.setattr(executor, "execute", execute_after_pause)
    mode._executor = executor
    run = mode.run()
    try:
        next(run)
        worker.futures[0].set_result(_choice("starter:treecko"))
        next(run)

        assert mode.paused is True
        assert emulator.pressed == []
        assert mode.recent_outcomes[-1].outcome is Outcome.INTERRUPTED
        mode.set_paused(False)
        next(run)
        assert len(worker.futures) == 2
        assert emulator.pressed == []
    finally:
        run.close()
        pause_thread.join(timeout=2)


def test_pause_during_deterministic_selection_discards_old_context(
    mode_runtime, monkeypatch: pytest.MonkeyPatch
):
    mode, reader, actions, _, _, telemetry = mode_runtime
    reader.observation = _observation("before-pause", actions=1)
    original_selected = telemetry.selected

    def pause_while_recording(**kwargs):
        original_selected(**kwargs)
        mode.set_paused(True)

    monkeypatch.setattr(telemetry, "selected", pause_while_recording)
    run = mode.run()
    try:
        next(run)
        assert actions.executed == []
        monkeypatch.setattr(telemetry, "selected", original_selected)
        reader.observation = _observation("after-resume", actions=1)
        mode.set_paused(False)
        next(run)
        assert [action.context_id for action in actions.executed] == ["after-resume"]
    finally:
        run.close()


def test_pause_resume_before_retry_discards_the_old_request(
    mode_runtime, monkeypatch: pytest.MonkeyPatch
):
    mode, _, actions, worker, _, telemetry = mode_runtime
    original_start = mode._start_model_request

    def pause_before_retry(*args, **kwargs):
        if kwargs["attempt"] > 1:
            mode.set_paused(True)
            mode.set_paused(False)
        original_start(*args, **kwargs)

    monkeypatch.setattr(mode, "_start_model_request", pause_before_retry)
    run = mode.run()
    try:
        next(run)
        worker.futures[0].set_exception(JevTimeoutError("request timed out"))
        next(run)
        assert len(worker.futures) == 1
        assert actions.executed == []
        next(run)
        requests = [
            event
            for event in map(json.loads, telemetry._path.read_text().splitlines())
            if event["event"] == "request"
        ]
        assert [request["attempt"] for request in requests] == [1, 1]
    finally:
        run.close()


@pytest.mark.parametrize(
    "error",
    [
        JevGatewayError("rate limited", status_code=429),
        JevGatewayError("provider unavailable", status_code=503),
        JevTimeoutError("request timed out"),
        JevResponseError("choice did not match its probabilities"),
    ],
)
def test_transient_failures_retry_twice_then_pause(mode_runtime, error: Exception):
    mode, _, actions, worker, _, telemetry = mode_runtime
    run = mode.run()
    next(run)

    for attempt in range(3):
        worker.futures[attempt].set_exception(error)
        next(run)

    assert len(worker.futures) == 3
    assert actions.executed == []
    assert mode.paused is True
    assert telemetry.snapshot.phase == "error"
    assert telemetry.snapshot.last_error == str(error)
    events = list(map(json.loads, telemetry._path.read_text().splitlines()))
    assert [event["attempt"] for event in events if event["event"] == "request"] == [
        1,
        2,
        3,
    ]
    failures = [event for event in events if event["event"] == "request-error"]
    assert [event["attempt"] for event in failures] == [1, 2, 3]
    assert [event["retry"] for event in failures] == [True, True, False]
    assert all(event["stale"] is False and event["context_id"] == "starter-context" for event in failures)
    from jev_plays_emerald.report import summarize
    accounting = summarize(telemetry._path)["jev"]
    assert accounting["error_events"] == 3
    assert accounting["unresolved_requests"] == 0
    assert accounting["input_tokens"] == 0


@pytest.mark.parametrize(
    "error",
    [
        JevGatewayError("authentication failed", status_code=401),
        ValueError("malformed choice response"),
    ],
)
def test_nontransient_failure_pauses_without_retry(mode_runtime, error: Exception):
    mode, _, actions, worker, _, telemetry = mode_runtime
    run = mode.run()
    next(run)

    worker.futures[0].set_exception(error)
    next(run)

    assert len(worker.futures) == 1
    assert actions.executed == []
    assert mode.paused is True
    assert telemetry.snapshot.last_error == str(error)


def test_singleton_action_is_deterministic_and_never_calls_model(mode_runtime):
    mode, reader, actions, worker, _, telemetry = mode_runtime
    reader.observation = _observation("forced-move", actions=1)

    next(mode.run())

    assert [action.id for action in actions.executed] == ["battle-move:0"]
    assert worker.futures == []
    assert telemetry.snapshot.last_decision is not None
    assert telemetry.snapshot.last_decision.source == "deterministic"
    assert telemetry.snapshot.last_decision.probabilities is None


def test_closing_mode_closes_active_executor_before_releasing_inputs(mode_runtime):
    mode, reader, actions, _, emulator, _ = mode_runtime
    reader.observation = _observation("forced-move", actions=1)
    run = mode.run()
    next(run)
    resets_before_close = emulator.reset_count

    run.close()

    assert actions.close_count == 1
    assert actions.current_action is None
    assert emulator.reset_count > resets_before_close


def test_battle_dialogue_advances_without_a_model_request(mode_runtime):
    mode, reader, actions, worker, emulator, _ = mode_runtime
    observation = _observation("battle-dialogue", actions=1)
    reader.observation = Observation(
        **{
            **observation.__dict__,
            "battle_phase": "no",
        }
    )

    next(mode.run())

    assert emulator.pressed == ["B"]
    assert actions.executed == []
    assert worker.futures == []


def test_trainer_approach_returns_control_when_custom_battle_starts(mode_runtime, monkeypatch):
    from modules.memory import GameState
    from modules.modes import _listeners

    _, _, _, _, emulator, _ = mode_runtime
    monkeypatch.setattr(emulator, "restore_held_buttons", lambda _: None, raising=False)
    game_state = GameState.OVERWORLD
    monkeypatch.setattr(_listeners, "get_game_state", lambda: game_state)
    monkeypatch.setattr(_listeners, "get_global_script_context", lambda: SimpleNamespace(is_active=True))
    approach = _listeners.TrainerApproachListener().handle_trainer_approach()
    next(approach)
    assert emulator.pressed == ["B"]
    game_state = GameState.BATTLE
    with pytest.raises(StopIteration):
        next(approach)
    assert emulator.pressed == ["B"]


@pytest.mark.parametrize("paused", [False, True])
def test_whiteout_listener_returns_control_to_jev(mode_runtime, monkeypatch, paused):
    from modules.battle_state import BattleOutcome
    from modules.memory import GameState
    from modules.modes import _listeners

    mode, reader, actions, worker, emulator, telemetry = mode_runtime
    game_state = GameState.WHITEOUT
    monkeypatch.setattr(_listeners, "get_game_state", lambda: game_state)
    monkeypatch.setattr(_listeners, "get_global_script_context", lambda: SimpleNamespace(stack=[]))
    monkeypatch.setattr(_listeners, "task_is_active", lambda _: False)
    monkeypatch.setattr(_listeners, "player_avatar_is_standing_still", lambda: True)
    monkeypatch.setattr(_listeners, "plugin_whiteout", lambda: None)
    manual_switches = []
    monkeypatch.setattr(_listeners.context, "set_manual_mode", lambda: manual_switches.append(True))

    mode.on_battle_ended(BattleOutcome.Lost)
    mode.set_paused(paused)
    recovery = _listeners.WhiteoutListener().handle_whiteout_dialogue(mode)
    next(recovery)
    assert emulator.pressed == ["B"]
    game_state = GameState.OVERWORLD
    with pytest.raises(StopIteration):
        next(recovery)

    assert manual_switches == []
    assert not mode.completed
    assert mode.paused is paused
    assert worker.futures == []
    events = [json.loads(line) for line in telemetry._path.read_text().splitlines()]
    assert {"event": "battle-ended", "outcome": "Lost"} in events

    # The next normal frame can resume decisions, while an explicit pause survives.
    reader.observation = _observation()
    run = mode.run()
    try:
        next(run)
        assert len(worker.futures) == (0 if paused else 1)
        assert actions.executed == []
    finally:
        run.close()


@pytest.mark.parametrize(
    ("starter", "direction"),
    [("treecko", "Left"), ("torchic", None), ("mudkip", "Right")],
)
def test_starter_executor_uses_choice_screen_and_confirms_acquired_species(
    monkeypatch: pytest.MonkeyPatch,
    starter: str,
    direction: str | None,
):
    sys.path.insert(0, str(POKEBOT_ROOT))
    from modules import pokemon_party
    from modules.context import context
    from modules.modes import util
    from jev_plays_emerald.actions import _starter_plan

    buttons: list[str] = []
    party_reads = 0

    class Emulator:
        def press_button(self, button: str) -> None:
            buttons.append(button)

    def get_party():
        nonlocal party_reads
        party_reads += 1
        if party_reads < 2:
            return ()
        return (SimpleNamespace(species=SimpleNamespace(name=starter.title())),)

    def no_frames(*_args, **_kwargs):
        if False:
            yield

    previous_emulator = context.emulator
    try:
        context.emulator = Emulator()
        monkeypatch.setattr(pokemon_party, "get_party", get_party)
        monkeypatch.setattr(util, "ensure_facing_direction", no_frames)
        monkeypatch.setattr(util, "wait_until_task_is_active", no_frames)
        action = Action(f"starter:{starter}", f"Choose {starter.title()}", "choice")

        list(_starter_plan(action).start())

        assert party_reads >= 2
        assert (direction in buttons) is (direction is not None)
        assert "A" in buttons
    finally:
        context.emulator = previous_emulator
        sys.path.remove(str(POKEBOT_ROOT))


def test_starter_executor_allows_post_selection_transition() -> None:
    from jev_plays_emerald.actions import _starter_plan

    action = Action("starter:treecko", "Choose Treecko", "choice")

    assert "UNKNOWN" in _starter_plan(action).allowed_states


def test_the_run_log_is_anchored_to_the_project(monkeypatch):
    """PokéBot is launched with its own working directory.

    A relative default sent every real run's decisions into the disposable
    upstream checkout, while the corpus the replay harness reads stayed empty.
    """

    monkeypatch.undo()  # the suite-wide fixture redirects this to a tmp log
    from jev_plays_emerald import telemetry

    expected = Path(__file__).resolve().parents[1] / "runs" / "decisions.jsonl"
    assert telemetry.RUN_LOG == expected
    assert telemetry.DecisionTelemetry()._path == expected


def test_telemetry_status_is_immutable_and_jsonl_excludes_request_secrets(tmp_path: Path):
    from jev_plays_emerald.telemetry import DecisionTelemetry

    path = tmp_path / "decisions.jsonl"
    telemetry = DecisionTelemetry(path)
    action = Action("starter:treecko", "Choose Treecko", "starter-context")
    telemetry.observe(action.context_id, (action,))
    telemetry.requested(
        context_id=action.context_id,
        attempt=1,
        state={"game_state": "CHOOSE_STARTER"},
        criteria={action.id: action.label},
        instructions="Choose exactly one legal action.",
    )
    telemetry.selected(
        context_id=action.context_id,
        source="model",
        action_id=action.id,
        result=JevChoice(
            choice=action.id,
            probabilities=MappingProxyType({action.id: 1.0}),
            confidence=0.9,
            usage=TokenUsage(10, 2),
            latency_ms=4.5,
        ),
    )
    telemetry.outcome(action, Outcome.SUCCESS, None)
    telemetry.battle_ended("Won")

    with pytest.raises(FrozenInstanceError):
        telemetry.snapshot.phase = "tampered"  # type: ignore[misc]
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "request",
        "decision",
        "outcome",
        "battle-ended",
    ]
    assert events[0] == {
        "event": "request",
        "source": "model",
        "context_id": "starter-context",
        "attempt": 1,
        "state": {"game_state": "CHOOSE_STARTER"},
        "questions": {
            "action": {
                "type": "choice",
                "instructions": "Choose exactly one legal action.",
                "criteria": {"starter:treecko": "Choose Treecko"},
            }
        },
    }
    assert events[1]["source"] == "model"
    assert events[1]["usage"] == {"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": None}
    assert telemetry.snapshot.last_battle_outcome == "Won"
    assert "api_key" not in path.read_text().casefold()
