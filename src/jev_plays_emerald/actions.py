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
                    yield self._finish(Outcome.INTERRUPTED, f"unexpected menu: {frame_state.menu_phase}")
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
        from modules.memory import GameState, get_game_state
        from modules.modes.util.walking import TimedOutTryingToReachWaypointError, navigate_to
        from modules.player import get_player_location, player_avatar_is_controllable

        upstream = navigate_to(destination_map, destination)
        last_location = get_player_location()
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
            return
        except TimedOutTryingToReachWaypointError as error:
            raise NavigationBlocked(str(error)) from error
        finally:
            upstream.close()

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
            while not task_is_active("Task_HandleInput"):
                yield
            # Fixed keyboard positions on the supported English Emerald ROM.
            # This is setup configuration, never a model-selected game action.
            for button in ("Down", "Right", "Right", "Right", "A", "Up", "Right", "A",
                           "Down", "Down", "Down", "Left", "Left", "A", "Start", "A"):
                context.emulator.press_button(button)
                for _ in range(17):
                    yield
            while get_game_state() is GameState.NAMING_SCREEN:
                context.emulator.press_button("A")
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
        from modules.tasks import get_global_script_context

        from jev_plays_emerald.opening import dialogue_button

        while get_global_script_context().is_active:
            context.emulator.press_button(dialogue_button(tuple(get_global_script_context().stack)))
            yield

    return ExecutionPlan(advance, frozenset({"OVERWORLD", "CHANGE_MAP"}), frozenset({"none", "script"}))


def _goal_plan(action: Action) -> ExecutionPlan:
    def goal():
        from modules.context import context
        from modules.map_data import MapRSE
        from modules.map import get_map_objects
        from modules.player import get_player, get_player_location
        from modules.modes.util import ensure_facing_direction
        from modules.modes.util.higher_level_actions import talk_to_npc

        here, _ = get_player_location()
        male = get_player().gender == "male"
        kind = action.id.partition(":")[2]
        targets = {
            "leave-truck": (MapRSE.INSIDE_OF_TRUCK, (4, 2)),
            "upstairs": (here, (8, 2) if male else (2, 2)),
            "rival-upstairs": (here, (2, 2) if male else (8, 2)),
            "clock": (here, (5, 2)),
            "rival-house": (MapRSE.LITTLEROOT_TOWN, (14, 8) if male else (5, 8)),
            "meet-rival": (here, (5, 5)),
            "leave-lab": (here, (6, 12)),
            "birch-bag": (MapRSE.ROUTE101, (7, 15)),
            "oldale": (MapRSE.OLDALE_TOWN, (10, 10)),
            "rival": (MapRSE.ROUTE103, (10, 4)),
        }
        if kind == "downstairs":
            destination = (here, (7, 1) if here == MapRSE.LITTLEROOT_TOWN_BRENDANS_HOUSE_2F else (1, 1))
        elif kind == "leave-house":
            destination = (here, (8, 8) if here == MapRSE.LITTLEROOT_TOWN_BRENDANS_HOUSE_1F else (2, 8))
        else:
            destination = targets[kind]
        map_id, coordinates = destination
        group, number = map_id.value if isinstance(map_id, MapRSE) else map_id
        walk = Action(f"walk:{group}:{number}:{coordinates[0]}:{coordinates[1]}", action.label, action.context_id)
        yield from _walk_plan(walk).start()
        if kind in {"clock", "meet-rival", "birch-bag", "rival"}:
            if kind == "rival":
                # Resolve the live object at the source-backed rival landmark.
                npc = next((obj for obj in get_map_objects() if obj.current_coords == (10, 3)), None)
                if npc is None:
                    raise RuntimeError("Route 103 rival object is not at the expected landmark")
                yield from talk_to_npc(npc.local_id)
            else:
                yield from ensure_facing_direction("Up")
                context.emulator.press_button("A")
                yield

    return ExecutionPlan(goal, frozenset({"OVERWORLD", "CHANGE_MAP"}), retry_on=(NavigationBlocked,))


def _heal_plan(action: Action) -> ExecutionPlan:
    if action.id != "heal:oldale":
        raise ValueError("only Oldale healing is in opening scope")

    def heal():
        from modules.map_data import PokemonCenter
        from modules.modes.util.higher_level_actions import heal_in_pokemon_center
        from modules.pokemon_party import get_party

        yield from heal_in_pokemon_center(PokemonCenter.OldaleTown)
        party = get_party()
        if not party or any(p.current_hp != p.total_hp or p.status_condition.name != "Healthy"
                            or any(move.pp != move.total_pp for move in p.moves if move is not None)
                            for p in party):
            raise RuntimeError("Pokémon Center finished without restoring party HP, status and PP")

    return ExecutionPlan(heal, frozenset({"OVERWORLD", "CHANGE_MAP"}), frozenset({"none", "script"}))


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


ACTION_EXECUTORS.update({"setup": _setup_plan, "dialogue": _dialogue_plan, "goal": _goal_plan,
                         "heal": _heal_plan, "battle-run": _battle_run_plan})
