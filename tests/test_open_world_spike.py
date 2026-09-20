"""Spike guards: the wide menu must stay stable and stay executable."""

from dataclasses import dataclass, replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / ".cache/pokebot-gen3"))
import pytest

from jev_plays_emerald.actions import ACTION_EXECUTORS
from jev_plays_emerald.opening import legal_actions, open_world_actions
from jev_plays_emerald.state import MapPosition, Observation, OpeningFlags, PartyMember, MoveState


@dataclass
class FakeWarp:
    local_coordinates: tuple[int, int]
    destination_name: str
    destination_id: tuple[int, int] = (0, 9)

    @property
    def destination_location(self):
        return type(
            "Destination",
            (),
            {"map_name": self.destination_name, "map_group_and_number": self.destination_id},
        )()


@dataclass
class FakeTemplate:
    local_id: int
    local_coordinates: tuple[int, int]
    kind: str = "normal"
    trainer_type: str = "None"
    script_symbol: str = "LittlerootTown_EventScript_Mom"
    is_trainer_defeated: bool = False


@dataclass
class FakeBgEvent:
    local_coordinates: tuple[int, int]
    kind: str = "Script"
    script_symbol: str = "LittlerootTown_BrendansHouse_2F_EventScript_Clock"


class FakeLocation:
    map_group_and_number = (0, 10)
    map_size = (20, 20)
    map_name = "Oldale Town"

    def __init__(self, warps, objects, bg_events=(), connections=()):
        self.warps = warps
        self.objects = objects
        self.bg_events = list(bg_events)
        self.connections = list(connections)


@pytest.fixture
def map_data(monkeypatch):
    """Static map definitions plus which NPC slots are currently loaded."""

    from modules import map as map_module

    location = FakeLocation(
        warps=[
            FakeWarp((5, 3), "OLDALE TOWN", destination_id=(0, 10)),
            FakeWarp((12, 8), "ROUTE 103", destination_id=(0, 18)),
        ],
        objects=[
            FakeTemplate(local_id=1, local_coordinates=(7, 7)),
            FakeTemplate(local_id=2, local_coordinates=(9, 4), trainer_type="Normal"),
            FakeTemplate(local_id=3, local_coordinates=(2, 2), kind="clone"),
        ],
        bg_events=[FakeBgEvent((3, 1))],
    )
    loaded = [type("Obj", (), {"local_id": i})() for i in (1, 2)]
    monkeypatch.setattr(map_module, "get_map_data_for_current_position", lambda: location, raising=False)
    monkeypatch.setattr(map_module, "get_map_objects", lambda: loaded, raising=False)
    return location


def observation(**changes):
    member = PartyMember("Treecko", 5, 20, 20, "Healthy", (MoveState("Pound", 35, 35),))
    base = Observation(
        "ctx", "OVERWORLD", MapPosition((0, 10), (10, 4), "Up"), True, "none", "none",
        (member,), (), OpeningFlags(True, False, False), None, (),
    )
    return replace(base, **changes)


def test_offered_set_is_identical_across_frames(map_data):
    """A churning menu would discard every in-flight decision and stall the run."""

    first = open_world_actions(observation())
    second = open_world_actions(observation())
    assert first == second
    assert len(first) > 2


def test_every_offered_action_has_an_executor(map_data):
    for action in open_world_actions(observation()):
        kind = action.id.partition(":")[0]
        assert kind in ACTION_EXECUTORS, f"{action.id} has no executor"


def test_unloaded_and_clone_objects_are_not_offered(map_data):
    offered = {action.id for action in open_world_actions(observation())}
    assert "talk:1" in offered and "talk:2" in offered
    assert "talk:3" not in offered  # clone template, never loaded


def test_menu_contains_no_story_destination(map_data):
    """The point of the spike: primitives only, no "go fight the rival"."""

    offered = {action.id for action in open_world_actions(observation())}
    assert not any(action.startswith(("goal:", "heal:")) for action in offered)
    assert "interact:3:1" in offered  # bg events keep the clock reachable


def test_flag_off_keeps_the_scripted_single_goal(monkeypatch):
    monkeypatch.delenv("JEV_OPEN_WORLD_SPIKE", raising=False)
    offered = legal_actions(observation())
    assert [action.id for action in offered] == ["goal:rival"]


def test_flag_on_replaces_every_overworld_goal(map_data, monkeypatch):
    monkeypatch.setenv("JEV_OPEN_WORLD_SPIKE", "1")
    offered = legal_actions(observation())
    assert offered == open_world_actions(observation())
    assert not any(a.id.startswith("goal:") for a in offered)


