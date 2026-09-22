"""The open-world menu must stay stable and stay executable."""

from dataclasses import dataclass, replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / ".cache/pokebot-gen3"))
import pytest

from jev_plays_emerald.actions import ACTION_EXECUTORS
from jev_plays_emerald.opening import (
    MAX_OPEN_WORLD_ACTIONS,
    legal_actions,
    open_world_actions,
)
from jev_plays_emerald.state import (
    DYNAMIC_WARP,
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
    """Primitives only: no "go fight the rival" left anywhere in the menu."""

    offered = {action.id for action in open_world_actions(observation())}
    assert not any(action.startswith(("goal:", "heal:")) for action in offered)
    assert "interact:3:1" in offered  # bg events keep the clock reachable


def test_every_overworld_choice_comes_from_the_enumerator():
    """Nothing between legal_actions and the menu decides where to go."""

    assert legal_actions(observation()) == open_world_actions(observation())


def test_a_doorway_is_walked_to_on_the_map_you_are_standing_on():
    """A doorway tile is local; a map connection is a tile on the neighbour."""

    door = MapExit(HERE, (5, 3), OLDALE, "Oldale Town")
    edge = MapExit(ROUTE103, (10, 20), ROUTE103, "Route103", "north")
    offered = {a.id: a.label for a in open_world_actions(observation(exits=(door, edge)))}
    assert "Go through the doorway at (5, 3) into Oldale Town" == offered["walk:0:10:5:3"]
    assert "Travel north out of here into Route103" == offered["walk:0:18:10:20"]


def test_an_exit_with_no_known_destination_claims_none():
    """The truck's doors are dynamic warps; a label must not invent a town."""

    unknown = MapExit(HERE, (4, 1), DYNAMIC_WARP, "")
    offered = {a.id: a.label for a in open_world_actions(observation(exits=(unknown,)))}
    assert offered["walk:0:10:4:1"] == "Go through the doorway at (4, 1) to leave this map"


def test_a_crowded_map_keeps_its_exits_and_its_nearest_landmarks():
    """Ordering by ID alone cut `walk:` first and left Jev nowhere to go."""

    signs = tuple(MapSign((x, 1), "Script", "Town_EventScript_Sign") for x in range(40))
    offered = [a.id for a in open_world_actions(observation(signs=signs))]
    assert len(offered) == MAX_OPEN_WORLD_ACTIONS
    assert {"walk:0:10:5:3", "walk:0:10:12:8"} <= set(offered)
    # The player stands at (10, 4), so (10, 1) survives and (39, 1) does not.
    assert "interact:10:1" in offered
    assert "interact:39:1" not in offered


def test_a_distant_person_is_reached_by_walking_to_their_tile():
    """The rival is eighteen tiles away and the game is not tracking him."""

    rival = MapObject(2, (10, 3), "Route103_EventScript_Rival", loaded=False)
    offered = {a.id: a.label for a in open_world_actions(observation(objects=(rival,)))}
    assert offered["interact:10:3"] == "Walk all the way over to Rival at (10, 3)"
    assert "talk:2" not in offered


def test_gym_room_doors_do_not_crowd_norman_out_of_the_menu():
    gym = (8, 1)
    doors = tuple(MapExit(gym, (1, y), gym, "Petalburg City Gym") for y in range(1, 106, 3))
    state = observation(position=MapPosition(gym, (4, 111), "Up"),
                        exits=doors + (MapExit(gym, (4, 111), (0, 0), "Petalburg City"),),
                        objects=(MapObject(1, (4, 108), "PetalburgCity_Gym_EventScript_Norman"),), signs=())
    offered = {a.id for a in open_world_actions(state)}
    assert "talk:1" in offered
    assert "walk:8:1:4:111" in offered
    assert "walk:8:1:1:1" not in offered


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
    from jev_plays_emerald.gameplay import CENTER_MAPS

    assert CENTER_MAPS["oldale"] == {MapRSE.OLDALE_TOWN.value, MapRSE.ROUTE103.value, MapRSE.OLDALE_TOWN_POKEMON_CENTER_1F.value}
    assert CENTER_MAPS["petalburg"] == {MapRSE.PETALBURG_CITY.value, MapRSE.PETALBURG_CITY_POKEMON_CENTER_1F.value}
    assert CENTER_MAPS["rustboro"] == {MapRSE.RUSTBORO_CITY.value, MapRSE.RUSTBORO_CITY_POKEMON_CENTER_1F.value}


# --- the reader that feeds the enumerator ------------------------------------


@dataclass
class FakeWarp:
    local_coordinates: tuple[int, int]
    destination_name: str
    destination_id: tuple[int, int] = (0, 9)

    @property
    def destination_map_group(self):
        return self.destination_id[0]

    @property
    def destination_map_number(self):
        return self.destination_id[1]

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
    offset: int = 0

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
    flag_id: int = 0


@dataclass
class FakeBgEvent:
    local_coordinates: tuple[int, int]
    kind: str = "Script"
    script_symbol: str = "LittlerootTown_BrendansHouse_2F_EventScript_Clock"


class FakeLocation:
    map_group_and_number = HERE
    map_name = "Oldale Town"
    map_size = (20, 20)

    def __init__(self, warps=(), objects=(), bg_events=(), connections=()):
        self.warps = list(warps)
        self.objects = list(objects)
        self.bg_events = list(bg_events)
        self.connections = list(connections)


def install(monkeypatch, location, loaded_ids=(), hidden_flags=()):
    from modules import map as map_module
    from modules import memory as memory_module

    from jev_plays_emerald import training
    monkeypatch.setattr(training, "training_spots", lambda _: ())
    loaded = [type("Obj", (), {"local_id": i, "current_coords": next(obj.local_coordinates for obj in location.objects if obj.local_id == i)})() for i in loaded_ids]
    monkeypatch.setattr(map_module, "get_map_data_for_current_position", lambda: location, raising=False)
    monkeypatch.setattr(map_module, "get_map_objects", lambda: loaded, raising=False)
    monkeypatch.setattr(
        memory_module, "get_event_flag_by_number", lambda flag: flag in hidden_flags, raising=False
    )
    return _read_landmarks(MapPosition(HERE, (10, 4), "Up"))


def test_reader_skips_clones_and_marks_distant_objects_unloaded(monkeypatch):
    """A far-off object still exists; only its live position is unknown."""

    location = FakeLocation(
        objects=[
            FakeTemplate(local_id=1, local_coordinates=(7, 7)),
            FakeTemplate(local_id=2, local_coordinates=(9, 4)),
            FakeTemplate(local_id=3, local_coordinates=(2, 2), kind="clone"),
        ]
    )
    landmarks = install(monkeypatch, location, loaded_ids=(1, 3))
    assert [(npc.local_id, npc.loaded) for npc in landmarks["objects"]] == [(1, True), (2, False)]


def test_reader_skips_an_object_the_story_has_hidden(monkeypatch):
    """The Route 103 rival is in the map data long before he is there."""

    location = FakeLocation(objects=[FakeTemplate(local_id=2, local_coordinates=(10, 3), flag_id=723)])
    assert install(monkeypatch, location, hidden_flags=(723,))["objects"] == ()
    assert len(install(monkeypatch, location)["objects"]) == 1


def test_reader_names_a_destination_by_map_identity(monkeypatch):
    """Emerald calls a house interior by the name of the town around it."""

    location = FakeLocation(warps=[FakeWarp((5, 3), "OLDALE TOWN", destination_id=ROUTE103)])
    landmarks = install(monkeypatch, location)
    assert landmarks["exits"][0].destination_name == "Route103"


def test_reader_refuses_to_name_a_dynamic_warp(monkeypatch):
    """Upstream answers from the save block, which is not where the door goes."""

    location = FakeLocation(warps=[FakeWarp((4, 1), "PETALBURG CITY", destination_id=DYNAMIC_WARP)])
    exit_ = install(monkeypatch, location)["exits"][0]
    assert exit_.destination_name == ""
    assert exit_.target_coordinates == (4, 1)


def test_reader_targets_the_tile_just_across_a_map_edge(monkeypatch):
    """Oldale's middle-of-Route-103 target was across water and unpathable."""

    from modules import map_path
    from jev_plays_emerald.state import _reachable_border_tile
    _reachable_border_tile.cache_clear()
    monkeypatch.setattr(map_path, "calculate_path", lambda *args, **kwargs: [])
    location = FakeLocation(connections=[FakeConnection("North", ROUTE103, size=(20, 30))])
    exit_ = install(monkeypatch, location)["exits"][0]
    assert exit_.target_map == ROUTE103
    assert exit_.target_coordinates == (10, 29)
    assert exit_.direction == "North"


def test_reader_skips_a_connection_you_cannot_walk_over(monkeypatch):
    location = FakeLocation(connections=[FakeConnection("Dive", ROUTE103)])
    assert install(monkeypatch, location)["exits"] == ()


def test_reader_carries_a_hidden_item_by_name(monkeypatch):
    event = FakeBgEvent((6, 6), kind="Hidden Item")
    event.hidden_item = type("Item", (), {"name": "Potion"})()
    landmarks = install(monkeypatch, FakeLocation(bg_events=[event]))
    assert landmarks["signs"][0].hidden_item == "Potion"


def test_reader_reads_nothing_without_a_walkable_position():
    """Outside overworld control there is no map to leave and nobody to meet."""

    assert _read_landmarks(None) == {}


def test_the_mode_reads_the_landmarks_the_menu_is_built_from():
    """Reading the map costs a lookup per warp on every frame, and buys the menu."""

    from jev_plays_emerald.mode import JevEmeraldMode

    assert JevEmeraldMode()._observation_reader.landmarks is True


def test_prompt_does_not_add_authored_route_hints():

    from modules.map_data import MapRSE

    from jev_plays_emerald.opening import decision_instructions

    truck = observation(
        position=MapPosition(MapRSE.INSIDE_OF_TRUCK.value, (1, 1), "Up"),
        party=(),
        opening_flags=OpeningFlags(False, False, False),
    )
    instructions = decision_instructions(truck)
    assert "moving truck" not in instructions
    assert "bedroom" not in instructions

    house = replace(truck, position=MapPosition((1, 0), (8, 7), "Up"))
    assert "wall clock" not in decision_instructions(house)


def test_rival_mission_states_the_goal_without_an_authored_route():
    from jev_plays_emerald.opening import MISSION

    assert MISSION == "Goal: defeat your rival on Route 103."
    assert "Birch" not in MISSION
    assert "travel north" not in MISSION


def test_first_gym_prompt_focuses_jev_on_the_immediate_objective(monkeypatch):
    from jev_plays_emerald.opening import decision_instructions

    monkeypatch.setenv("JEV_TARGET", "first-gym")
    state = observation(
        recent_dialogue=("Go introduce yourself to our new neighbour.",),
        opening_flags=OpeningFlags(False, False, False, set_wall_clock=True),
    )

    instructions = decision_instructions(state)

    assert instructions.startswith("Goal: earn the Stone Badge.")
    assert "Build the immediate objective from the latest relevant dialogue" in instructions
    assert "immediate objective" in instructions
    assert "Keep that objective across map changes" in instructions
    assert "Check whether the expected progress occurred" in instructions
    assert "interact with its relevant person or object before leaving" in instructions
    assert "Choose your starter, rescue Birch" not in instructions
    assert "neighbor's house" not in instructions
    assert "May's House" not in instructions
    assert "Route 101" not in instructions


def test_luna_advice_becomes_jev_current_objective(monkeypatch):
    from jev_plays_emerald.opening import decision_instructions

    monkeypatch.setenv("JEV_TARGET", "first-gym")
    instructions = decision_instructions(
        observation(recent_dialogue=("Old dialogue that may no longer apply.",)),
        advice="Go upstairs and inspect the Poke Ball.",
    )

    assert "Luna recovery hint: Go upstairs and inspect the Poke Ball." in instructions
    assert "Current objective from Luna" not in instructions


def test_reader_uses_loaded_npc_position_after_script_relocation(monkeypatch):
    from modules import map as map_module
    from jev_plays_emerald import training
    location = FakeLocation(objects=[FakeTemplate(local_id=1, local_coordinates=(4, 2))])
    monkeypatch.setattr(map_module, 'get_map_data_for_current_position', lambda: location)
    monkeypatch.setattr(map_module, 'get_map_objects', lambda: [type('Obj', (), {'local_id': 1, 'current_coords': (4, 107)})()])
    monkeypatch.setattr(training, 'training_spots', lambda _: ())
    objects = _read_landmarks(MapPosition(HERE, (1, 106), 'Up'))['objects']
    assert objects[0].coordinates == (4, 107)
