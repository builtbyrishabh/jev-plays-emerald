"""Immutable game observations read from PokéBot on the emulator thread."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import deque
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jev_plays_emerald.actions import Outcome


class DialogueMemory:
    """Keep a small transcript of dialogue Jev has actually seen."""

    def __init__(self, *, limit: int = 8) -> None:
        self._messages: deque[str] = deque(maxlen=limit)

    @property
    def messages(self) -> tuple[str, ...]:
        return tuple(self._messages)

    def remember(self, text: str | None) -> None:
        normalized = " ".join((text or "").split())
        if normalized and (not self._messages or normalized != self._messages[-1]):
            self._messages.append(normalized)


@dataclass(frozen=True)
class MapPosition:
    map_id: tuple[int, int]
    coordinates: tuple[int, int]
    facing: str
    # Where the player would say they are. Without it the only clue is a pair
    # of numbers, and Jev walked off Route 103 looking for Route 103.
    map_name: str = ""


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
    """A person or interactive object that is present on this map.

    `loaded` is false for one that exists but is too far away for the game to
    be tracking it. Those are still worth offering - the Route 103 rival is
    eighteen tiles from where you arrive - but only their spawn tile is known,
    not where they have since walked to.
    """

    local_id: int
    coordinates: tuple[int, int]
    script_symbol: str
    trainer_type: str = "None"
    trainer_defeated: bool = False
    loaded: bool = True


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
    is_egg: bool = False


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
class ShopItem:
    name: str
    price: int


@dataclass(frozen=True)
class OpeningFlags:
    rescued_birch: bool
    received_pokedex: bool
    defeated_rival_route103: bool
    set_wall_clock: bool = False
    rival_left_for_route103: bool = False
    stone_badge: bool = False
    petalburg_tutorial: bool = False
    devon_goods_saved: bool = False


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
    recent_dialogue: tuple[str, ...] = ()
    money: int = 0
    shop_items: tuple[ShopItem, ...] = ()
    learning_move: MoveState | None = None
    learning_party_index: int | None = None
    tutorial_battle: bool = False
    training_spots: tuple[tuple[int, int], ...] = ()


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
    recent_dialogue: tuple[str, ...] = (),
    menu_details: tuple = (),
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
        "recent_dialogue": recent_dialogue,
        "menu_details": menu_details,
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
        self._dialogue = DialogueMemory()
        self._last_observation: Observation | None = None

    def read(self, recent_outcomes: tuple[RecentOutcome, ...] = ()) -> Observation:
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("observations must be built on the emulator owner thread")

        from modules.items import get_item_bag
        from modules.memory import GameState, get_event_flag, get_event_var, get_game_state, read_symbol, unpack_uint16
        from modules.player import get_player, get_player_avatar, player_avatar_is_controllable
        from modules.tasks import get_tasks, get_global_script_context
        from modules.pokemon_party import get_party

        state = get_game_state()
        # Emerald temporarily clears its relocatable save pointers during some
        # transitions. Upstream returns zero bytes then; those are not evidence
        # that badges/story milestones (or the player's inventory) were lost.
        # CHANGE_MAP/UNKNOWN can also expose partially rebuilt save data with
        # non-null pointers. Defer new facts until a coherent game state returns.
        pointers_ready = state not in {None, GameState.CHANGE_MAP, GameState.UNKNOWN} and all(
            int.from_bytes(read_symbol(f"gSaveBlock{number}Ptr", size=4), "little") != 0
            for number in (1, 2)
        )
        if not pointers_ready:
            previous = self._last_observation
            if previous is None:
                previous = Observation("boot:unknown", "UNKNOWN", None, False, "none", "none", (), (),
                                       OpeningFlags(False, False, False), None, ())
            return replace(previous, context_id="transition:unavailable", game_state=state.name if state else "UNKNOWN",
                           position=None, controllable=False, menu_phase="none", battle_phase="none",
                           active_battler=None, opponent=None, trainer_id=None, can_run=False,
                           tasks=tuple(task.symbol for task in get_tasks()) if state is not None else (), scripts=(),
                           exits=(), objects=(), signs=(), shop_items=(), training_spots=(), tutorial_battle=False,
                           learning_move=None, learning_party_index=None, recent_outcomes=tuple(recent_outcomes))
        game_state = state.name
        position = None
        if state in {GameState.OVERWORLD, GameState.CHANGE_MAP, GameState.BATTLE_STARTING, GameState.BATTLE}:
            try:
                avatar = get_player_avatar()
                map_id = tuple(avatar.map_group_and_number)
                position = MapPosition(
                    map_id,
                    tuple(avatar.local_coordinates),
                    avatar.facing_direction,
                    _map_name(map_id, ""),
                )
            except (RuntimeError, ValueError):
                position = None

        menu_phase, battle_phase = _read_phases(state)
        if menu_phase in {"script", "yes_no"}:
            from modules.game import decode_string
            from modules.text_printer import get_text_printer

            try:
                if get_text_printer().active:
                    self._dialogue.remember(decode_string(read_symbol("gStringVar4", size=0x3E8)))
            except (RuntimeError, ValueError):
                # Some transition frames do not have a valid text-printer buffer.
                pass
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
                is_egg=pokemon.is_egg,
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
            stone_badge=get_event_flag("BADGE01_GET"),
            petalburg_tutorial=get_event_var("PETALBURG_GYM_STATE") >= 2,
            devon_goods_saved=get_event_var("PETALBURG_WOODS_STATE") == 1,
        )
        active_battler = None
        opponent = None
        trainer_id = None
        can_run = False
        tutorial_battle = False
        if state is GameState.BATTLE:
            from modules.battle_state import get_battle_state

            battle_state = get_battle_state()
            from modules.battle_state import BattleType
            tutorial_battle = BattleType.WallyTutorial in battle_state.type
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
        money = get_player().money
        shop_items = ()
        if menu_phase == "shop":
            from modules.mart import get_mart_buyable_items
            shop_items = tuple(ShopItem(item.name, item.price) for item in get_mart_buyable_items())
        learning_move, learning_party_index = _read_learning_move(menu_phase)
        controllable = player_avatar_is_controllable() if state is GameState.OVERWORLD else False
        landmarks = _read_landmarks(position if controllable and self.landmarks else None)
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
            self._dialogue.messages,
            (money, shop_items, learning_move, learning_party_index, landmarks.get("training_spots", ())),
        )
        observation = Observation(
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
            recent_dialogue=self._dialogue.messages,
            money=money,
            shop_items=shop_items,
            learning_move=learning_move,
            learning_party_index=learning_party_index,
            tutorial_battle=tutorial_battle,
            **landmarks,
        )
        self._last_observation = observation
        return observation


# Emerald marks a warp whose destination a script fills in at runtime by
# pointing it at map 127/127. The truck's three doors are the opening's only
# ones: the arrival cutscene decides where they lead.
DYNAMIC_WARP = (127, 127)


def _read_landmarks(position: MapPosition | None) -> dict[str, tuple]:
    """Read map landmarks and loaded NPC positions on the emulator owner thread.

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
    from modules.memory import get_event_flag_by_number

    location = get_map_data_for_current_position()
    if location is None:
        return {}

    here = tuple(location.map_group_and_number)
    exits: list[MapExit] = []
    for warp in location.warps:
        if (warp.destination_map_group, warp.destination_map_number) == DYNAMIC_WARP:
            # Nothing on this map knows where a dynamic warp goes, and asking
            # upstream makes it answer from the save block - which is how the
            # truck's door announced itself as Petalburg City. Offer the way
            # out without a destination instead of a confident wrong one.
            exits.append(MapExit(here, tuple(warp.local_coordinates), DYNAMIC_WARP, ""))
            continue
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
            key = tuple(neighbour.map_group_and_number)
            name = _map_name(key, neighbour.map_name)
            target = _reachable_border_tile(
                position, connection.direction, connection.offset, location.map_size, neighbour.map_size, key
            )
        except (RuntimeError, ValueError, IndexError):
            continue
        if target is None:
            continue
        exits.append(MapExit(key, target, key, name, connection.direction))

    loaded = {npc.local_id: npc for npc in get_map_objects()}
    objects: list[MapObject] = []
    for template in location.objects:
        # A clone template is a second copy of an object that is already offered.
        if template.kind != "normal":
            continue
        try:
            # Emerald removes an object while its hide flag is set: the rival is
            # not on Route 103 until the story puts him there.
            if template.flag_id and get_event_flag_by_number(template.flag_id):
                continue
        except (RuntimeError, ValueError):
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
                tuple(loaded[template.local_id].current_coords) if template.local_id in loaded else tuple(template.local_coordinates),
                template.script_symbol,
                template.trainer_type,
                defeated,
                template.local_id in loaded,
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

    from .training import training_spots
    return {"exits": tuple(exits), "objects": tuple(objects), "signs": tuple(signs),
            "training_spots": training_spots(position)}


