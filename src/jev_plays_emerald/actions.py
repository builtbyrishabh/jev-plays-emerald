"""Semantic actions and bounded execution at PokéBot's frame boundary."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    context_id: str


class Outcome(StrEnum):
    SUCCESS = "success"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


@dataclass(frozen=True)
class FrameState:
    game_state: str
    menu_phase: str = "none"
    paused: bool = False
    # The scripts Emerald is running right now, innermost last. Carried so an
    # interruption can say which one took over: "the game started a cutscene"
    # and "the game refused to let you leave town" look identical without it.
    scripts: tuple[str, ...] = ()


class FrameBoundary(Protocol):
    def read_frame_state(self) -> FrameState: ...

    def read_context_id(self) -> str: ...

    def reset_held_buttons(self) -> None: ...


class NavigationBlocked(RuntimeError):
    """Raised by a navigation plan when upstream cannot reach its next waypoint."""


@dataclass(frozen=True)
class ExecutionPlan:
    start: Callable[[], Generator[None, None, None]]
    allowed_states: frozenset[str]
    allowed_menu_phases: frozenset[str] = frozenset({"none"})
    retry_on: tuple[type[Exception], ...] = ()


ActionPlanFactory = Callable[[Action], ExecutionPlan]


class ActionExecutor:
    """Advance one upstream input generator per frame and stop it safely."""

    def __init__(
        self,
        boundary: FrameBoundary,
        *,
        dispatch: dict[str, ActionPlanFactory] | None = None,
        frame_limit: int = 3_600,
        max_navigation_replans: int = 2,
    ) -> None:
        if frame_limit <= 0:
            raise ValueError("frame_limit must be positive")
        if max_navigation_replans < 0:
            raise ValueError("max_navigation_replans cannot be negative")
        self._boundary = boundary
        self._dispatch = ACTION_EXECUTORS if dispatch is None else dispatch
        self._frame_limit = frame_limit
        self._max_navigation_replans = max_navigation_replans
        self.current_action: Action | None = None
        self.interrupted_action: Action | None = None
        self.failure_reason: str | None = None
        self.last_reason: str | None = None

    def execute(self, action: Action) -> Generator[Outcome | None, None, None]:
        """Run an action, yielding once per emulator frame and one terminal outcome."""

        if self.current_action is not None:
            raise RuntimeError("an action is already running")
        if action.context_id != self._boundary.read_context_id():
            self.failure_reason = "action context changed before execution"
            self.last_reason = self.failure_reason
            self._boundary.reset_held_buttons()
            yield Outcome.FAILED
            return

        action_kind = action.id.partition(":")[0]
        try:
            plan = self._dispatch[action_kind](action)
        except (KeyError, ValueError) as error:
            self.failure_reason = str(error) or f"unsupported action: {action.id}"
            self.last_reason = self.failure_reason
            self._boundary.reset_held_buttons()
            yield Outcome.FAILED
            return

        self.current_action = action
        self.failure_reason = None
        self.last_reason = None
        frames = 0
        replans = 0
        operation: Generator[None, None, None] | None = None

        try:
            while True:
                frame_state = self._boundary.read_frame_state()
                if frame_state.paused:
                    yield self._finish(Outcome.INTERRUPTED, "paused")
                    return
                if frame_state.game_state not in plan.allowed_states:
                    yield self._finish(Outcome.INTERRUPTED, f"game changed to {frame_state.game_state}")
                    return
                if frame_state.menu_phase not in plan.allowed_menu_phases:
                    script = f" ({frame_state.scripts[-1]})" if frame_state.scripts else ""
                    yield self._finish(
                        Outcome.INTERRUPTED, f"unexpected menu: {frame_state.menu_phase}{script}"
                    )
                    return
                if frames >= self._frame_limit:
                    yield self._finish(Outcome.FAILED, f"action exceeded {self._frame_limit} frames")
                    return

                if operation is None:
                    operation = plan.start()
                try:
                    next(operation)
                except StopIteration:
                    yield self._finish(Outcome.SUCCESS)
                    return
                except plan.retry_on:
                    operation.close()
                    operation = None
                    self._boundary.reset_held_buttons()
                    if replans >= self._max_navigation_replans:
                        yield self._finish(
                            Outcome.FAILED,
                            f"navigation remained blocked after {self._max_navigation_replans} replans",
                        )
                        return
                    replans += 1
                    continue
                except Exception as error:
                    yield self._finish(Outcome.FAILED, str(error) or type(error).__name__)
                    return

                frames += 1
                yield None
        finally:
            if operation is not None:
                operation.close()
            if self.current_action is action:
                self.current_action = None
                self._boundary.reset_held_buttons()

    def _finish(self, outcome: Outcome, reason: str | None = None) -> Outcome:
        action = self.current_action
        self._boundary.reset_held_buttons()
        self.current_action = None
        self.last_reason = reason
        self.failure_reason = reason if outcome is Outcome.FAILED else None
        if outcome is Outcome.INTERRUPTED:
            self.interrupted_action = action
        elif outcome is Outcome.SUCCESS:
            self.interrupted_action = None
        return outcome

    def revalidate_interrupted(self, legal_actions: Iterable[Action]) -> Action | None:
        """Return the new-context form of an interrupted goal only if it is still legal."""

        if self.interrupted_action is None:
            return None
        return next((action for action in legal_actions if action.id == self.interrupted_action.id), None)


def _walk_plan(action: Action) -> ExecutionPlan:
    try:
        _, map_group, map_number, x, y = action.id.split(":")
        destination_map = (int(map_group), int(map_number))
        destination = (int(x), int(y))
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid walk action: {action.id}") from error

    def navigate() -> Generator[None, None, None]:
        from modules.context import context
        from modules.memory import GameState, get_game_state
        from modules.modes.util.walking import TimedOutTryingToReachWaypointError, navigate_to
        from modules.player import get_player_avatar, get_player_location, player_avatar_is_controllable

        starting_location = get_player_location()
        upstream = navigate_to(destination_map, destination)
        last_location = starting_location
        stalled_frames = 0
        try:
            while True:
                next(upstream)
                if get_game_state() is GameState.OVERWORLD and player_avatar_is_controllable():
                    location = get_player_location()
                    if location == last_location:
                        stalled_frames += 1
                    else:
                        last_location = location
                        stalled_frames = 0
                    if stalled_frames >= 45:
                        raise NavigationBlocked("no navigation progress for 45 overworld frames")
                yield
        except StopIteration:
            pass
        except TimedOutTryingToReachWaypointError as error:
            raise NavigationBlocked(str(error)) from error
        finally:
            upstream.close()

        # A warp action can be offered while the avatar is already standing on
        # its landing tile. Upstream navigation then has no path to traverse, so
        # step back through the doorway instead of reporting a false success.
        doorway = (destination_map, destination)
        if starting_location == doorway and get_player_location() == doorway:
            opposite = {"Up": "Down", "Down": "Up", "Left": "Right", "Right": "Left"}
            button = opposite[get_player_avatar().facing_direction]
            while get_player_location() == doorway:
                context.emulator.hold_button(button)
                yield

    return ExecutionPlan(
        navigate,
        frozenset({"OVERWORLD", "CHANGE_MAP"}),
        retry_on=(NavigationBlocked,),
    )


def _talk_plan(action: Action) -> ExecutionPlan:
    try:
        local_object_id = int(action.id.split(":", 1)[1])
    except (IndexError, ValueError) as error:
        raise ValueError(f"invalid talk action: {action.id}") from error

    def talk() -> Generator[None, None, None]:
        from modules.modes.util.higher_level_actions import talk_to_npc
        from modules.map import get_map_data, get_map_data_for_current_position
        from modules.modes.util.walking import navigate_to, ensure_facing_direction
        from modules.context import context

        location = get_map_data_for_current_position()
        npc = next((obj for obj in location.objects if obj.local_id == local_object_id), None)
        if npc is not None:
            x, y = npc.local_coordinates
            for dx, dy, facing in ((0, 1, "Up"), (0, -1, "Down"), (1, 0, "Left"), (-1, 0, "Right")):
                counter = (x + dx, y + dy)
                if get_map_data(location.map_group_and_number, counter).tile_type == "Counter":
                    yield from navigate_to(location.map_group_and_number, (x + 2 * dx, y + 2 * dy))
                    yield from ensure_facing_direction(facing)
                    context.emulator.press_button("A")
                    yield
                    return
        yield from talk_to_npc(local_object_id)

    return ExecutionPlan(talk, frozenset({"OVERWORLD"}), frozenset({"none", "script"}))


def _starter_plan(action: Action) -> ExecutionPlan:
    starter = action.id.split(":", 1)[-1].casefold()
    if starter not in {"treecko", "torchic", "mudkip"}:
        raise ValueError(f"invalid starter action: {action.id}")

    def choose_starter() -> Generator[None, None, None]:
        from modules.context import context
        from modules.pokemon_party import get_party

        # The action is exposed only after Emerald has opened its starter
        # choice screen. Give that task one frame before moving its cursor.
        yield
        if starter == "treecko":
            context.emulator.press_button("Left")
            yield
        elif starter == "mudkip":
            context.emulator.press_button("Right")
            yield
        while not get_party():
            context.emulator.press_button("A")
            yield
        acquired = get_party()[0].species.name.casefold()
        if acquired != starter:
            raise RuntimeError(
                f"selected {starter}, but the acquired starter was {acquired}"
            )

    return ExecutionPlan(
        choose_starter,
        frozenset(
            {"OVERWORLD", "CHOOSE_STARTER", "UNKNOWN", "BATTLE_STARTING", "BATTLE"}
        ),
        allowed_menu_phases=frozenset({"none", "starter", "battle"}),
    )


def _battle_move_plan(action: Action) -> ExecutionPlan:
    try:
        move_index = int(action.id.split(":", 1)[1])
    except (IndexError, ValueError) as error:
        raise ValueError(f"invalid battle-move action: {action.id}") from error
    if move_index not in range(4):
        raise ValueError(f"invalid battle-move action: {action.id}")

    def use_move() -> Generator[None, None, None]:
        from modules.battle_action_selection import battle_action_use_move
        from modules.battle_state import get_battle_state
        from modules.battle_strategies import TurnAction

        yield from battle_action_use_move(TurnAction.UseMove, 0, move_index, get_battle_state())

    return ExecutionPlan(
        use_move,
        frozenset({"BATTLE"}),
        allowed_menu_phases=frozenset({"battle"}),
    )


ACTION_EXECUTORS: dict[str, ActionPlanFactory] = {
    "walk": _walk_plan,
    "talk": _talk_plan,
    "starter": _starter_plan,
    "battle-move": _battle_move_plan,
}


PLAYER_NAME = "Jev"


def _setup_plan(action: Action) -> ExecutionPlan:
    def setup():
        from modules.context import context
        from modules.memory import GameState, get_game_state
        from modules.tasks import task_is_active

        kind = action.id.partition(":")[2]
        if kind == "new-game":
            while get_game_state() in {GameState.TITLE_SCREEN, GameState.MAIN_MENU, GameState.UNKNOWN}:
                context.emulator.press_button("A")
                yield
        elif kind == "name":
            from modules.keyboard import type_in_naming_screen

            while not task_is_active("Task_HandleInput"):
                yield
            # Jev confirms the requested name; upstream types it using the live
            # keyboard state, including the lowercase page for e and v.
            yield from type_in_naming_screen(PLAYER_NAME, max_length=7)
        elif kind == "close-clock":
            while task_is_active("Task_ViewClock_HandleInput"):
                context.emulator.press_button("B")
                yield
        elif kind == "clock":
            context.emulator.press_button("A")
            yield
            while not task_is_active("Task_SetClock_HandleConfirmInput"):
                yield
            context.emulator.press_button("Up")  # Confirmation defaults to No.
            yield
            yield
            context.emulator.press_button("A")
            yield
        else:
            raise ValueError(f"unknown opening setup: {kind}")

    return ExecutionPlan(setup, frozenset({"TITLE_SCREEN", "MAIN_MENU", "NAMING_SCREEN", "UNKNOWN", "OVERWORLD"}),
                         frozenset({"none", "script"}))


def _dialogue_plan(action: Action) -> ExecutionPlan:
    def advance():
        from modules.context import context
        from modules.tasks import get_global_script_context, is_waiting_for_input

        from jev_plays_emerald.opening import dialogue_button

        while get_global_script_context().is_active:
            # A freshly created shop/choice can consume an already held input
            # before its task is visible. Only acknowledge waiting text here.
            if is_waiting_for_input():
                context.emulator.press_button(dialogue_button(tuple(get_global_script_context().stack)))
            yield

    return ExecutionPlan(advance, frozenset({"OVERWORLD", "CHANGE_MAP"}), frozenset({"none", "script"}))


def _heal_plan(action: Action) -> ExecutionPlan:
    center_name = action.id.partition(":")[2]
    centers = {"oldale": "OldaleTown", "petalburg": "PetalburgCity", "rustboro": "RustboroCity"}
    if center_name not in centers:
        raise ValueError(f"unsupported healing center: {center_name}")

    def heal():
        from modules.map_data import PokemonCenter
        from modules.modes.util.higher_level_actions import heal_in_pokemon_center
        from modules.pokemon_party import get_party

        from modules.player import get_player_avatar
        from jev_plays_emerald.gameplay import CENTER_INTERIORS

        if tuple(get_player_avatar().map_group_and_number) == CENTER_INTERIORS[center_name]:
            from modules.modes.util.walking import navigate_to, ensure_facing_direction
            from modules.modes.util.tasks_scripts import wait_for_yes_no_question, wait_for_no_script_to_run
            from modules.context import context

            yield from navigate_to(CENTER_INTERIORS[center_name], (7, 4))
            yield from ensure_facing_direction("Up")
            context.emulator.press_button("A")
            yield
            yield from wait_for_yes_no_question("Yes")
            yield from wait_for_no_script_to_run("B")
        else:
            yield from heal_in_pokemon_center(getattr(PokemonCenter, centers[center_name]))
        party = get_party()
        if not party or any(p.current_hp != p.total_hp or p.status_condition.name != "Healthy"
                            or any(move.pp != move.total_pp for move in p.moves if move is not None)
                            for p in party):
            raise RuntimeError("Pokémon Center finished without restoring party HP, status and PP")

    return ExecutionPlan(heal, frozenset({"OVERWORLD", "CHANGE_MAP"}), frozenset({"none", "script", "yes_no"}))


def _battle_run_plan(action: Action) -> ExecutionPlan:
    def run():
        from modules.context import context
        from modules.battle_state import get_battle_state
        from modules.battle_strategies._util import BattleStrategyUtil
        from modules.battle_action_selection import scroll_to_battle_action

        if BattleStrategyUtil(get_battle_state()).get_escape_chance() <= 0:
            raise RuntimeError("running is not legal in this battle")
        yield from scroll_to_battle_action(3)
        context.emulator.press_button("A")
        yield

    return ExecutionPlan(run, frozenset({"BATTLE"}), frozenset({"battle"}))



def _interact_plan(action: Action) -> ExecutionPlan:
    """Stand next to a map tile, face it and press A.

    Emerald puts clocks, signs, TVs and the PC on tiles you cannot stand on,
    so the plan walks to whichever neighbouring tile it can actually reach.
    """

    try:
        _, x, y = action.id.split(":")
        target = (int(x), int(y))
    except ValueError as error:
        raise ValueError(f"invalid interact action: {action.id}") from error

    def interact() -> Generator[None, None, None]:
        from modules.context import context
        from modules.modes.util import ensure_facing_direction
        from modules.modes.util.walking import TimedOutTryingToReachWaypointError, navigate_to
        from modules.player import get_player_location

        here, _ = get_player_location()
        neighbours = (
            (target[0], target[1] + 1),
            (target[0] - 1, target[1]),
            (target[0] + 1, target[1]),
            (target[0], target[1] - 1),
        )
        for spot in neighbours:
            try:
                yield from navigate_to(here, spot)
                break
            except (TimedOutTryingToReachWaypointError, NavigationBlocked, RuntimeError, ValueError):
                continue
        else:
            raise NavigationBlocked(f"no reachable tile next to {target}")
        yield from ensure_facing_direction(target)
        context.emulator.press_button("A")
        yield

    return ExecutionPlan(
        interact,
        frozenset({"OVERWORLD", "CHANGE_MAP"}),
        frozenset({"none", "script"}),
        retry_on=(NavigationBlocked,),
    )


def _battle_switch_plan(action: Action) -> ExecutionPlan:
    """Send out a benched party member, mirroring PokéBot's RotateLead inputs.

    Opening the party list moves the game into PARTY_MENU for a few frames, so
    that state is allowed alongside BATTLE - otherwise the executor would treat
    its own menu as an interruption.
    """

    try:
        party_index = int(action.id.split(":", 1)[1])
    except (IndexError, ValueError) as error:
        raise ValueError(f"invalid battle-switch action: {action.id}") from error

    def switch() -> Generator[None, None, None]:
        from modules.battle_menuing import scroll_to_battle_action
        from modules.battle_state import get_battle_state
        from modules.context import context
        from modules.memory import GameState, get_game_state
        from modules.menuing import scroll_to_party_menu_index
        from modules.pokemon_party import get_party_size

        battle_state = get_battle_state()
        if party_index >= get_party_size():
            raise RuntimeError(f"cannot switch to slot {party_index}: the party is smaller")
        active = battle_state.own_side.active_battler
        if active is not None and party_index == active.party_index:
            raise RuntimeError("that Pokémon is already in battle")
        in_battle_index = battle_state.map_battle_party_index(party_index)
        yield from scroll_to_battle_action(2)
        for _ in range(5):
            yield
        context.emulator.press_button("A")
        yield from scroll_to_party_menu_index(in_battle_index)
        while get_game_state() == GameState.PARTY_MENU:
            context.emulator.press_button("A")
            yield

    return ExecutionPlan(switch, frozenset({"BATTLE", "PARTY_MENU"}), frozenset({"battle", "party_menu"}))


def _battle_item_plan(action: Action) -> ExecutionPlan:
    """Use a bag item on the active Pokémon during battle.

    Healing and PP items need a target, so the active battler's party slot is
    passed; stat items apply to whoever is out and take no target. The bag and
    party sub-menus move the game out of BATTLE briefly, so both are allowed.
    """

    try:
        name = action.id.split(":", 1)[1]
    except IndexError as error:
        raise ValueError(f"invalid battle-item action: {action.id}") from error
    if not name:
        raise ValueError(f"invalid battle-item action: {action.id}")

    def use_item() -> Generator[None, None, None]:
        from modules.battle_action_selection import battle_action_use_item
        from modules.battle_state import get_battle_state
        from modules.items import ItemBattleUse, get_item_by_name

        item = get_item_by_name(name)
        battle_state = get_battle_state()
        target = None
        if item.battle_use in (ItemBattleUse.Healing, ItemBattleUse.PpRecovery):
            active = battle_state.own_side.active_battler
            if active is None:
                raise RuntimeError(f"{name} needs a target Pokémon, but none is active")
            target = active.party_index
        yield from battle_action_use_item(battle_state, item, target)

    return ExecutionPlan(
        use_item,
        frozenset({"BATTLE", "BAG_MENU", "PARTY_MENU"}),
        frozenset({"battle", "bag_menu", "party_menu"}),
    )


def _catch_plan(action: Action) -> ExecutionPlan:
    """Throw a Poké Ball at the wild opponent. Opens the in-battle bag only."""

    try:
        name = action.id.split(":", 1)[1]
    except IndexError as error:
        raise ValueError(f"invalid catch action: {action.id}") from error
    if not name:
        raise ValueError(f"invalid catch action: {action.id}")

    def throw() -> Generator[None, None, None]:
        from modules.battle_action_selection import battle_action_use_item
        from modules.battle_state import get_battle_state
        from modules.items import get_item_by_name

        yield from battle_action_use_item(get_battle_state(), get_item_by_name(name), None)

    return ExecutionPlan(throw, frozenset({"BATTLE", "BAG_MENU"}), frozenset({"battle", "bag_menu"}))


ACTION_EXECUTORS.update({"setup": _setup_plan, "dialogue": _dialogue_plan,
                         "heal": _heal_plan, "battle-run": _battle_run_plan,
                         "interact": _interact_plan, "battle-switch": _battle_switch_plan,
                         "battle-item": _battle_item_plan, "catch": _catch_plan})


def _answer_plan(action: Action) -> ExecutionPlan:
    answer = action.id.partition(':')[2]
    if answer not in {'yes', 'no'}:
        raise ValueError('answer must be yes or no')

    def answer_question():
        from modules.modes.util.tasks_scripts import wait_for_yes_no_question
        yield from wait_for_yes_no_question(answer.title())

    return ExecutionPlan(answer_question, frozenset({'OVERWORLD'}), frozenset({'yes_no', 'script', 'none'}))


def _shop_plan(action: Action) -> ExecutionPlan:
    def shop():
        from modules.context import context
        from modules.tasks import task_is_active
        if action.id == 'shop-exit':
            while task_is_active('Task_ShopMenu'):
                context.emulator.press_button('B')
                yield
        else:
            from modules.items import get_item_by_name
            from modules.modes.util.higher_level_actions import buy_in_shop
            _, name, quantity = action.id.split(':')
            if int(quantity) != 1:
                raise ValueError('buy one item per decision')
            yield from buy_in_shop([(get_item_by_name(name), 1)])

    return ExecutionPlan(shop, frozenset({'OVERWORLD', 'UNKNOWN'}), frozenset({'shop', 'script', 'none', 'yes_no'}))


def _forced_switch_plan(action: Action) -> ExecutionPlan:
    index = int(action.id.partition(':')[2])

    def switch():
        from modules.context import context
        from modules.memory import get_game_state, GameState
        from modules.menuing import scroll_to_party_menu_index
        from modules.pokemon_party import get_party
        party = get_party()
        if index not in range(len(party)) or party[index].current_hp <= 0 or party[index].is_egg:
            raise ValueError('replacement must be a healthy party member')
        # In PARTY_MENU the party is already in battle order; do not map twice.
        yield from scroll_to_party_menu_index(index)
        while get_game_state() is GameState.PARTY_MENU:
            context.emulator.press_button('A')
            yield

    return ExecutionPlan(switch, frozenset({'PARTY_MENU', 'BATTLE'}), frozenset({'forced_switch', 'party_menu', 'battle'}))


def _learn_move_plan(action: Action) -> ExecutionPlan:
    choice = action.id.partition(':')[2]
    index = 4 if choice == 'skip' else int(choice)
    if index not in range(5):
        raise ValueError('invalid move replacement')

    def learn():
        from modules.context import context
        from modules.battle_move_replacing import (
            get_learn_move_state, LearnMoveState, _get_move_selection_cursor,
        )
        # The upstream handler only consults its strategy at the first prompt.
        # Read the same upstream state/cursor here so a resumed selection still
        # honors the move the player chose, including cancelling a prior decline.
        while True:
            phase = get_learn_move_state()
            if phase is LearnMoveState.DialogueNotActive:
                return
            if phase is LearnMoveState.AskWhetherToLearn:
                button = 'B' if index == 4 else 'A'
            elif phase is LearnMoveState.ConfirmCancellation:
                button = 'A' if index == 4 else 'B'
            elif phase is LearnMoveState.SelectMoveToReplace:
                cursor = _get_move_selection_cursor()
                button = 'Down' if cursor < index else 'Up' if cursor > index else 'A'
            else:
                button = 'B'
            context.emulator.press_button(button)
            yield
            if button in {'Up', 'Down'}:
                yield

    return ExecutionPlan(learn, frozenset({'BATTLE', 'EVOLUTION', 'UNKNOWN', 'POKEMON_SUMMARY_SCREEN', 'PARTY_MENU'}),
                         frozenset({'learn_move', 'battle', 'evolution', 'pokemon_summary_screen', 'party_menu', 'none'}))


def _evolution_plan(action: Action) -> ExecutionPlan:
    choice = action.id.partition(':')[2]
    if choice not in {'yes', 'no'}:
        raise ValueError('invalid evolution choice')

    def evolve():
        from modules.context import context
        from modules.tasks import task_is_active
        from modules.battle_move_replacing import get_learn_move_state, LearnMoveState
        while task_is_active('Task_EvolutionScene'):
            # Evolution can immediately teach a move. End this choice there so
            # the player, not an upstream default strategy, picks what to forget.
            if get_learn_move_state() is LearnMoveState.AskWhetherToLearn:
                return
            context.emulator.press_button('A' if choice == 'yes' else 'B')
            yield

    return ExecutionPlan(evolve, frozenset({'EVOLUTION', 'BATTLE', 'OVERWORLD'}),
                         frozenset({'evolution', 'learn_move', 'battle', 'none'}))


def _field_item_plan(action: Action) -> ExecutionPlan:
    _, name, slot = action.id.split(':')
    index = int(slot)
    from jev_plays_emerald.gameplay import FIELD_HEALING_ITEMS
    if name not in FIELD_HEALING_ITEMS:
        raise ValueError('unsupported field item')

    def use():
        from modules.context import context
        from modules.items import get_item_by_name, get_item_bag
        from modules.memory import get_game_state, GameState
        from modules.menuing import StartMenuNavigator, scroll_to_party_menu_index
        from modules.modes.util.items import scroll_to_item_in_bag
        from modules.pokemon_party import get_party
        from modules.player import player_avatar_is_controllable

        item = get_item_by_name(name)
        party = get_party()
        if index not in range(len(party)) or not 0 < party[index].current_hp < party[index].total_hp:
            raise ValueError('healing item needs a living injured target')
        before_quantity = get_item_bag().quantity_of(item)
        before_hp = party[index].current_hp
        if before_quantity <= 0:
            raise ValueError('item is not in the bag')
        yield from StartMenuNavigator('BAG').step()
        yield from scroll_to_item_in_bag(item)
        while get_game_state() is not GameState.PARTY_MENU:
            context.emulator.press_button('A')
            yield
        yield from scroll_to_party_menu_index(index)
        while get_item_bag().quantity_of(item) == before_quantity:
            context.emulator.press_button('A')
            yield
        while get_game_state() is not GameState.OVERWORLD or not player_avatar_is_controllable():
            context.emulator.press_button('B')
            yield
        if get_party()[index].current_hp <= before_hp:
            raise RuntimeError('healing item was consumed without restoring target HP')

    return ExecutionPlan(use, frozenset({'OVERWORLD', 'UNKNOWN', 'BAG_MENU', 'PARTY_MENU'}),
                         frozenset({'none', 'start', 'bag_menu', 'party_menu'}))


ACTION_EXECUTORS.update({'answer': _answer_plan, 'shop-buy': _shop_plan, 'shop-exit': _shop_plan,
                         'forced-switch': _forced_switch_plan, 'learn-move': _learn_move_plan,
                         'evolve': _evolution_plan, 'field-item': _field_item_plan})


def _tutorial_plan(action: Action) -> ExecutionPlan:
    def advance():
        from modules.context import context
        from modules.memory import GameState, get_game_state
        from modules.battle_state import BattleType, get_battle_type
        while get_game_state() is GameState.BATTLE and BattleType.WallyTutorial in get_battle_type():
            context.emulator.press_button('B')
            yield
    return ExecutionPlan(advance, frozenset({'BATTLE', 'BATTLE_ENDING', 'OVERWORLD'}), frozenset({'battle', 'none', 'script'}))


ACTION_EXECUTORS['tutorial'] = _tutorial_plan


def _training_plan(action: Action) -> ExecutionPlan:
    from .training import training_plan
    return training_plan(action)


ACTION_EXECUTORS["train"] = _training_plan


def _caught_dex_plan(action: Action) -> ExecutionPlan:
    def close():
        from modules.context import context
        from modules.tasks import task_is_active
        while task_is_active('Task_HandleCaughtMonPageInput'):
            context.emulator.press_button('B')
            yield
    return ExecutionPlan(close, frozenset({'UNKNOWN', 'BATTLE'}), frozenset({'none', 'battle'}))


ACTION_EXECUTORS['caught-dex'] = _caught_dex_plan


def _keep_species_name_plan(action: Action) -> ExecutionPlan:
    def keep_name():
        from modules.keyboard import get_naming_screen_data, type_in_naming_screen
        while get_naming_screen_data() is None:
            yield
        # Emerald interprets an empty confirmed nickname as keeping the default.
        # Upstream types/deletes through the keyboard and confirms with inputs.
        yield from type_in_naming_screen("", max_length=10)
    return ExecutionPlan(keep_name, frozenset({"NAMING_SCREEN", "UNKNOWN", "OVERWORLD", "BATTLE"}),
                         frozenset({"none", "script", "battle", "yes_no"}))


ACTION_EXECUTORS["nickname"] = _keep_species_name_plan
