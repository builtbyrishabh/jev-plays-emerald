"""PokéBot mode that owns observations and bounded semantic actions."""

from collections import deque
from collections.abc import Generator
from typing import TYPE_CHECKING

from modules.context import context
from modules.modes import BattleAction, BotMode

from jev_plays_emerald.actions import Action, ActionExecutor, FrameState, Outcome
from jev_plays_emerald.opening import legal_actions
from jev_plays_emerald.state import Observation, ObservationReader, RecentOutcome

if TYPE_CHECKING:
    from modules.encounter import EncounterInfo


class JevEmeraldMode(BotMode):
    """Own PokéBot's frame loop and keep RAM access on that owner thread."""

    def __init__(self, observation_reader: ObservationReader | None = None, executor: ActionExecutor | None = None):
        self._observation_reader = ObservationReader() if observation_reader is None else observation_reader
        self._paused = False
        self._latest_observation: Observation | None = None
        self._available_actions: tuple[Action, ...] = ()
        self._pending_action: Action | None = None
        self._last_started_action: Action | None = None
        self._active_run: Generator[Outcome | None, None, None] | None = None
        self._recent_outcomes: deque[RecentOutcome] = deque(maxlen=12)
        self._executor = ActionExecutor(_ModeFrameBoundary(self)) if executor is None else executor

    @staticmethod
    def name() -> str:
        return "Jev Emerald"

    @property
    def observation(self) -> Observation | None:
        return self._latest_observation

    @property
    def available_actions(self) -> tuple[Action, ...]:
        return self._available_actions

    @property
    def active_action(self) -> Action | None:
        return self._executor.current_action

    @property
    def recent_outcomes(self) -> tuple[RecentOutcome, ...]:
        return tuple(self._recent_outcomes)

    def submit_action(self, action: Action) -> None:
        if self._active_run is not None or self._pending_action is not None:
            raise RuntimeError("an action is already pending or running")
        if action not in self._available_actions:
            raise ValueError("action is not legal in the current context")
        self._pending_action = action

    def set_paused(self, paused: bool) -> None:
        """Request a pause; the owner thread releases inputs on its next frame."""

        self._paused = paused

    def resume_interrupted(self) -> bool:
        action = self._executor.revalidate_interrupted(self._available_actions)
        if action is None:
            return False
        self.submit_action(action)
        return True

    def run(self) -> Generator[None, None, None]:
        if context.emulator is not None:
            context.emulator.reset_held_buttons()
        while True:
            self._latest_observation = self._observation_reader.read(tuple(self._recent_outcomes))
            self._available_actions = legal_actions(self._latest_observation)

            if self._active_run is None and self._pending_action is not None and not self._paused:
                self._last_started_action = self._pending_action
                self._active_run = self._executor.execute(self._pending_action)
                self._pending_action = None

            if self._active_run is not None:
                outcome = next(self._active_run)
                if outcome is not None:
                    action = self._executor.current_action
                    if action is None and outcome is Outcome.INTERRUPTED:
                        action = self._executor.interrupted_action
                    if action is None:
                        # Executors clear current_action at their terminal yield. The mode's
                        # selected action remains the authoritative record for that frame.
                        action = self._last_started_action
                    if action is not None:
                        self._recent_outcomes.append(
                            RecentOutcome(action.id, outcome, self._executor.last_reason)
                        )
                    self._active_run.close()
                    self._active_run = None
                    self._last_started_action = None
                    if outcome is Outcome.FAILED:
                        self._paused = True
            elif self._paused and context.emulator is not None:
                context.emulator.reset_held_buttons()
            yield

    def on_battle_started(self, encounter: "EncounterInfo | None") -> BattleAction:
        """Keep battle frames in this mode instead of starting an upstream policy."""

        return BattleAction.CustomAction


class _ModeFrameBoundary:
    def __init__(self, mode: JevEmeraldMode) -> None:
        self._mode = mode

    def read_frame_state(self) -> FrameState:
        observation = self._mode.observation
        if observation is None:
            raise RuntimeError("no observation has been published")
        return FrameState(observation.game_state, observation.menu_phase, self._mode._paused)

    def read_context_id(self) -> str:
        observation = self._mode.observation
        if observation is None:
            raise RuntimeError("no observation has been published")
        return observation.context_id

    def reset_held_buttons(self) -> None:
        context.emulator.reset_held_buttons()