@lru_cache(maxsize=256)
def _reachable_border_tile(
    position: MapPosition,
    direction: str,
    offset: int,
    here_size: tuple[int, int],
    there_size: tuple[int, int],
    destination_map: tuple[int, int],
) -> tuple[int, int] | None:
    """Choose a reachable crossing, checking the shared border center first.

    Cache by map and avatar tile to avoid repeating pathfinding every frame.
    Invalid transitional coordinates raise, so they are never cached as a
    permanently unreachable exit. Moving NPCs are rechecked by navigation.
    """
    from modules.map_path import calculate_path, PathFindingError

    here_width, here_height = here_size
    width, height = there_size
    if not (0 <= position.coordinates[0] < here_width and 0 <= position.coordinates[1] < here_height):
        raise ValueError("transitional avatar coordinates")
    if direction in {"North", "South"}:
        start, stop = max(0, offset), min(here_width, offset + width)
        targets = [(value - offset, height - 1 if direction == "North" else 0) for value in range(start, stop)]
    elif direction in {"East", "West"}:
        start, stop = max(0, offset), min(here_height, offset + height)
        targets = [(width - 1 if direction == "West" else 0, value - offset) for value in range(start, stop)]
    else:
        return None
    midpoint = len(targets) // 2
    for index in sorted(range(len(targets)), key=lambda candidate: abs(candidate - midpoint)):
        target = targets[index]
        try:
            calculate_path((position.map_id, position.coordinates), (destination_map, target), no_surfing=True)
        except PathFindingError:
            continue
        return target
    return None


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
    from modules.tasks import task_is_active
    from modules.battle_move_replacing import get_learn_move_state, LearnMoveState

    if game_state in {GameState.BATTLE, GameState.EVOLUTION, GameState.POKEMON_SUMMARY_SCREEN, GameState.PARTY_MENU}:
        if get_learn_move_state() in {LearnMoveState.AskWhetherToLearn, LearnMoveState.SelectMoveToReplace, LearnMoveState.ConfirmCancellation}:
            return "learn_move", "none"
    if task_is_active("Task_EvolutionScene"):
        return "evolution", "none"
    if task_is_active("Task_ShopMenu"):
        return "shop", "none"
    if task_is_active("Task_HandleYesNoInput"):
        return "yes_no", "none"
    if game_state is GameState.PARTY_MENU:
        from modules.battle_state import battle_is_active, get_battle_state
        if battle_is_active() and get_battle_state().own_side.is_fainted:
            return "forced_switch", "none"

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


def _read_learning_move(menu_phase: str) -> tuple[MoveState | None, int | None]:
    if menu_phase != "learn_move":
        return None, None
    from modules.memory import read_symbol, unpack_uint16
    from modules.pokemon import get_move_by_index
    from modules.battle_state import get_battle_state
    from modules.menu_parsers import get_party_menu_cursor_pos
    from modules.pokemon_party import get_party_size
    from modules.tasks import task_is_active

    if task_is_active("Task_HandleReplaceMoveYesNoInput"):
        data = get_party_menu_cursor_pos(get_party_size())
        move = get_move_by_index(data["data1"])
        index = data["slot_id"]
    else:
        move = get_move_by_index(unpack_uint16(read_symbol("gMoveToLearn", size=2)))
        index = get_battle_state().map_battle_party_index(read_symbol("gBattleStruct", 16, 1)[0])
    return MoveState(move.name, move.pp, move.pp, move.type.name, move.base_power, move.accuracy, move.description), index
