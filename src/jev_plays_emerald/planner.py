"""Small, owner-thread memory for occasional LLM coaching."""

from collections import Counter, deque
from dataclasses import asdict, dataclass
import os

from jev_plays_emerald.actions import Action, Outcome
from jev_plays_emerald.jev import TokenUsage
from jev_plays_emerald.state import Observation


def planner_model() -> str | None:
    return os.environ.get("JEV_PLANNER_MODEL", "").strip() or None


@dataclass(frozen=True)
class PlannerAdvice:
    text: str
    model: str
    usage: TokenUsage
    latency_ms: float


def story_progress(observation: Observation) -> tuple:
    return (
        observation.opening_flags, observation.rival_house_state, observation.lab_state,
        tuple(member.species for member in observation.party),
    )


class PlannerMemory:
    """Count actual overworld attempts, including mechanically successful loops.

    Story changes and accepted advice reset repetition; a bounded history keeps
    map-scoped evidence for the next correction. Battle turns and forced text
    never count as mistakes. This is a conservative stuck signal, not a verdict
    that every repeated interaction is wrong.
    """

    def __init__(self) -> None:
        self.history: deque[dict] = deque(maxlen=24)
        self._attempts: deque[tuple] = deque(maxlen=24)
        self._planned_progress: tuple | None = None

    def reason(self, observation: Observation) -> str | None:
        if self._planned_progress is None:
            return "initial objective"
        if self._planned_progress != story_progress(observation):
            return "story progress changed"
        if any(count >= 3 for count in Counter(self._attempts).values()):
            return "three repeated attempts without story progress"
        return None

    def accept(self, observation: Observation) -> None:
        self._planned_progress = story_progress(observation)
        self._attempts.clear()

    def record(self, before: Observation, after: Observation, action: Action,
               outcome: Outcome, reason: str | None) -> None:
        if before.game_state != "OVERWORLD" or action.id.startswith(("dialogue:", "setup:")):
            return
        self.history.append({
            "from": asdict(before.position) if before.position else None,
            "to": asdict(after.position) if after.position else None,
            "action": action.id, "label": action.label,
            "outcome": outcome.value, "reason": reason,
            "story_changed": story_progress(before) != story_progress(after),
        })
        if story_progress(before) != story_progress(after):
            self._attempts.clear()
        elif before.position is not None:
            self._attempts.append((before.position.map_id, action.id))
