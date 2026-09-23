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
    input_usd_per_token: float | None
    output_usd_per_token: float | None
    source: str | None
    checked_on: str | None


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
    model: str | None = None


@dataclass(frozen=True)
class UsageTotals:
    """Known token subtotals, with unknown terminal usage and pending calls explicit."""

    calls: int = 0
    responses: int = 0
    errors: int = 0
    stale: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    missing_input: int = 0
    missing_output: int = 0
    missing_cached: int = 0

    @property
    def pending(self) -> int:
        return max(0, self.calls - self.responses - self.errors)

    def finished(self, usage: TokenUsage, *, error: bool = False, stale: bool = False) -> UsageTotals:
        return replace(
            self,
            responses=self.responses + (not error),
            errors=self.errors + error,
            stale=self.stale + stale,
            input_tokens=self.input_tokens + (usage.input_tokens or 0),
            output_tokens=self.output_tokens + (usage.output_tokens or 0),
            cached_input_tokens=self.cached_input_tokens + (usage.cached_input_tokens or 0),
            missing_input=self.missing_input + (usage.input_tokens is None),
            missing_output=self.missing_output + (usage.output_tokens is None),
            missing_cached=self.missing_cached + (usage.cached_input_tokens is None),
        )


@dataclass(frozen=True)
class AgentStatus:
    decision_usage: UsageTotals = UsageTotals()
    planner_usage: UsageTotals = UsageTotals()
    decision_count: int = 0
    phase: str = "idle"
    context_id: str | None = None
    available_actions: tuple[tuple[str, str], ...] = ()
    pending_attempt: int | None = None
    last_decision: DecisionRecord | None = None
    last_error: str | None = None
    last_battle_outcome: str | None = None


# Anchored to the project, not to the working directory: PokéBot is launched
# with `cwd=.cache/pokebot-gen3`, so a relative path put every real run's log
# inside the disposable upstream checkout while the replay corpus this project
# reads stayed empty.
RUN_LOG = Path(__file__).resolve().parents[2] / "runs" / "decisions.jsonl"


class DecisionTelemetry:
    """Publish immutable status and append credential-free JSONL events."""

    def __init__(self, path: Path | None = None, *, model: str = "typesafe-ai/jev") -> None:
        self._path = RUN_LOG if path is None else path
        self._model = model
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
        usage = self._snapshot.decision_usage
        self._snapshot = replace(self._snapshot, decision_usage=replace(usage, calls=usage.calls + 1))
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

    def request_failed(
        self, *, context_id: str, attempt: int, error: Exception, stale: bool, retry: bool
    ) -> None:
        """Record a terminal request failure without inventing provider usage."""
        self._snapshot = replace(self._snapshot, decision_usage=self._snapshot.decision_usage.finished(
            TokenUsage(), error=True, stale=stale,
        ))
        self._append({
            "event": "request-error",
            "source": "model",
            "model": self._model,
            "context_id": context_id,
            "attempt": attempt,
            "message": str(error) or type(error).__name__,
            "error_type": type(error).__name__,
            "stale": stale,
            "retry": retry,
            "usage": None,
        })

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
            cost=_estimate_cost(result.usage if result is not None else TokenUsage(), self._model),
            model=self._model if result is not None else None,
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
        self._snapshot = replace(self._snapshot, decision_usage=self._snapshot.decision_usage.finished(
            result.usage, stale=disposition == "stale",
        ))
        self._append(
            {
                "event": "response",
                "source": "model",
                "model": self._model,
                "context_id": context_id,
                "attempt": attempt,
                "disposition": disposition,
                "choice": result.choice,
                "probabilities": tuple(result.probabilities.items()),
                "confidence": result.confidence,
                "latency_ms": result.latency_ms,
                "usage": asdict(result.usage),
                "cost": asdict(_estimate_cost(result.usage, self._model)),
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

    def planner_event(self, event: str, **fields: object) -> None:
        # Count transport events only, never advice selection or action outcomes.
        totals = self._snapshot.planner_usage
        if event == "planner-request":
            totals = replace(totals, calls=totals.calls + 1)
        elif event in {"planner-response", "planner-error"}:
            raw = fields.get("usage")
            raw = raw if isinstance(raw, Mapping) else {}
            usage = TokenUsage(**{
                key: value if isinstance(value := raw.get(key), int) and not isinstance(value, bool) and value >= 0 else None
                for key in ("input_tokens", "output_tokens", "cached_input_tokens")
            })
            totals = totals.finished(usage, error=event == "planner-error",
                                     stale=fields.get("disposition") == "stale" or fields.get("stale") is True)
        self._snapshot = replace(self._snapshot, planner_usage=totals)
        self._append({"event": event, **fields})

    def _append(self, event: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, separators=(",", ":"), sort_keys=True))
            output.write("\n")


def _estimate_cost(usage: TokenUsage, model: str = "typesafe-ai/jev") -> CostEstimate:
    if model != "typesafe-ai/jev":
        return CostEstimate(None, None, None, None, None)
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
