"""Per-run coaching limits and observable intervention history, owned by the mode."""

from collections import deque
from dataclasses import asdict, dataclass, replace
import os

from jev_plays_emerald.actions import Outcome
from jev_plays_emerald.planner import PlannerAdvice
from jev_plays_emerald.state import Observation
from jev_plays_emerald.journey import configured_target
from jev_plays_emerald.telemetry import DecisionTelemetry


def _limit(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a nonnegative integer") from None
    if value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _progress(observation: Observation) -> dict:
    return {
        "opening_flags": asdict(observation.opening_flags),
        "rival_house_state": observation.rival_house_state,
        "lab_state": observation.lab_state,
        "party_species": [member.species for member in observation.party],
    }


@dataclass(frozen=True)
class Intervention:
    call: int
    trigger: str
    status: str = "pending"
    hint: str | None = None
    destination_action_id: str | None = None
    location: str | None = None
    success_signal: str | None = None
    selected: bool = False
    action_outcome: str | None = None
    action_reason: str | None = None
    story_progress: bool = False
    progress_evidence: dict | None = None


class CoachingSession:
    """Count every started call; keep evidence without claiming advice caused progress.

    The token limit is a stop threshold on reported input + output. The provider
    cannot reserve an exact prompt cost, so the last in-flight call may cross it.
    Missing usage prevents another call rather than silently treating it as zero.
    """

    def __init__(self, telemetry: DecisionTelemetry):
        gym = configured_target() == "first-gym"
        self.max_calls = _limit("JEV_PLANNER_MAX_CALLS", 32 if gym else 8)
        self.max_tokens = _limit("JEV_PLANNER_MAX_TOKENS", 200_000 if gym else 50_000)
        self.calls = 0
        self.known_tokens = 0
        self.unknown_usage = False
        self._telemetry = telemetry
        self._history: deque[Intervention] = deque(maxlen=24)
        self._active: int | None = None
        self._progress_before: dict | None = None
        self._pending: tuple[int, dict] | None = None
        self._executing: int | None = None

    @property
    def budget(self) -> dict:
        reason = (
            "Call limit reached" if self.calls >= self.max_calls else
            "Reported token limit reached" if self.known_tokens >= self.max_tokens else
            "Usage missing from a previous call" if self.unknown_usage else None
        )
        return {"max_calls": self.max_calls, "max_tokens": self.max_tokens,
                "calls": self.calls, "known_tokens": self.known_tokens,
                "unknown_usage": self.unknown_usage, "exhausted": reason is not None,
                "reason": reason}

    @property
    def interventions(self) -> list[dict]:
        return [asdict(row) for row in self._history]

    def _update(self, call: int, **changes) -> None:
        for index, row in enumerate(self._history):
            if row.call == call:
                updated = replace(row, **changes)
                self._history[index] = updated
                self._telemetry.planner_event("planner-intervention", **asdict(updated))
                return

    def start(self, observation: Observation, trigger: str) -> int:
        if self._pending is not None:
            raise RuntimeError("A coaching request is already pending")
        if self.budget["exhausted"]:
            raise RuntimeError(f"Coaching limit: {self.budget['reason']}")
        self.calls += 1
        self._pending = (self.calls, _progress(observation))
        self._history.append(Intervention(self.calls, trigger))
        self._telemetry.planner_event("planner-intervention", **asdict(self._history[-1]))
        self._telemetry.planner_event("planner-budget", **self.budget)
        return self.calls

    def respond(self, call: int, advice: PlannerAdvice, *, stale: bool,
                observation: Observation | None = None, invalid: bool = False) -> None:
        usage = advice.usage
        if self._pending is None or self._pending[0] != call:
            raise RuntimeError("No matching pending coaching request")
        before = self._pending[1]
        self._pending = None
        self.known_tokens += (usage.input_tokens or 0) + (usage.output_tokens or 0)
        self.unknown_usage |= usage.input_tokens is None or usage.output_tokens is None
        if not stale and not invalid:
            self.expire("superseded")
            self._active = call
            self._progress_before = _progress(observation) if observation else before
        self._update(call, status="invalid" if invalid else "stale" if stale else "advice_given", hint=advice.hint,
                     destination_action_id=advice.destination_action_id,
                     location=advice.location, success_signal=advice.success_signal)
        self._telemetry.planner_event("planner-budget", **self.budget)

    def failed(self, call: int, message: str) -> None:
        if self._pending is None or self._pending[0] != call:
            raise RuntimeError("No matching pending coaching request")
        self._pending = None
        self.unknown_usage = True
        self._update(call, status="error", action_reason=message)
        self._telemetry.planner_event("planner-budget", **self.budget)

    def selected(self, action_id: str) -> None:
        row = next((row for row in self._history if row.call == self._active), None)
        if row and row.destination_action_id == action_id:
            self._executing = row.call
            if not row.selected:
                self._update(row.call, selected=True, status="selected")

    def outcome(self, action_id: str, outcome: Outcome, reason: str | None) -> None:
        row = next((row for row in self._history if row.call == self._executing), None)
        if row and row.selected and row.destination_action_id == action_id:
            self._update(row.call, status="progress_observed" if row.story_progress else "action_finished", action_outcome=outcome.value,
                         action_reason=reason)
            self._executing = None

    def observe(self, observation: Observation) -> None:
        if self._active is None:
            return
        current = _progress(observation)
        if self._progress_before is None:
            self._progress_before = current
            return
        changes = {key: {"before": self._progress_before[key], "after": value}
                   for key, value in current.items() if value != self._progress_before[key]}
        if changes:
            self._update(self._active, status="progress_observed", story_progress=True,
                         progress_evidence=changes)
            self._active = None
            self._progress_before = None

    def expire(self, status: str = "expired") -> None:
        if self._active is not None:
            self._update(self._active, status=status)
            self._active = None
            self._progress_before = None
