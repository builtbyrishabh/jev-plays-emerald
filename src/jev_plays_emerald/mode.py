"""PokéBot mode that owns observations and bounded semantic actions."""

import asyncio
from collections import deque
from collections.abc import Generator
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from threading import Lock
from typing import TYPE_CHECKING, Protocol

from modules.context import context
from modules.modes import BattleAction, BotMode

from jev_plays_emerald.actions import Action, ActionExecutor, FrameState, Outcome
from jev_plays_emerald.jev import JevChoice, JevGatewayError, JevTimeoutError, JsonValue
from jev_plays_emerald.opening import (
    RivalProgress,
    decision_instructions,
    legal_actions,
    open_world_spike_enabled,
)
from jev_plays_emerald.service import default_choice_client
from jev_plays_emerald.state import Observation, ObservationReader, RecentOutcome
from jev_plays_emerald.telemetry import DecisionTelemetry

if TYPE_CHECKING:
    from modules.battle_state import BattleOutcome
    from modules.encounter import EncounterInfo


class ChoiceClient(Protocol):
    async def choose(
        self,
        *,
        state: JsonValue,
        options: dict[str, JsonValue | None],
        instructions: JsonValue,
    ) -> JevChoice: ...


class _SingleRequestWorker:
    """Use one process-wide worker without ever queueing a second HTTP call."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev-choice")
        self._lock = Lock()
        self._active: Future[JevChoice] | None = None

    def submit(self, function, /, *args, **kwargs) -> Future[JevChoice]:
        with self._lock:
            if self._active is not None and not self._active.done():
                raise RuntimeError("a Jev request is already in flight")
            future = self._executor.submit(function, *args, **kwargs)
            self._active = future
            return future


_MODEL_WORKER = _SingleRequestWorker()


@dataclass(frozen=True)
class _PendingDecision:
    context_id: str
    generation: int
    actions: tuple[Action, ...]
    state: JsonValue
    options: dict[str, JsonValue | None]
    attempt: int
    future: Future[JevChoice]


class JevEmeraldMode(BotMode):
    """Own PokéBot's frame loop and keep RAM access on that owner thread."""

    def __init__(
        self,
        observation_reader: ObservationReader | None = None,
        executor: ActionExecutor | None = None,
        *,
        gateway: ChoiceClient | None = None,
        model_worker: Executor | None = None,
        telemetry: DecisionTelemetry | None = None,
    ):
        self._observation_reader = (
            ObservationReader(landmarks=open_world_spike_enabled())
            if observation_reader is None
            else observation_reader
        )
        self._paused = False
        self._progress = RivalProgress()
        self._latest_observation: Observation | None = None
        self._available_actions: tuple[Action, ...] = ()
        self._pending_action: Action | None = None
        self._last_started_action: Action | None = None
        self._active_run: Generator[Outcome | None, None, None] | None = None
        self._recent_outcomes: deque[RecentOutcome] = deque(maxlen=12)
        self._executor = ActionExecutor(_ModeFrameBoundary(self)) if executor is None else executor
        self._gateway = gateway
        self._model_worker = _MODEL_WORKER if model_worker is None else model_worker
        self._telemetry = DecisionTelemetry() if telemetry is None else telemetry
        self._state_lock = Lock()
        self._decision_generation = 0
        self._pending_decision: _PendingDecision | None = None

    @staticmethod
    def name() -> str:
        return "Jev Emerald"

    @property
    def completed(self) -> bool:
        return self._progress.completed

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

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def status(self):
        return self._telemetry.snapshot

    def submit_action(self, action: Action) -> None:
        with self._state_lock:
            if self._active_run is not None or self._pending_action is not None:
                raise RuntimeError("an action is already pending or running")
            if action not in self._available_actions:
                raise ValueError("action is not legal in the current context")
            self._decision_generation += 1
            self._pending_action = action

    def set_paused(self, paused: bool) -> None:
        """Request a pause; the owner thread releases inputs on its next frame."""

        with self._state_lock:
            if paused == self._paused:
                return
            self._paused = paused
            self._decision_generation += 1
            if paused:
                self._pending_action = None
        if paused:
            self._telemetry.pause()

    def resume_interrupted(self) -> bool:
        action = self._executor.revalidate_interrupted(self._available_actions)
        if action is None:
            return False
        self.submit_action(action)
        return True

    def run(self) -> Generator[None, None, None]:
        if context.emulator is not None:
            context.emulator.reset_held_buttons()
        try:
            while True:
                observation = self._observation_reader.read(tuple(self._recent_outcomes))
                self._progress.observe(observation)
                if self.completed and not self._paused:
                    self.set_paused(True)
                actions = () if self.completed else legal_actions(observation)
                with self._state_lock:
                    self._latest_observation = observation
                    self._available_actions = actions
                self._telemetry.observe(
                    self._latest_observation.context_id, self._available_actions
                )

                handled_response = self._poll_model_response()
                if (
                    not handled_response
                    and self._active_run is None
                    and self._pending_action is None
                    and self._pending_decision is None
                    and not self._paused
                ):
                    self._choose_or_submit()

                self._advance_nondecision_frame()

                if self._pending_decision is not None and context.emulator is not None:
                    context.emulator.reset_held_buttons()

                with self._state_lock:
                    if self._active_run is None and self._pending_action is not None and not self._paused:
                        self._last_started_action = self._pending_action
                        # Creating the generator claims the action; advancing it below
                        # remains on the owner thread, outside the state lock.
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
                            if action.id.startswith("starter:") and outcome is Outcome.SUCCESS:
                                self._progress.starter_acquired = bool(observation.party)
                            self._recent_outcomes.append(
                                RecentOutcome(action.id, outcome, self._executor.last_reason)
                            )
                            self._telemetry.outcome(
                                action, outcome, self._executor.last_reason
                            )
                        self._active_run.close()
                        with self._state_lock:
                            self._active_run = None
                            self._last_started_action = None
                            if outcome is Outcome.FAILED and not open_world_spike_enabled():
                                self._paused = True
                                self._decision_generation += 1
                                self._pending_action = None
                elif self._paused and context.emulator is not None:
                    context.emulator.reset_held_buttons()
                yield
        finally:
            with self._state_lock:
                self._decision_generation += 1
                self._pending_action = None
                pending = self._pending_decision
                self._pending_decision = None
                active_run = self._active_run
                self._active_run = None
                self._last_started_action = None
            if pending is not None:
                pending.future.cancel()
            if active_run is not None:
                active_run.close()
            if context.emulator is not None:
                context.emulator.reset_held_buttons()

    def _advance_nondecision_frame(self) -> None:
        observation = self._latest_observation
        if (
            observation is not None
            and observation.game_state == "BATTLE"
            and observation.battle_phase == "no"
            and not self._available_actions
            and self._active_run is None
            and self._pending_action is None
            and self._pending_decision is None
            and not self._paused
            and context.emulator is not None
        ):
            # Emerald's battle script waits for acknowledgement between turns.
            # This advances dialogue; move selection remains a semantic action.
            context.emulator.press_button("B")

    def _choose_or_submit(self) -> None:
        with self._state_lock:
            if (
                self._paused
                or self._active_run is not None
                or self._pending_action is not None
                or self._pending_decision is not None
                or not self._available_actions
            ):
                return
            actions = self._available_actions
            if len(actions) == 1:
                self._pending_action = actions[0]
        if len(actions) == 1:
            action = actions[0]
            self._telemetry.selected(
                context_id=action.context_id,
                source="deterministic",
                action_id=action.id,
            )
            if self._paused:
                self._telemetry.pause()
            return
        self._start_model_request(actions, attempt=1)

    def _start_model_request(
        self,
        actions: tuple[Action, ...],
        *,
        attempt: int,
        generation: int | None = None,
    ) -> None:
        observation = self._latest_observation
        if observation is None:
            return
        state = asdict(observation)
        state["observation_note"] = (
            "HP values are exact observations read from game memory. "
            "Opponent moves and future random outcomes are not provided."
        )
        options: dict[str, JsonValue | None] = {
            action.id: action.label for action in actions
        }
        instructions = decision_instructions(observation)
        with self._state_lock:
            if (
                self._paused
                or self._active_run is not None
                or self._pending_action is not None
                or self._pending_decision is not None
                or generation is not None and generation != self._decision_generation
            ):
                return
            try:
                future = self._model_worker.submit(
                    _request_choice,
                    self._gateway,
                    state,
                    options,
                    instructions,
                )
            except RuntimeError:
                return
            self._pending_decision = _PendingDecision(
                context_id=observation.context_id,
                generation=self._decision_generation,
                actions=actions,
                state=state,
                options=options,
                attempt=attempt,
                future=future,
            )
            self._telemetry.requested(
                context_id=observation.context_id,
                attempt=attempt,
                state=state,
                criteria=options,
                instructions=instructions,
            )
            self._telemetry.pending(context_id=observation.context_id, attempt=attempt)

    def _poll_model_response(self) -> bool:
        pending = self._pending_decision
        if pending is None or not pending.future.done():
            return False
        with self._state_lock:
            self._pending_decision = None

        try:
            result = pending.future.result()
        except Exception as error:
            retry = _is_transient(error) and pending.attempt <= 2
            with self._state_lock:
                stale = self._pending_is_stale(pending)
                if not stale and not retry:
                    self._paused = True
                    self._decision_generation += 1
                    self._pending_action = None
            if stale:
                self._telemetry.discard_stale()
                if self._paused:
                    self._telemetry.pause()
            elif retry:
                self._start_model_request(
                    pending.actions,
                    attempt=pending.attempt + 1,
                    generation=pending.generation,
                )
            else:
                self._telemetry.error(str(error) or type(error).__name__)
            return True

        action = next(
            (action for action in pending.actions if action.id == result.choice), None
        )
        with self._state_lock:
            stale = self._pending_is_stale(pending)
            if not stale:
                if action is None:
                    self._paused = True
                    self._decision_generation += 1
                    self._pending_action = None
                else:
                    self._pending_action = action
        if not stale and action is None:
            self._telemetry.error("Jev returned an action outside the offered choices")
            return True
        self._telemetry.responded(
            context_id=pending.context_id,
            attempt=pending.attempt,
            result=result,
            disposition="stale" if stale else "accepted",
        )
        if stale:
            self._telemetry.discard_stale()
            if self._paused:
                self._telemetry.pause()
            return True
        self._telemetry.selected(
            context_id=pending.context_id,
            source="model",
            action_id=action.id,
            result=result,
        )
        with self._state_lock:
            paused = self._paused
        if paused:
            self._telemetry.pause()
        return True

    def _pending_is_stale(self, pending: _PendingDecision) -> bool:
        return (
            self._paused
            or pending.generation != self._decision_generation
            or self._latest_observation is None
            or pending.context_id != self._latest_observation.context_id
            or pending.actions != self._available_actions
        )

    def on_battle_started(self, encounter: "EncounterInfo | None") -> BattleAction:
        """Keep battle frames in this mode instead of starting an upstream policy."""

        return BattleAction.CustomAction

    def on_battle_ended(self, outcome: "BattleOutcome") -> None:
        self._progress.battle_ended(outcome.name)
        self._telemetry.battle_ended(outcome.name)


class _ModeFrameBoundary:
    def __init__(self, mode: JevEmeraldMode) -> None:
        self._mode = mode

    def read_frame_state(self) -> FrameState:
        observation = self._mode.observation
        if observation is None:
            raise RuntimeError("no observation has been published")
        return FrameState(
            observation.game_state,
            observation.menu_phase,
            self._mode._paused,
            observation.scripts,
        )

    def read_context_id(self) -> str:
        observation = self._mode.observation
        if observation is None:
            raise RuntimeError("no observation has been published")
        return observation.context_id

    def reset_held_buttons(self) -> None:
        context.emulator.reset_held_buttons()


def _request_choice(
    gateway: ChoiceClient | None,
    state: JsonValue,
    options: dict[str, JsonValue | None],
    instructions: JsonValue,
) -> JevChoice:
    client = default_choice_client() if gateway is None else gateway
    return asyncio.run(
        client.choose(
            state=state,
            options=options,
            instructions=instructions,
        )
    )


def _is_transient(error: Exception) -> bool:
    if isinstance(error, JevTimeoutError):
        return True
    return isinstance(error, JevGatewayError) and (
        error.status_code == 429
        or error.status_code is not None
        and 500 <= error.status_code <= 599
    )
