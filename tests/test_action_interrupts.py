from collections.abc import Generator
from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
from threading import Thread
from types import SimpleNamespace

import pytest

from jev_plays_emerald.actions import (
    Action,
    ActionExecutor,
    ExecutionPlan,
    FrameState,
    NavigationBlocked,
    Outcome,
)
from jev_plays_emerald.opening import legal_actions
from jev_plays_emerald.state import (
    ActiveBattler,
    InventoryItem,
    MapPosition,
    MoveState,
    Observation,
    ObservationReader,
    OpponentBattler,
    OpeningFlags,
    PartyMember,
    observation_context_id,
)


class FakeFrameBoundary:
    def __init__(self) -> None:
        self.state = FrameState("OVERWORLD")
        self.context_id = "overworld:route101"
        self.held_buttons: set[str] = set()

    def read_frame_state(self) -> FrameState:
        return self.state

    def read_context_id(self) -> str:
        return self.context_id

    def reset_held_buttons(self) -> None:
        self.held_buttons.clear()


@pytest.fixture
def navigation_fixture():
    class NavigationFixture:
        def __init__(self) -> None:
            self.boundary = FakeFrameBoundary()
            self.steps = 0

            def walk(_: Action) -> Generator[None, None, None]:
                while True:
                    self.steps += 1
                    self.boundary.held_buttons.add("Right")
                    yield

            self.executor = ActionExecutor(
                self.boundary,
                dispatch={"walk": lambda action: ExecutionPlan(lambda: walk(action), frozenset({"OVERWORLD"}))},
            )

        @property
        def held_buttons(self) -> set[str]:
            return self.boundary.held_buttons

        def begin_walk(self):
            action = Action("walk:0:16:8:7", "Walk north", self.boundary.context_id)
            return self.executor.execute(action)

        def enter_battle(self) -> None:
            self.boundary.state = FrameState("BATTLE", "action")

    return NavigationFixture()


def test_actions_are_immutable() -> None:
    action = Action("walk:0:16:8:7", "Walk north", "overworld:route101")

    with pytest.raises(FrozenInstanceError):
        action.label = "Changed"  # type: ignore[misc]


def test_battle_interrupts_navigation(navigation_fixture) -> None:
    run = navigation_fixture.begin_walk()
    assert next(run) is None
    assert navigation_fixture.held_buttons == {"Right"}

    navigation_fixture.enter_battle()

    assert next(run) is Outcome.INTERRUPTED
    assert navigation_fixture.held_buttons == set()
    assert navigation_fixture.steps == 1
    assert navigation_fixture.executor.last_reason == "game changed to BATTLE"


def test_unexpected_menu_interrupts_navigation(navigation_fixture) -> None:
    run = navigation_fixture.begin_walk()
    assert next(run) is None

    navigation_fixture.boundary.state = FrameState("OVERWORLD", "start")

    assert next(run) is Outcome.INTERRUPTED
    assert navigation_fixture.held_buttons == set()


def test_navigation_replans_twice_then_fails() -> None:
    boundary = FakeFrameBoundary()
    attempts = 0

    def blocked_walk() -> Generator[None, None, None]:
        nonlocal attempts
        attempts += 1
        boundary.held_buttons.add("Up")
        raise NavigationBlocked("NPC did not move")
        yield

    executor = ActionExecutor(
        boundary,
        dispatch={
            "walk": lambda _: ExecutionPlan(
                blocked_walk,
                frozenset({"OVERWORLD"}),
                retry_on=(NavigationBlocked,),
            )
        },
        max_navigation_replans=2,
    )
    run = executor.execute(Action("walk:0:9:8:4", "Cross town", boundary.context_id))

    assert next(run) is Outcome.FAILED
    assert attempts == 3
    assert boundary.held_buttons == set()
    assert executor.failure_reason == "navigation remained blocked after 2 replans"


def test_timeout_fails_and_releases_input() -> None:
    boundary = FakeFrameBoundary()

    def endless_walk() -> Generator[None, None, None]:
        while True:
            boundary.held_buttons.add("Down")
            yield

    executor = ActionExecutor(
        boundary,
        dispatch={"walk": lambda _: ExecutionPlan(endless_walk, frozenset({"OVERWORLD"}))},
        frame_limit=2,
    )
    run = executor.execute(Action("walk:0:9:8:4", "Cross town", boundary.context_id))

    assert next(run) is None
    assert next(run) is None
    assert next(run) is Outcome.FAILED
    assert boundary.held_buttons == set()
    assert executor.failure_reason == "action exceeded 2 frames"


def test_stale_context_is_rejected_before_input() -> None:
    boundary = FakeFrameBoundary()
    started = False

    def walk() -> Generator[None, None, None]:
        nonlocal started
        started = True
        yield

    executor = ActionExecutor(
        boundary,
        dispatch={"walk": lambda _: ExecutionPlan(walk, frozenset({"OVERWORLD"}))},
    )

    run = executor.execute(Action("walk:0:9:8:4", "Cross town", "old-context"))

    assert next(run) is Outcome.FAILED
    assert started is False
    assert executor.failure_reason == "action context changed before execution"