def test_identical_destinations_are_offered_once(monkeypatch):
    """Three doors to the same map split Jev's probability three ways."""

    from modules import map as map_module

    location = FakeLocation(
        warps=[
            FakeWarp((4, 1), "LITTLEROOT TOWN", destination_id=(0, 9)),
            FakeWarp((4, 2), "LITTLEROOT TOWN", destination_id=(0, 9)),
            FakeWarp((4, 3), "LITTLEROOT TOWN", destination_id=(0, 9)),
        ],
        objects=[],
    )
    monkeypatch.setattr(map_module, "get_map_data_for_current_position", lambda: location, raising=False)
    monkeypatch.setattr(map_module, "get_map_objects", lambda: [], raising=False)
    walks = [a for a in open_world_actions(observation()) if a.id.startswith("walk:")]
    assert len(walks) == 1


def test_repeatedly_interrupted_action_stops_being_offered(map_data):
    """The livelock: a door the game refuses stayed the best-looking option."""

    from jev_plays_emerald.actions import Outcome
    from jev_plays_emerald.state import RecentOutcome

    door = next(a for a in open_world_actions(observation()) if a.id.startswith("walk:"))
    history = tuple(RecentOutcome(door.id, Outcome.INTERRUPTED, "unexpected menu: script") for _ in range(3))
    offered = {a.id for a in open_world_actions(observation(recent_outcomes=history))}
    assert door.id not in offered


def test_interruption_after_a_success_is_still_retryable(map_data):
    """Cutscenes interrupt good actions constantly; those must stay on the menu."""

    from jev_plays_emerald.actions import Outcome
    from jev_plays_emerald.state import RecentOutcome

    door = next(a for a in open_world_actions(observation()) if a.id.startswith("walk:"))
    history = (
        RecentOutcome(door.id, Outcome.INTERRUPTED, "script"),
        RecentOutcome(door.id, Outcome.SUCCESS, None),
        RecentOutcome(door.id, Outcome.INTERRUPTED, "script"),
        RecentOutcome(door.id, Outcome.INTERRUPTED, "script"),
        RecentOutcome(door.id, Outcome.INTERRUPTED, "script"),
    )
    offered = {a.id for a in open_world_actions(observation(recent_outcomes=history))}
    assert door.id in offered


def test_suppression_never_empties_the_menu(map_data):
    """A suppressed action beats having nothing to choose at all."""

    from jev_plays_emerald.actions import Outcome
    from jev_plays_emerald.state import RecentOutcome

    every = open_world_actions(observation())
    history = tuple(
        RecentOutcome(a.id, Outcome.INTERRUPTED, "blocked") for a in every for _ in range(3)
    )
    assert open_world_actions(observation(recent_outcomes=history)) == every


def test_a_single_hard_failure_removes_the_action(map_data):
    """"No empty tile around this object" never becomes true by retrying."""

    from jev_plays_emerald.actions import Outcome
    from jev_plays_emerald.state import RecentOutcome

    history = (RecentOutcome("talk:1", Outcome.FAILED, "Could not find an empty tile"),)
    offered = {a.id for a in open_world_actions(observation(recent_outcomes=history))}
    assert "talk:1" not in offered
    assert "talk:2" in offered


def test_a_failure_before_a_success_is_forgiven(map_data):
    from jev_plays_emerald.actions import Outcome
    from jev_plays_emerald.state import RecentOutcome

    history = (
        RecentOutcome("talk:1", Outcome.FAILED, "blocked"),
        RecentOutcome("talk:1", Outcome.SUCCESS, None),
    )
    offered = {a.id for a in open_world_actions(observation(recent_outcomes=history))}
    assert "talk:1" in offered


def test_objects_are_named_from_the_games_own_script_symbols(map_data):
    """A player sees a clock; the model should read the same thing."""

    labels = {a.id: a.label for a in open_world_actions(observation())}
    assert "Clock" in labels["interact:3:1"]
    assert "(3, 1)" in labels["interact:3:1"]
    assert "Mom" in labels["talk:1"]


def test_unnamed_objects_fall_back_without_inventing_a_name(monkeypatch):
    from modules import map as map_module

    location = FakeLocation(
        warps=[], objects=[], bg_events=[FakeBgEvent((2, 2), script_symbol="0x8160f3a")]
    )
    monkeypatch.setattr(map_module, "get_map_data_for_current_position", lambda: location, raising=False)
    monkeypatch.setattr(map_module, "get_map_objects", lambda: [], raising=False)
    label = open_world_actions(observation())[0].label
    assert "object at (2, 2)" in label


def test_trainer_label_reports_whether_it_was_already_beaten(map_data):
    labels = {a.id: a.label for a in open_world_actions(observation())}
    assert "not yet battled" in labels["talk:2"]


def test_an_oscillating_success_is_suppressed(map_data):
    """The staircase loop: going up and down worked perfectly every time."""

    from jev_plays_emerald.actions import Outcome
    from jev_plays_emerald.state import RecentOutcome

    door = next(a for a in open_world_actions(observation()) if a.id.startswith("walk:"))
    history = tuple(RecentOutcome(door.id, Outcome.SUCCESS, None) for _ in range(4))
    offered = {a.id for a in open_world_actions(observation(recent_outcomes=history))}
    assert door.id not in offered
