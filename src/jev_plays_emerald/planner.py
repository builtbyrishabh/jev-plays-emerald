"""Small, owner-thread memory for occasional LLM coaching."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from jev_plays_emerald.actions import Action, Outcome
from jev_plays_emerald.jev import TokenUsage
from jev_plays_emerald.planner_knowledge import stage_key
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
    def guidance(self) -> dict[str, str]:
        """The advice models need, without request-accounting metadata."""

        return {
            "hint": self.hint,
            "destination_action_id": self.destination_action_id,
            "location": self.location,
            "avoid": self.avoid,
            "success_signal": self.success_signal,
        }

    @property
    def text(self) -> str:
        return (
            f"{self.hint} Exact location: {self.location}. "
            f"Avoid: {self.avoid} Success looks like: {self.success_signal}."
        )

def story_progress(observation: Observation) -> tuple:
    return (
        observation.opening_flags,
        observation.rival_house_state,
        observation.lab_state,
        tuple(member.species for member in observation.party),
    )


def practical_progress(before: Observation, after: Observation) -> bool:
    """Recognize useful travel or preparation that should not look like a stall."""

    before_map = before.position.map_id if before.position else None
    after_map = after.position.map_id if after.position else None
    before_party = tuple(
        (member.species, member.level, member.hp, member.status,
         tuple(move.pp for move in member.moves))
        for member in before.party
    )
    after_party = tuple(
        (member.species, member.level, member.hp, member.status,
         tuple(move.pp for move in member.moves))
        for member in after.party
    )
    before_inventory = tuple((item.name, item.quantity) for item in before.inventory)
    after_inventory = tuple((item.name, item.quantity) for item in after.inventory)
    return (
        before_map != after_map
        or before_party != after_party
        or before_inventory != after_inventory
        or before.money != after.money
    )


@dataclass
class _Attempt:
    count: int = 0
    last_result: str | None = None


@dataclass
class _ActiveHypothesis:
    action_id: str
    followed: bool = False


class PlannerMemory:
    """Track failed choices in memory to decide when Jev needs coaching."""

    def __init__(self) -> None:
        self._attempts: dict[tuple[str, tuple[int, int], str], _Attempt] = {}
        self._actions_without_progress = 0
        self._planned_progress: tuple | None = None
        self._active_hypothesis: _ActiveHypothesis | None = None

    def reason(self, observation: Observation) -> str | None:
        if (
            observation.game_state != "OVERWORLD"
            or not observation.controllable
            or observation.menu_phase != "none"
            or observation.battle_phase != "none"
        ):
            return None
        current_stage = stage_key(observation)
        if any(
            attempt.count >= 3
            for (stage, _, _), attempt in self._attempts.items()
            if stage == current_stage
        ):
            return "three repeated attempts without story progress"
        if self._actions_without_progress >= 8:
            return "eight decisions without story progress"
        return None

    def sync_progress(self, observation: Observation) -> bool:
        """Clear attempts and the active hint when trusted progress changes."""

        if observation.game_state not in {"OVERWORLD", "BATTLE", "CHOOSE_STARTER"}:
            return False
        progress = story_progress(observation)
        if self._planned_progress is None:
            self._planned_progress = progress
            return False
        if self._planned_progress == progress:
            return False

        self._active_hypothesis = None
        self._planned_progress = progress
        self._attempts.clear()
        self._actions_without_progress = 0
        return True

    def accept(
        self, observation: Observation, advice: PlannerAdvice | None = None
    ) -> None:
        """Activate validated advice without promoting or condemning the old hint."""

        if advice is not None:
            self._active_hypothesis = _ActiveHypothesis(advice.destination_action_id)
        self._planned_progress = story_progress(observation)
        self._attempts.clear()
        self._actions_without_progress = 0

    def expire_advice(self) -> None:
        """Consume an immediate hint without treating it as proven or disproven."""

        self._active_hypothesis = None

    def mark_advice_followed(self, action_id: str) -> None:
        active = self._active_hypothesis
        if active is not None and active.action_id == action_id:
            active.followed = True

    def advice_was_followed(self, action_id: str) -> bool:
        active = self._active_hypothesis
        return bool(
            active is not None
            and active.action_id == action_id
            and active.followed
        )

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
        if story_progress(before) != story_progress(after):
            self._attempts.clear()
            self._actions_without_progress = 0
            return
        if before.position is None:
            return
        stage = stage_key(before)
        key = (stage, before.position.map_id, action.id)
        attempt = self._attempts.setdefault(key, _Attempt())
        attempt.count += 1
        attempt.last_result = reason or outcome.value
        if practical_progress(before, after):
            self._actions_without_progress = 0
        else:
            self._actions_without_progress += 1

    def decision_brief(
        self,
        observation: Observation,
        actions: tuple[Action, ...],
        advice: PlannerAdvice | None,
    ) -> dict[str, Any]:
        return {
            "legal_actions": [
                self._action_summary(observation, action) for action in actions
            ],
            "planner_hint": advice.guidance if advice is not None else None,
        }

    def planner_context(
        self,
        observation: Observation,
        actions: tuple[Action, ...],
        advice: PlannerAdvice | None,
    ) -> dict[str, Any]:
        brief = self.decision_brief(observation, actions, advice)
        objective = stage_key(observation)
        context = {
            "trigger": self.reason(observation),
            "currentObjective": objective,
            "legalActions": brief["legal_actions"],
            "previousAdvice": brief["planner_hint"],
        }
        if objective in {"meet_neighbor", "reach_rival"}:
            rival_name = "May" if observation.player_gender == "male" else "Brendan"
            context["currentObjective"] = (
                f"meet {rival_name}, the rival"
                if objective == "meet_neighbor"
                else f"find and defeat {rival_name}, the rival, on Route 103"
            )
            context["rivalName"] = rival_name
        return context

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
            "attempts_without_progress": attempt.count if attempt else 0,
            "last_result": attempt.last_result if attempt else None,
        }
