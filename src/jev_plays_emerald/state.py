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
class MoveState:
    name: str
    pp: int
    max_pp: int


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
class InventoryItem:
    name: str
    quantity: int


@dataclass(frozen=True)
class OpeningFlags:
    rescued_birch: bool
    received_pokedex: bool
    defeated_rival_route103: bool


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


def observation_context_id(
    game_state: str,
    map_id: tuple[int, int] | None,
    menu_phase: str,
    battle_phase: str,
    party: tuple[PartyMember, ...],
    inventory: tuple[InventoryItem, ...],
    opening_flags: OpeningFlags,
    active_battler: ActiveBattler | None = None,
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
    }
    encoded = json.dumps(payload, default=lambda value: value.__dict__, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]


class ObservationReader:
    """Capture all RAM-backed fields together on the thread that owns mGBA."""

    def __init__(self) -> None:
        self._owner_thread = threading.get_ident()

    def read(self, recent_outcomes: tuple[RecentOutcome, ...] = ()) -> Observation:
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("observations must be built on the emulator owner thread")

        from modules.items import get_item_bag
        from modules.memory import GameState, get_event_flag, get_game_state
        from modules.player import get_player_avatar, player_avatar_is_controllable
        from modules.pokemon_party import get_party

        state = get_game_state()
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
                    MoveState(move.move.name, move.pp, move.total_pp)
                    for move in pokemon.moves
                    if move is not None
                ),
            )
            for pokemon in get_party()
        )
        bag = get_item_bag()
        inventory = tuple(
            InventoryItem(slot.item.name, slot.quantity)
            for pocket in (bag.items, bag.key_items, bag.poke_balls, bag.tms_hms, bag.berries)
            for slot in pocket
        )
        opening_flags = OpeningFlags(
            rescued_birch=get_event_flag("RESCUED_BIRCH"),
            received_pokedex=get_event_flag("SYS_POKEDEX_GET"),
            defeated_rival_route103=get_event_flag("DEFEATED_RIVAL_ROUTE103"),
        )
        active_battler = None
        if state is GameState.BATTLE:
            from modules.battle_state import get_battle_state

            battler = get_battle_state().own_side.active_battler
            if battler is not None:
                active_battler = ActiveBattler(
                    battler.party_index,
                    tuple(
                        MoveState(move.move.name, move.pp, move.total_pp)
                        for move in battler.moves
                        if move is not None
                    ),
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
        )
        return Observation(
            context_id,
            game_state,
            position,
            player_avatar_is_controllable() if state is GameState.OVERWORLD else False,
            menu_phase,
            battle_phase,
            party,
            inventory,
            opening_flags,
            active_battler,
            tuple(recent_outcomes),
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
