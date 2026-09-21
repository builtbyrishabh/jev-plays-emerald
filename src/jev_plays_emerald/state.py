"""Immutable game observations read from PokéBot on the emulator thread."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jev_plays_emerald.actions import Outcome


@dataclass(frozen=True)
class MapPosition:
    map_id: tuple[int, int]
    coordinates: tuple[int, int]
    facing: str


@dataclass(frozen=True)
class MapExit:
    """A doorway or map edge, with the tile to walk to in order to use it.

    A doorway is walked to on the current map; a map connection is walked to on
    the neighbour itself, so the target map is carried rather than assumed.
    `direction` is empty for a doorway and set for a connection, which is also
    how the two are told apart downstream.
    """

    target_map: tuple[int, int]
    target_coordinates: tuple[int, int]
    destination_id: tuple[int, int]
    destination_name: str
    direction: str = ""


@dataclass(frozen=True)
class MapObject:
    """A person or interactive object currently loaded on this map."""

    local_id: int
    coordinates: tuple[int, int]
    script_symbol: str
    trainer_type: str = "None"
    trainer_defeated: bool = False


@dataclass(frozen=True)
class MapSign:
    """A background event: a sign, an examinable object, or a hidden item."""

    coordinates: tuple[int, int]
    kind: str
    script_symbol: str = ""
    hidden_item: str = ""


@dataclass(frozen=True)
class MoveState:
    name: str
    pp: int
    max_pp: int
    type: str = "Unknown"
    power: int = 0
    accuracy: float = 0.0
    description: str = ""
    usable: bool = True


@dataclass(frozen=True)
class PartyMember:
    species: str
    level: int
    hp: int
    max_hp: int
    status: str
    moves: tuple[MoveState, ...]


@dataclass(frozen=True)
class ActiveBattler:
    party_index: int
    moves: tuple[MoveState, ...]


@dataclass(frozen=True)
class OpponentBattler:
    species: str
    level: int
    hp: int
    max_hp: int
    status: str


@dataclass(frozen=True)
class InventoryItem:
    name: str
    quantity: int
    # PokéBot's ItemBattleUse value ("healing", "pp_recovery", "stat_increase",
    # "catch", "escape", "not_usable"). Carried as a fact so the enumerator can
    # offer battle-item and catch options without reaching back into the API.
    battle_use: str = "not_usable"


@dataclass(frozen=True)
class OpeningFlags:
    rescued_birch: bool
    received_pokedex: bool
    defeated_rival_route103: bool
    set_wall_clock: bool = False
    rival_left_for_route103: bool = False


@dataclass(frozen=True)
class RecentOutcome:
    action_id: str
    outcome: "Outcome"
    reason: str | None = None


@dataclass(frozen=True)
class Observation:
    context_id: str
    game_state: str
    position: MapPosition | None
    controllable: bool
    menu_phase: str
    battle_phase: str
    party: tuple[PartyMember, ...]
    inventory: tuple[InventoryItem, ...]
    opening_flags: OpeningFlags
    active_battler: ActiveBattler | None
    recent_outcomes: tuple[RecentOutcome, ...]
    opponent: OpponentBattler | None = None
    trainer_id: int | None = None
    can_run: bool = False
    tasks: tuple[str, ...] = ()
    scripts: tuple[str, ...] = ()
    rival_house_state: int = 0
    lab_state: int = 0
    player_gender: str = "male"
    # Landmarks of the current map, read here so the option enumerator is pure
    # logic over these fields instead of live PokeBot map calls.
    exits: tuple[MapExit, ...] = ()
    objects: tuple[MapObject, ...] = ()
    signs: tuple[MapSign, ...] = ()


def observation_context_id(
    game_state: str,
    map_id: tuple[int, int] | None,
    menu_phase: str,
    battle_phase: str,
    party: tuple[PartyMember, ...],
    inventory: tuple[InventoryItem, ...],
    opening_flags: OpeningFlags,
    active_battler: ActiveBattler | None = None,
    opponent: OpponentBattler | None = None,
) -> str:
    """Identify a decision situation without changing for movement animation frames."""

    payload = {
        "game_state": game_state,
        "map_id": map_id,
        "menu_phase": menu_phase,
        "battle_phase": battle_phase,
        "party": party,
        "inventory": inventory,
        "opening_flags": opening_flags,
        "active_battler": active_battler,
        "opponent": opponent,
    }
    encoded = json.dumps(payload, default=lambda value: value.__dict__, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


class ObservationReader:
    """Capture all RAM-backed fields together on the thread that owns mGBA."""

    def __init__(self, *, landmarks: bool = False) -> None:
        self._owner_thread = threading.get_ident()
        # Reading the map costs a table lookup per warp and per connection, every
        # frame. Only the open-world enumerator consumes them, so the caller that
        # knows whether it is running asks for them.
        self.landmarks = landmarks

    def read(self, recent_outcomes: tuple[RecentOutcome, ...] = ()) -> Observation:
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("observations must be built on the emulator owner thread")

        from modules.items import get_item_bag
        from modules.memory import GameState, get_event_flag, get_event_var, get_game_state, read_symbol, unpack_uint16
        from modules.player import get_player, get_player_avatar, player_avatar_is_controllable
        from modules.tasks import get_tasks, get_global_script_context
        from modules.pokemon_party import get_party

        state = get_game_state()
        if state is None:
            # The real launcher can enter the mode before Emerald's first frame.
            # Save blocks and task memory are not safe to decode at that point.
            return Observation(
                context_id="boot:unknown",
                game_state="UNKNOWN",
                position=None,
                controllable=False,
                menu_phase="none",
                battle_phase="none",
                party=(),
                inventory=(),
                opening_flags=OpeningFlags(False, False, False),
                active_battler=None,
                recent_outcomes=tuple(recent_outcomes),
            )
        game_state = state.name
        position = None
        if state in {GameState.OVERWORLD, GameState.CHANGE_MAP, GameState.BATTLE_STARTING, GameState.BATTLE}:
            try:
                avatar = get_player_avatar()
                position = MapPosition(
                    tuple(avatar.map_group_and_number),
                    tuple(avatar.local_coordinates),
                    avatar.facing_direction,
                )
            except (RuntimeError, ValueError):
                position = None

        menu_phase, battle_phase = _read_phases(state)
        party = tuple(
            PartyMember(
                pokemon.species.name,
                pokemon.level,
                pokemon.current_hp,
                pokemon.total_hp,
                pokemon.status_condition.name,
                tuple(
                    _move_state(move)
                    for move in pokemon.moves
                    if move is not None
                ),
            )
            for pokemon in get_party()
        )
        bag = get_item_bag()
        inventory = tuple(
            InventoryItem(slot.item.name, slot.quantity, slot.item.battle_use.value)
            for pocket in (bag.items, bag.key_items, bag.poke_balls, bag.tms_hms, bag.berries)
            for slot in pocket
        )
        opening_flags = OpeningFlags(
            rescued_birch=get_event_flag("RESCUED_BIRCH"),
            received_pokedex=get_event_flag("SYS_POKEDEX_GET"),
            defeated_rival_route103=get_event_flag("DEFEATED_RIVAL_ROUTE103"),
            set_wall_clock=get_event_flag("SET_WALL_CLOCK"),
            rival_left_for_route103=get_event_flag("RIVAL_LEFT_FOR_ROUTE103"),
        )
        active_battler = None
        opponent = None
        trainer_id = None
        can_run = False
        if state is GameState.BATTLE:
            from modules.battle_state import get_battle_state

            battle_state = get_battle_state()
            if battle_state.is_trainer_battle:
                trainer_id = unpack_uint16(read_symbol("gTrainerBattleOpponent_A", size=2))
            if battle_phase == "action" and battle_state.own_side.active_battler is not None:
                from modules.battle_strategies._util import BattleStrategyUtil
                can_run = BattleStrategyUtil(battle_state).get_escape_chance() > 0
            battler = battle_state.own_side.active_battler
            if battler is not None:
                active_battler = ActiveBattler(
                    battler.party_index,
                    tuple(
                        _move_state(move, usable=battler.can_use_move(move.move))
                        for move in battler.moves
                        if move is not None
                    ),
                )
            opponent_battler = battle_state.opponent.active_battler
            if opponent_battler is not None:
                opponent = OpponentBattler(
                    opponent_battler.species.name,
                    opponent_battler.level,
                    opponent_battler.current_hp,
                    opponent_battler.total_hp,
                    opponent_battler.status_permanent.name,
                )
        context_id = observation_context_id(
            game_state,
            position.map_id if position is not None else None,
            menu_phase,
            battle_phase,
            party,
            inventory,
            opening_flags,
            active_battler,
            opponent,
        )
        controllable = player_avatar_is_controllable() if state is GameState.OVERWORLD else False
        return Observation(
            context_id,
            game_state,
            position,
            controllable,
            menu_phase,
            battle_phase,
            party,
            inventory,
            opening_flags,
            active_battler,
            tuple(recent_outcomes),
            opponent,
            trainer_id=trainer_id,
            can_run=can_run,
            tasks=tuple(task.symbol for task in get_tasks()),
            scripts=tuple(get_global_script_context().stack) if get_global_script_context().is_active else (),
            rival_house_state=get_event_var("LITTLEROOT_RIVAL_STATE"),
            lab_state=get_event_var("BIRCH_LAB_STATE"),
            player_gender=get_player().gender,
            **_read_landmarks(position if controllable and self.landmarks else None),
        )


def _read_landmarks(position: MapPosition | None) -> dict[str, tuple]:
    """Read the current map's static landmarks on the emulator owner thread.

    Everything the option enumerator needs about where it can go and who it can
    talk to becomes plain data here, so the enumerator itself never calls
    PokeBot. Any map lookup that fails is skipped rather than guessed at: an
    absent landmark costs one option, an invented one costs a stuck run.

    Callers pass `None` when nothing will read the result: outside walkable
    overworld control, where there is no map to leave and nobody to walk up to,
    or when the scripted route is driving and the enumerator is not running.
    """

    if position is None:
        return {}

    from modules.map import get_map_data_for_current_position, get_map_objects

    location = get_map_data_for_current_position()
    if location is None:
        return {}

    here = tuple(location.map_group_and_number)
    exits: list[MapExit] = []
    for warp in location.warps:
        try:
            destination = warp.destination_location
            key = tuple(destination.map_group_and_number)
            name = _map_name(key, destination.map_name)
        except (RuntimeError, ValueError, IndexError):
            continue
        exits.append(MapExit(here, tuple(warp.local_coordinates), key, name))
    for connection in location.connections:
        try:
            neighbour = connection.destination_map
            width, height = neighbour.map_size
            key = tuple(neighbour.map_group_and_number)
            name = _map_name(key, neighbour.map_name)
        except (RuntimeError, ValueError, IndexError):
            continue
        exits.append(MapExit(key, (width // 2, height // 2), key, name, connection.direction))

    loaded = {npc.local_id for npc in get_map_objects()}
    objects: list[MapObject] = []
    for template in location.objects:
        # A clone template is a second copy of an object that is already
        # offered, and an unloaded slot is not on screen to walk up to.
        if template.kind != "normal" or template.local_id not in loaded:
            continue
        defeated = False
        if template.trainer_type != "None":
            try:
                defeated = template.is_trainer_defeated
            except (RuntimeError, ValueError):
                continue
        objects.append(
            MapObject(
                template.local_id,
                tuple(template.local_coordinates),
                template.script_symbol,
                template.trainer_type,
                defeated,
            )
        )

    signs: list[MapSign] = []
    for event in location.bg_events:
        hidden_item = ""
        symbol = ""
        if event.kind == "Hidden Item":
            try:
                hidden_item = event.hidden_item.name
            except (RuntimeError, ValueError):
                continue
        else:
            symbol = event.script_symbol
        signs.append(MapSign(tuple(event.local_coordinates), event.kind, symbol, hidden_item))

    return {"exits": tuple(exits), "objects": tuple(objects), "signs": tuple(signs)}


def _map_name(map_id: tuple[int, int], fallback: str) -> str:
    """Name a map by its identity, not by its on-screen town name.

    Emerald gives a house interior the same display name as the town around it,
    so `map_name` alone told Jev that the staircase and the front door both led
    to Littleroot Town.
    """

    from modules.map_data import MapRSE

    try:
        return MapRSE(map_id).name.replace("_", " ").title()
    except ValueError:
        return fallback


def _move_state(move: object, *, usable: bool = True) -> MoveState:
    move_data = move.move
    return MoveState(
        move_data.name,
        move.pp,
        move.total_pp,
        move_data.type.name,
        move_data.base_power,
        move_data.accuracy,
        move_data.description,
        usable,
    )


def _read_phases(game_state: object) -> tuple[str, str]:
    from modules.memory import GameState

    if game_state is GameState.BATTLE:
        from modules.menu_parsers import get_battle_menu

        battle_phase = get_battle_menu().casefold()
        return "battle", battle_phase
    if game_state is GameState.CHOOSE_STARTER:
        return "starter", "none"
    if game_state is GameState.OVERWORLD:
        from modules.menu_parsers import parse_start_menu
        from modules.tasks import get_global_script_context

        if parse_start_menu()["open"]:
            return "start", "none"
        if get_global_script_context().is_active:
            return "script", "none"
        return "none", "none"
    if game_state in {GameState.BAG_MENU, GameState.PARTY_MENU, GameState.POKEMON_SUMMARY_SCREEN}:
        return game_state.name.casefold(), "none"
    return "none", "none"
