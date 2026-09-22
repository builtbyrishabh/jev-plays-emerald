from concurrent.futures import Future
from dataclasses import FrozenInstanceError
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
    JevTimeoutError,
    TokenUsage,
)
from jev_plays_emerald.state import (
    ActiveBattler,
    MapPosition,
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
    assert response_event["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert response_event["cost"] == {
        "estimated_usd": 0.0000042,
        "input_usd_per_token": 0.000000042,
        "output_usd_per_token": 0.0,
        "source": "https://ai-gateway.vercel.sh/v1/models",
        "checked_on": "2026-09-20",
    }
    assert telemetry.snapshot.last_decision is not None
    assert telemetry.snapshot.last_decision.source == "model"


def test_planner_advice_replaces_hints_and_jev_keeps_all_choices(mode_runtime):
    from jev_plays_emerald.planner import PlannerAdvice, PlannerMemory

    mode, _, actions, worker, emulator, telemetry = mode_runtime
    mode._planner = PlannerMemory()
    run = mode.run()
    try:
        next(run)
        assert mode.planner_view["pending"]
        assert actions.executed == []
        next(run)
        assert len(worker.futures) == 1
        assert emulator.reset_count > 0
        worker.futures[0].set_result(PlannerAdvice("Pick the companion you prefer.", "test", TokenUsage(40, 10), 8))
        next(run)
        next(run)
        events = list(map(json.loads, telemetry._path.read_text().splitlines()))
        request = next(event for event in events if event["event"] == "request")
        assert request["questions"]["action"]["instructions"].endswith("Pick the companion you prefer.")
        assert "Expect the rival" not in request["questions"]["action"]["instructions"]
        assert len(request["questions"]["action"]["criteria"]) == 3
        worker.futures[1].set_result(_choice("starter:mudkip"))
        next(run)
        assert actions.executed[0].id == "starter:mudkip"
        assert mode.planner_view["calls"] == 1
    finally:
        run.close()


@pytest.mark.parametrize("invalidate", ["pause", "story"])
def test_late_planner_advice_cannot_survive_pause_or_story_change(mode_runtime, invalidate):
    from dataclasses import replace
    from jev_plays_emerald.planner import PlannerAdvice, PlannerMemory

    mode, reader, actions, worker, _, telemetry = mode_runtime
    mode._planner = PlannerMemory()
    run = mode.run()
    try:
        next(run)
        if invalidate == "pause":
            mode.set_paused(True)
            mode.set_paused(False)
        else:
            reader.observation = replace(reader.observation, rival_house_state=3)
        worker.futures[0].set_result(PlannerAdvice("stale advice", "test", TokenUsage(), 1))
        next(run)
        assert mode.planner_view["advice"] is None
        assert actions.executed == []
        events = list(map(json.loads, telemetry._path.read_text().splitlines()))
        assert next(e for e in events if e["event"] == "planner-response")["disposition"] == "stale"
    finally:
        run.close()


def test_planner_failure_pauses_without_a_silent_hint_fallback(mode_runtime):
    from jev_plays_emerald.planner import PlannerMemory

    mode, _, actions, worker, _, telemetry = mode_runtime
    mode._planner = PlannerMemory()
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
    events = map(json.loads, telemetry._path.read_text().splitlines())
    assert [event["attempt"] for event in events if event["event"] == "request"] == [
        1,
        2,
        3,
    ]


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
    assert events[1]["usage"] == {"input_tokens": 10, "output_tokens": 2}
    assert telemetry.snapshot.last_battle_outcome == "Won"
    assert "api_key" not in path.read_text().casefold()