def test_interrupted_goal_must_be_legal_in_the_new_context(navigation_fixture) -> None:
    run = navigation_fixture.begin_walk()
    assert next(run) is None
    navigation_fixture.enter_battle()
    assert next(run) is Outcome.INTERRUPTED

    new_action = Action("walk:0:16:8:7", "Walk north", "overworld:route101:after-battle")
    unrelated = Action("talk:3", "Talk to trainer", new_action.context_id)

    assert navigation_fixture.executor.revalidate_interrupted([unrelated]) is None
    assert navigation_fixture.executor.revalidate_interrupted([unrelated, new_action]) == new_action


def test_observation_context_ignores_movement_frames_but_changes_at_a_menu() -> None:
    flags = OpeningFlags(False, False, False)
    party = (PartyMember("Torchic", 6, 20, 20, "Healthy", (MoveState("Scratch", 35, 35),)),)
    inventory = (InventoryItem("Potion", 1),)

    first = observation_context_id("OVERWORLD", (0, 16), "none", "none", party, inventory, flags)
    one_tile_later = observation_context_id("OVERWORLD", (0, 16), "none", "none", party, inventory, flags)
    battle_menu = observation_context_id("BATTLE", (0, 16), "battle", "action", party, inventory, flags)

    assert first == one_tile_later
    assert battle_menu != first


def test_observation_context_changes_with_observed_opponent_hp() -> None:
    flags = OpeningFlags(True, False, False)
    party = (PartyMember("Treecko", 5, 20, 20, "Healthy", ()),)
    opponent = OpponentBattler("Zigzagoon", 2, 13, 13, "Healthy")

    full_hp = observation_context_id(
        "BATTLE", (0, 16), "battle", "action", party, (), flags, opponent=opponent
    )
    damaged = observation_context_id(
        "BATTLE",
        (0, 16),
        "battle",
        "action",
        party,
        (),
        flags,
        opponent=OpponentBattler("Zigzagoon", 2, 7, 13, "Healthy"),
    )

    assert damaged != full_hp


def test_observation_is_deeply_immutable() -> None:
    observation = Observation(
        context_id="context",
        game_state="OVERWORLD",
        position=MapPosition((0, 16), (7, 14), "Up"),
        controllable=True,
        menu_phase="none",
        battle_phase="none",
        party=(),
        inventory=(),
        opening_flags=OpeningFlags(False, False, False),
        active_battler=None,
        recent_outcomes=(),
    )

    with pytest.raises(FrozenInstanceError):
        observation.controllable = False  # type: ignore[misc]


def test_observations_can_only_be_built_on_the_owner_thread() -> None:
    reader = ObservationReader()
    errors: list[BaseException] = []

    def read_from_other_thread() -> None:
        try:
            reader.read()
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=read_from_other_thread)
    thread.start()
    thread.join()

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert str(errors[0]) == "observations must be built on the emulator owner thread"


