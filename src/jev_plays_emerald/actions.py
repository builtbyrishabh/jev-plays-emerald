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
        from modules.player import get_player_location

        upstream = navigate_to(destination_map, destination)
        last_location = get_player_location()
        stalled_frames = 0
        try:
            while True:
                next(upstream)
                if get_game_state() is GameState.OVERWORLD:
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
        from modules.modes.util import ensure_facing_direction, wait_until_task_is_active
        from modules.player import get_player_avatar

        facing = "Left" if get_player_avatar().local_coordinates == (8, 14) else "Up"
        yield from ensure_facing_direction(facing)
        yield from wait_until_task_is_active("Task_HandleStarterChooseInput", "A")
        if starter == "treecko":
            context.emulator.press_button("Left")
            yield
        elif starter == "mudkip":
            context.emulator.press_button("Right")
            yield
        context.emulator.press_button("A")
        yield

    return ExecutionPlan(
        choose_starter,
        frozenset({"OVERWORLD", "CHOOSE_STARTER", "BATTLE_STARTING", "BATTLE"}),
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
