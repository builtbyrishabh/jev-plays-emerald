"""Spike guards: the wide menu must stay stable and stay executable."""

from dataclasses import dataclass, replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / ".cache/pokebot-gen3"))
import pytest

from jev_plays_emerald.actions import ACTION_EXECUTORS
from jev_plays_emerald.opening import OLDALE_HEAL_MAPS, legal_actions, open_world_actions
from jev_plays_emerald.state import (
    MapExit,
    MapObject,
    MapPosition,
    MapSign,
    MoveState,
    Observation,
    OpeningFlags,
    PartyMember,
    _read_landmarks,
)

HERE = (0, 10)
OLDALE = (0, 10)
ROUTE103 = (0, 18)


def observation(**changes):
    """An ordinary overworld situation: two ways out, two people, one clock."""

    member = PartyMember("Treecko", 5, 20, 20, "Healthy", (MoveState("Pound", 35, 35),))
    base = Observation(
        "ctx", "OVERWORLD", MapPosition(HERE, (10, 4), "Up"), True, "none", "none",
        (member,), (), OpeningFlags(True, False, False), None, (),
        exits=(
            MapExit(HERE, (5, 3), OLDALE, "Oldale Town"),
            MapExit(HERE, (12, 8), ROUTE103, "Route103"),
        ),
        objects=(
            MapObject(1, (7, 7), "LittlerootTown_EventScript_Mom"),
            MapObject(2, (9, 4), "Route103_EventScript_Calvin", trainer_type="Normal"),
        ),
        signs=(MapSign((3, 1), "Script", "LittlerootTown_BrendansHouse_2F_EventScript_Clock"),),
    )
    return replace(base, **changes)


def test_offered_set_is_identical_across_frames():
    """A churning menu would discard every in-flight decision and stall the run."""

    first = open_world_actions(observation())
    second = open_world_actions(observation())
    assert first == second
    assert len(first) > 2


def test_every_offered_action_has_an_executor():
    for action in open_world_actions(observation()):
        kind = action.id.partition(":")[0]
        assert kind in ACTION_EXECUTORS, f"{action.id} has no executor"


def test_menu_contains_no_story_destination():
    """The point of the spike: primitives only, no "go fight the rival"."""

    offered = {action.id for action in open_world_actions(observation())}
    assert not any(action.startswith(("goal:", "heal:")) for action in offered)
    assert "interact:3:1" in offered  # bg events keep the clock reachable


def test_flag_off_keeps_the_scripted_single_goal(monkeypatch):
    monkeypatch.delenv("JEV_OPEN_WORLD_SPIKE", raising=False)
    offered = legal_actions(observation())
    assert [action.id for action in offered] == ["goal:rival"]


def test_flag_on_replaces_every_overworld_goal(monkeypatch):
    monkeypatch.setenv("JEV_OPEN_WORLD_SPIKE", "1")
    offered = legal_actions(observation())
    assert offered == open_world_actions(observation())
    assert not any(a.id.startswith("goal:") for a in offered)


def test_a_doorway_is_walked_to_on_the_map_you_are_standing_on():
    """A doorway tile is local; a map connection is a tile on the neighbour."""

    door = MapExit(HERE, (5, 3), OLDALE, "Oldale Town")
    edge = MapExit(ROUTE103, (10, 20), ROUTE103, "Route103", "north")
    offered = {a.id: a.label for a in open_world_actions(observation(exits=(door, edge)))}
    assert "Go through the doorway at (5, 3) into Oldale Town" == offered["walk:0:10:5:3"]
    assert "Travel north out of here into Route103" == offered["walk:0:18:10:20"]


def test_identical_destinations_are_offered_once():
    """Three doors to the same map split Jev's probability three ways."""

    doors = tuple(MapExit(HERE, (4, y), (0, 9), "Littleroot Town") for y in (1, 2, 3))
    walks = [a for a in open_world_actions(observation(exits=doors)) if a.id.startswith("walk:")]
    assert len(walks) == 1


def test_repeatedly_interrupted_action_stops_being_offered():
    """The livelock: a door the game refuses stayed the best-looking option."""

    from jev_plays_emerald.actions import Outcome
    from jev_plays_emerald.state import RecentOutcome

    door = next(a for a in open_world_actions(observation()) if a.id.startswith("walk:"))
    history = tuple(RecentOutcome(door.id, Outcome.INTERRUPTED, "unexpected menu: script") for _ in range(3))
    offered = {a.id for a in open_world_actions(observation(recent_outcomes=history))}
    assert door.id not in offered


def test_interruption_after_a_success_is_still_retryable():
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


def test_suppression_never_empties_the_menu():
    """A suppressed action beats having nothing to choose at all."""

    from jev_plays_emerald.actions import Outcome
    from jev_plays_emerald.state import RecentOutcome

    every = open_world_actions(observation())
    history = tuple(
        RecentOutcome(a.id, Outcome.INTERRUPTED, "blocked") for a in every for _ in range(3)
    )
    assert open_world_actions(observation(recent_outcomes=history)) == every


def test_a_single_hard_failure_removes_the_action():
    """"No empty tile around this object" never becomes true by retrying."""

    from jev_plays_emerald.actions import Outcome
    from jev_plays_emerald.state import RecentOutcome

    history = (RecentOutcome("talk:1", Outcome.FAILED, "Could not find an empty tile"),)
    offered = {a.id for a in open_world_actions(observation(recent_outcomes=history))}
    assert "talk:1" not in offered
    assert "talk:2" in offered


def test_a_failure_before_a_success_is_forgiven():
    from jev_plays_emerald.actions import Outcome
    from jev_plays_emerald.state import RecentOutcome

    history = (
        RecentOutcome("talk:1", Outcome.FAILED, "blocked"),
        RecentOutcome("talk:1", Outcome.SUCCESS, None),
    )
    offered = {a.id for a in open_world_actions(observation(recent_outcomes=history))}
    assert "talk:1" in offered