def test_starter_and_battle_actions_are_legal_semantic_choices() -> None:
    starter_observation = Observation(
        context_id="starter-context",
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
    battle_observation = Observation(
        context_id="battle-context",
        game_state="BATTLE",
        position=MapPosition((0, 16), (7, 14), "Up"),
        controllable=False,
        menu_phase="battle",
        battle_phase="action",
        party=(
            PartyMember(
                "Torchic",
                6,
                20,
                20,
                "Healthy",
                (MoveState("Scratch", 35, 35), MoveState("Growl", 40, 40)),
            ),
        ),
        inventory=(),
        opening_flags=OpeningFlags(True, False, False),
        active_battler=ActiveBattler(
            0,
            (MoveState("Scratch", 35, 35), MoveState("Growl", 40, 40)),
        ),
        recent_outcomes=(),
    )

    assert [(action.id, action.context_id) for action in legal_actions(starter_observation)] == [
        ("starter:treecko", "starter-context"),
        ("starter:torchic", "starter-context"),
        ("starter:mudkip", "starter-context"),
    ]
    assert [action.id for action in legal_actions(battle_observation)] == ["battle-move:0", "battle-move:1"]


def test_battle_actions_use_the_actual_active_battler_after_a_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    pokebot_root = Path(__file__).parents[1] / ".cache" / "pokebot-gen3"
    sys.path.insert(0, str(pokebot_root))
    try:
        import jev_plays_emerald.state as state_module
        from modules import battle_state, items, memory, player, pokemon_party
        from modules.memory import GameState

        def learned_move(name: str, move_type: str, power: int, pp: int):
            return SimpleNamespace(
                move=SimpleNamespace(
                    name=name,
                    type=SimpleNamespace(name=move_type),
                    base_power=power,
                    accuracy=1.0,
                    description=f"{name} description",
                ),
                pp=pp,
                total_pp=pp,
            )

        scratch = learned_move("Scratch", "Normal", 40, 35)
        water_gun = learned_move("Water Gun", "Water", 40, 25)

        def pokemon(species: str, move: SimpleNamespace) -> SimpleNamespace:
            return SimpleNamespace(
                species=SimpleNamespace(name=species),
                level=6,
                current_hp=20,
                total_hp=20,
                status_condition=SimpleNamespace(name="Healthy"),
                moves=(move, None, None, None),
            )

        from modules import tasks
        from modules.battle_strategies._util import BattleStrategyUtil
        monkeypatch.setattr(memory, "get_event_var", lambda _: 0)
        monkeypatch.setattr(player, "get_player", lambda: SimpleNamespace(gender="male"))
        monkeypatch.setattr(tasks, "get_tasks", lambda: ())
        monkeypatch.setattr(tasks, "get_global_script_context", lambda: SimpleNamespace(is_active=False))
        monkeypatch.setattr(BattleStrategyUtil, "get_escape_chance", lambda _: 0)
        monkeypatch.setattr(memory, "get_game_state", lambda: GameState.BATTLE)
        monkeypatch.setattr(memory, "get_event_flag", lambda _: False)
        monkeypatch.setattr(player, "get_player_avatar", lambda: (_ for _ in ()).throw(RuntimeError()))
        monkeypatch.setattr(player, "player_avatar_is_controllable", lambda: False)
        monkeypatch.setattr(pokemon_party, "get_party", lambda: (pokemon("Torchic", scratch), pokemon("Mudkip", water_gun)))
        monkeypatch.setattr(
            items,
            "get_item_bag",
            lambda: SimpleNamespace(items=(), key_items=(), poke_balls=(), tms_hms=(), berries=()),
        )
        monkeypatch.setattr(
            battle_state,
            "get_battle_state",
                lambda: SimpleNamespace(
                    is_trainer_battle=False,
                    own_side=SimpleNamespace(
                        active_battler=SimpleNamespace(
                            party_index=1,
                            moves=(water_gun,),
                            can_use_move=lambda _: True,
                        )
                    ),
                    opponent=SimpleNamespace(active_battler=None),
                ),
        )
        monkeypatch.setattr(state_module, "_read_phases", lambda _: ("battle", "action"))

        observation = ObservationReader().read()

        assert observation.active_battler == ActiveBattler(
            1,
            (
                MoveState(
                    "Water Gun",
                    25,
                    25,
                    "Water",
                    40,
                    1.0,
                    "Water Gun description",
                ),
            ),
        )
        [(action_id, label)] = [
            (action.id, action.label) for action in legal_actions(observation)
        ]
        assert action_id == "battle-move:0"
        assert label == (
            "Use Water Gun (Water, 25/25 PP, 100% accuracy, 40 power). "
            "Water Gun description"
        )
    finally:
        sys.path.remove(str(pokebot_root))


def test_talk_allows_its_expected_script_but_interrupts_for_a_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    pokebot_root = Path(__file__).parents[1] / ".cache" / "pokebot-gen3"
    sys.path.insert(0, str(pokebot_root))
    try:
        from modules.modes.util import higher_level_actions

        boundary = FakeFrameBoundary()

        def talk_to_npc(_: int) -> Generator[None, None, None]:
            yield

        monkeypatch.setattr(higher_level_actions, "talk_to_npc", talk_to_npc)
        executor = ActionExecutor(boundary)
        action = Action("talk:3", "Talk", boundary.context_id)
        run = executor.execute(action)
        assert next(run) is None

        boundary.state = FrameState("OVERWORLD", "script")
        assert next(run) is Outcome.SUCCESS

        boundary.state = FrameState("OVERWORLD")
        run = executor.execute(action)
        assert next(run) is None
        boundary.state = FrameState("OVERWORLD", "start")
        assert next(run) is Outcome.INTERRUPTED
    finally:
        sys.path.remove(str(pokebot_root))


def test_mode_tracks_an_action_until_its_terminal_outcome() -> None:
    pokebot_root = Path(__file__).parents[1] / ".cache" / "pokebot-gen3"
    sys.path.insert(0, str(pokebot_root))
    from jev_plays_emerald.mode import JevEmeraldMode

    observation = Observation(
        context_id="starter-context",
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

    class FakeReader:
        def read(self, recent_outcomes=()):
            return observation

    class FakeExecutor:
        current_action = None
        interrupted_action = None
        failure_reason = None
        last_reason = None

        def execute(self, action):
            self.current_action = action
            yield None
            self.current_action = None
            yield Outcome.SUCCESS

        def revalidate_interrupted(self, actions):
            return None

    try:
        mode = JevEmeraldMode(observation_reader=FakeReader(), executor=FakeExecutor())
        run = mode.run()
        assert next(run) is None
        [treecko, _, _] = mode.available_actions

        mode.submit_action(treecko)

        assert next(run) is None
        assert mode.active_action == treecko
        assert next(run) is None
        assert mode.active_action is None
        assert mode.recent_outcomes[-1].action_id == "starter:treecko"
        assert mode.recent_outcomes[-1].outcome is Outcome.SUCCESS
    finally:
        sys.path.remove(str(pokebot_root))
