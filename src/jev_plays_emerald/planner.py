"""Small, owner-thread memory for occasional LLM coaching."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import os
from typing import Any

from jev_plays_emerald.actions import Action, Outcome
from jev_plays_emerald.jev import TokenUsage
from jev_plays_emerald.planner_knowledge import knowledge_for, stage_key
from jev_plays_emerald.planner_memory import EvidenceLedger
from jev_plays_emerald.state import Observation


def planner_model() -> str | None:
    return os.environ.get("JEV_PLANNER_MODEL", "").strip() or None


@dataclass(frozen=True)
class PlannerAdvice:
    hint: str
    destination_action_id: str
    location: str
    avoid: str
    success_signal: str
    model: str
    usage: TokenUsage
    latency_ms: float

    @property
    def text(self) -> str:
        return (
            f"Hint: {self.hint} Exact location: {self.location}. "
            f"Avoid: {self.avoid} Success looks like: {self.success_signal}."
        )

    @property
    def follow_up_text(self) -> str:
        return (
            "Planner follow-up: the exact first action has already been completed. "
            f"Continue the remaining goal using only the current legal actions: {self.hint} "
            f"Avoid: {self.avoid} Success looks like: {self.success_signal}."
        )


def story_progress(observation: Observation) -> tuple:
    return (
        observation.opening_flags,
        observation.rival_house_state,
        observation.lab_state,
        tuple(member.species for member in observation.party),
    )


@dataclass
class _Attempt:
    count: int = 0
    last_result: str | None = None


@dataclass
class _ActiveHypothesis:
    stage: str
    action_id: str
    followed: bool = False


class PlannerMemory:
    """Accumulate failed choices and retain only evidence-backed lessons."""

    def __init__(self, *, ledger: EvidenceLedger | None = None) -> None:
        self.ledger = ledger or EvidenceLedger()
        self.history: deque[dict[str, Any]] = deque(maxlen=24)
        self._attempts: dict[tuple[str, tuple[int, int], str], _Attempt] = {}
        self._planned_progress: tuple | None = None
        self._active_hypothesis: _ActiveHypothesis | None = None

    def reason(self, observation: Observation) -> str | None:
        current_stage = stage_key(observation)
        if any(
            attempt.count >= 3
            for (stage, _, _), attempt in self._attempts.items()
            if stage == current_stage
        ):
            return "three repeated attempts without story progress"
        return None

    def sync_progress(self, observation: Observation) -> bool:
        """Clear attempts and verify a followed hint when trusted progress changes."""

        progress = story_progress(observation)
        if self._planned_progress is None:
            self._planned_progress = progress
            return False
        if self._planned_progress == progress:
            return False

        previous = self._planned_progress
        active = self._active_hypothesis
        if active is not None and active.followed:
            self.ledger.verify_hypothesis(
                active.stage,
                active.action_id,
                f"story_progress:{previous!r}->{progress!r}",
            )
        self._active_hypothesis = None
        self._planned_progress = progress
        self._attempts.clear()
        return True

    def accept(
        self, observation: Observation, advice: PlannerAdvice | None = None
    ) -> None:
        """Activate validated advice without promoting or condemning the old hint."""

        if advice is not None:
            stage = stage_key(observation)
            map_id = observation.position.map_id if observation.position else None
            self.ledger.record_hypothesis(
                stage, map_id, advice.hint, advice.destination_action_id
            )
            self._active_hypothesis = _ActiveHypothesis(
                stage, advice.destination_action_id
            )
        self._planned_progress = story_progress(observation)
        self._attempts.clear()

    def expire_advice(self) -> None:
        """Consume an immediate hint without treating it as proven or disproven."""

        self._active_hypothesis = None

    def mark_advice_followed(self, action_id: str) -> None:
        active = self._active_hypothesis
        if active is not None and active.action_id == action_id:
            active.followed = True

    def record(
        self,
        before: Observation,
        after: Observation,
        action: Action,
        outcome: Outcome,
        reason: str | None,
    ) -> None:
        if before.game_state != "OVERWORLD" or action.id.startswith(
            ("dialogue:", "setup:")
        ):
            return
        changed = story_progress(before) != story_progress(after)
        self.history.append(
            {
                "from": asdict(before.position) if before.position else None,
                "to": asdict(after.position) if after.position else None,
                "action": action.id,
                "label": action.label,
                "outcome": outcome.value,
                "reason": reason,
                "story_changed": changed,
            }
        )
        if changed:
            self._attempts.clear()
            return
        if before.position is None:
            return

        stage = stage_key(before)
        key = (stage, before.position.map_id, action.id)
        attempt = self._attempts.setdefault(key, _Attempt())
        attempt.count += 1
        attempt.last_result = reason or outcome.value
        if attempt.count == 3 and outcome is not Outcome.SUCCESS:
            self.ledger.record_dead_end(
                stage,
                before.position.map_id,
                action.id,
                action.label,
                attempt.last_result,
                attempt.count,
            )

    def decision_brief(
        self,
        observation: Observation,
        actions: tuple[Action, ...],
        advice: PlannerAdvice | None,
        follow_up: PlannerAdvice | None = None,
    ) -> dict[str, Any]:
        stage = stage_key(observation)
        map_id = observation.position.map_id if observation.position else None
        saved = self.ledger.summary(stage, map_id)
        return {
            "current_goal": stage.replace("_", " "),
            "confirmed_facts": list(knowledge_for(observation)),
            "verified_lessons": saved["verified"],
            "avoid_repeating": saved["dead_ends"] + saved["rejected"],
            "legal_actions": [
                self._action_summary(observation, action) for action in actions
            ],
            "planner_hint": asdict(advice) if advice is not None else None,
            "planner_follow_up": (
                {**asdict(follow_up), "status": "first_action_completed"}
                if follow_up is not None
                else None
            ),
        }

    def planner_context(
        self,
        observation: Observation,
        actions: tuple[Action, ...],
        advice: PlannerAdvice | None,
        follow_up: PlannerAdvice | None = None,
    ) -> dict[str, Any]:
        brief = self.decision_brief(observation, actions, advice, follow_up)
        return {
            "trigger": self.reason(observation),
            "walkthroughKnowledge": brief["confirmed_facts"],
            "verifiedLessons": brief["verified_lessons"],
            "deadEnds": brief["avoid_repeating"],
            "legalActions": brief["legal_actions"],
            "previousAdvice": brief["planner_hint"] or brief["planner_follow_up"],
        }

    def _action_summary(
        self, observation: Observation, action: Action
    ) -> dict[str, Any]:
        map_id = observation.position.map_id if observation.position else None
        attempt = (
            self._attempts.get((stage_key(observation), map_id, action.id))
            if map_id is not None
            else None
        )
        return {
            "action_id": action.id,
            "label": action.label,
            "attempts_without_progress": attempt.count if attempt else 0,
            "last_result": attempt.last_result if attempt else None,
        }