def test_objects_are_named_from_the_games_own_script_symbols():
    """A player sees a clock; the model should read the same thing."""

    labels = {a.id: a.label for a in open_world_actions(observation())}
    assert "Clock" in labels["interact:3:1"]
    assert "(3, 1)" in labels["interact:3:1"]
    assert "Mom" in labels["talk:1"]


def test_unnamed_objects_fall_back_without_inventing_a_name():
    unnamed = (MapSign((2, 2), "Script", "0x8160f3a"),)
    label = open_world_actions(observation(exits=(), objects=(), signs=unnamed))[0].label
    assert "object at (2, 2)" in label


def test_a_hidden_item_reads_as_what_is_buried_there():
    buried = (MapSign((6, 6), "Hidden Item", hidden_item="Potion"),)
    label = open_world_actions(observation(exits=(), objects=(), signs=buried))[0].label
    assert "a Potion is hidden" in label


def test_trainer_label_reports_whether_it_was_already_beaten():
    labels = {a.id: a.label for a in open_world_actions(observation())}
    assert "not yet battled" in labels["talk:2"]


def test_an_oscillating_success_is_suppressed():
    """The staircase loop: going up and down worked perfectly every time."""

    from jev_plays_emerald.actions import Outcome
    from jev_plays_emerald.state import RecentOutcome

    door = next(a for a in open_world_actions(observation()) if a.id.startswith("walk:"))
    history = tuple(RecentOutcome(door.id, Outcome.SUCCESS, None) for _ in range(4))
    offered = {a.id for a in open_world_actions(observation(recent_outcomes=history))}
    assert door.id not in offered


def test_heal_map_ids_match_pokebot():
    """The enumerator carries these as data; PokéBot's table stays the source."""

    from modules.map_data import MapRSE

    assert OLDALE_HEAL_MAPS == {MapRSE.OLDALE_TOWN.value, MapRSE.ROUTE103.value}


# --- the reader that feeds the enumerator ------------------------------------


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
class FakeConnection:
    direction: str
    destination_id: tuple[int, int]
    size: tuple[int, int] = (20, 30)

    @property
    def destination_map(self):
        return type(
            "Neighbour",
            (),
            {
                "map_name": "somewhere",
                "map_group_and_number": self.destination_id,
                "map_size": self.size,
            },
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
    map_group_and_number = HERE
    map_name = "Oldale Town"

    def __init__(self, warps=(), objects=(), bg_events=(), connections=()):
        self.warps = list(warps)
        self.objects = list(objects)
        self.bg_events = list(bg_events)
        self.connections = list(connections)


def install(monkeypatch, location, loaded_ids=()):
    from modules import map as map_module

    loaded = [type("Obj", (), {"local_id": i})() for i in loaded_ids]
    monkeypatch.setattr(map_module, "get_map_data_for_current_position", lambda: location, raising=False)
    monkeypatch.setattr(map_module, "get_map_objects", lambda: loaded, raising=False)
    return _read_landmarks(MapPosition(HERE, (10, 4), "Up"))


def test_reader_skips_clone_and_unloaded_objects(monkeypatch):
    location = FakeLocation(
        objects=[
            FakeTemplate(local_id=1, local_coordinates=(7, 7)),
            FakeTemplate(local_id=2, local_coordinates=(9, 4)),
            FakeTemplate(local_id=3, local_coordinates=(2, 2), kind="clone"),
        ]
    )
    landmarks = install(monkeypatch, location, loaded_ids=(1, 3))
    assert [npc.local_id for npc in landmarks["objects"]] == [1]


def test_reader_names_a_destination_by_map_identity(monkeypatch):
    """Emerald calls a house interior by the name of the town around it."""

    location = FakeLocation(warps=[FakeWarp((5, 3), "OLDALE TOWN", destination_id=ROUTE103)])
    landmarks = install(monkeypatch, location)
    assert landmarks["exits"][0].destination_name == "Route103"


def test_reader_targets_the_middle_of_a_connected_map(monkeypatch):
    """A map edge is crossed by walking to a tile on the neighbour, not here."""

    location = FakeLocation(connections=[FakeConnection("north", ROUTE103, size=(20, 30))])
    exit_ = install(monkeypatch, location)["exits"][0]
    assert exit_.target_map == ROUTE103
    assert exit_.target_coordinates == (10, 15)
    assert exit_.direction == "north"


def test_reader_carries_a_hidden_item_by_name(monkeypatch):
    event = FakeBgEvent((6, 6), kind="Hidden Item")
    event.hidden_item = type("Item", (), {"name": "Potion"})()
    landmarks = install(monkeypatch, FakeLocation(bg_events=[event]))
    assert landmarks["signs"][0].hidden_item == "Potion"


def test_reader_reads_nothing_without_a_walkable_position():
    """Outside overworld control there is no map to leave and nobody to meet."""

    assert _read_landmarks(None) == {}


def test_the_scripted_route_pays_nothing_for_landmarks(monkeypatch):
    """Reading the map costs a lookup per warp on every one of 60 frames a second.

    Only the open-world enumerator reads the result, so the scripted route must
    not be paying for it.
    """

    from jev_plays_emerald.mode import JevEmeraldMode

    monkeypatch.delenv("JEV_OPEN_WORLD_SPIKE", raising=False)
    assert JevEmeraldMode()._observation_reader.landmarks is False

    monkeypatch.setenv("JEV_OPEN_WORLD_SPIKE", "1")
    assert JevEmeraldMode()._observation_reader.landmarks is True
