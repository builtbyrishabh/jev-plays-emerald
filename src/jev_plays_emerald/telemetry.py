"""Immutable live status and sanitized local decision events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from jev_plays_emerald.actions import Action, Outcome
from jev_plays_emerald.jev import JevChoice, JsonValue, TokenUsage

JEV_INPUT_USD_PER_TOKEN = 0.000000042
JEV_OUTPUT_USD_PER_TOKEN = 0.0
JEV_PRICING_SOURCE = "https://ai-gateway.vercel.sh/v1/models"
JEV_PRICING_CHECKED_ON = "2026-09-20"


@dataclass(frozen=True)
class CostEstimate:
    estimated_usd: float | None
    input_usd_per_token: float
    output_usd_per_token: float
    source: str
    checked_on: str


@dataclass(frozen=True)
class DecisionRecord:
    context_id: str
    source: str
    action_id: str
    probabilities: tuple[tuple[str, float], ...] | None
    confidence: float | None
    latency_ms: float | None
    usage: TokenUsage
    cost: CostEstimate


@dataclass(frozen=True)
class AgentStatus:
    decision_count: int = 0
    phase: str = "idle"
    context_id: str | None = None
    available_actions: tuple[tuple[str, str], ...] = ()
    pending_attempt: int | None = None
    last_decision: DecisionRecord | None = None
    last_error: str | None = None
    last_battle_outcome: str | None = None


class DecisionTelemetry:
    """Publish immutable status and append credential-free JSONL events."""

    def __init__(self, path: Path = Path("runs/decisions.jsonl")) -> None:
        self._path = path
        self._snapshot = AgentStatus()

    @property
    def snapshot(self) -> AgentStatus:
        return self._snapshot

    def observe(self, context_id: str, actions: tuple[Action, ...]) -> None:
        self._snapshot = replace(
            self._snapshot,
            context_id=context_id,
            available_actions=tuple((action.id, action.label) for action in actions),
        )

    def pending(self, *, context_id: str, attempt: int) -> None:
        self._snapshot = replace(
            self._snapshot,
            phase="pending",
            context_id=context_id,
            pending_attempt=attempt,
            last_error=None,
        )

    def requested(
        self,
        *,
        context_id: str,
        attempt: int,
        state: JsonValue,
        criteria: Mapping[str, JsonValue | None],
        instructions: JsonValue,
    ) -> None:
        self._append(
            {
                "event": "request",
                "source": "model",
                "context_id": context_id,
                "attempt": attempt,
                "state": state,
                "questions": {
                    "action": {
                        "type": "choice",
                        "instructions": instructions,
                        "criteria": dict(criteria),
                    }
                },
            }
        )

    def selected(
        self,
        *,
        context_id: str,
        source: str,
        action_id: str,
        result: JevChoice | None = None,
    ) -> None:
        decision = DecisionRecord(
            context_id=context_id,
            source=source,
            action_id=action_id,
            probabilities=(
                tuple(result.probabilities.items()) if result is not None else None
            ),
            confidence=result.confidence if result is not None else None,
            latency_ms=result.latency_ms if result is not None else None,
            usage=result.usage if result is not None else TokenUsage(),
            cost=_estimate_cost(result.usage if result is not None else TokenUsage()),
        )
        self._snapshot = replace(
            self._snapshot,
            phase="selected",
            pending_attempt=None,
            last_decision=decision,
            decision_count=self._snapshot.decision_count + (source == "model"),
            last_error=None,
        )
        self._append({"event": "decision", **asdict(decision)})

    def responded(
        self,
        *,
        context_id: str,
        attempt: int,
        result: JevChoice,
        disposition: str,
    ) -> None:
        self._append(
            {
                "event": "response",
                "source": "model",
                "context_id": context_id,
                "attempt": attempt,
                "disposition": disposition,
                "choice": result.choice,
                "probabilities": tuple(result.probabilities.items()),
                "confidence": result.confidence,
                "latency_ms": result.latency_ms,
                "usage": asdict(result.usage),
                "cost": asdict(_estimate_cost(result.usage)),
            }
        )

    def outcome(self, action: Action, outcome: Outcome, reason: str | None) -> None:
        self._snapshot = replace(
            self._snapshot,
            phase="idle" if outcome is not Outcome.FAILED else "error",
            last_error=reason if outcome is Outcome.FAILED else None,
        )
        self._append(
            {
                "event": "outcome",
                "context_id": action.context_id,
                "action_id": action.id,
                "outcome": outcome.value,
                "reason": reason,
            }
        )

    def battle_ended(self, outcome: str) -> None:
        self._snapshot = replace(self._snapshot, last_battle_outcome=outcome)
        self._append({"event": "battle-ended", "outcome": outcome})

    def pause(self) -> None:
        self._snapshot = replace(
            self._snapshot, phase="paused", pending_attempt=None
        )

    def error(self, message: str) -> None:
        self._snapshot = replace(
            self._snapshot,
            phase="error",
            pending_attempt=None,
            last_error=message,
        )
        self._append({"event": "error", "message": message})

    def discard_stale(self) -> None:
        message = "discarded stale model response"
        self._snapshot = replace(
            self._snapshot,
            phase="idle",
            pending_attempt=None,
            last_error=message,
        )
        self._append({"event": "discarded", "reason": message})

    def _append(self, event: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, separators=(",", ":"), sort_keys=True))
            output.write("\n")


def _estimate_cost(usage: TokenUsage) -> CostEstimate:
    estimated_usd = (
        None
        if usage.input_tokens is None
        else usage.input_tokens * JEV_INPUT_USD_PER_TOKEN
        + (usage.output_tokens or 0) * JEV_OUTPUT_USD_PER_TOKEN
    )
    return CostEstimate(
        estimated_usd=estimated_usd,
        input_usd_per_token=JEV_INPUT_USD_PER_TOKEN,
        output_usd_per_token=JEV_OUTPUT_USD_PER_TOKEN,
        source=JEV_PRICING_SOURCE,
        checked_on=JEV_PRICING_CHECKED_ON,
    )
