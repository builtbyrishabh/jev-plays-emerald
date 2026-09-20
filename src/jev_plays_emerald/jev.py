"""Validated access to Jev through Vercel AI Gateway's evaluation endpoint."""

from __future__ import annotations

import math
import os
import ssl
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from types import MappingProxyType
from typing import TypeAlias

import aiohttp


JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)

DEFAULT_BASE_URL = "https://ai-gateway.vercel.sh/v4/ai"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 60.0


class JevGatewayError(RuntimeError):
    """A safe-to-log error from the Jev Gateway boundary."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class JevTimeoutError(JevGatewayError):
    """The bounded Jev request exceeded its configured deadline."""


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class JevChoice:
    choice: str
    probabilities: Mapping[str, float]
    confidence: float | None
    usage: TokenUsage
    latency_ms: float


def _number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def validate_choice(payload: dict, legal_ids: set[str]) -> str:
    """Validate one Choice answer without changing its returned distribution."""
    if not legal_ids:
        raise ValueError("at least one legal action is required")
    if payload.get("type") != "choice":
        raise ValueError("answer must have type 'choice'")

    choice = payload.get("choice")
    if not isinstance(choice, str) or choice not in legal_ids:
        raise ValueError("choice must identify a legal action")

    probabilities = payload.get("probabilities")
    if not isinstance(probabilities, dict):
        raise ValueError("choice probabilities are required")
    if set(probabilities) != legal_ids:
        raise ValueError("probability labels must exactly match legal action IDs")

    total = 0.0
    for action_id, value in probabilities.items():
        probability = _number(value, f"probability for {action_id!r}")
        if probability < 0:
            raise ValueError("probabilities must be nonnegative")
        total += probability
    if abs(total - 1.0) > 0.01 + 1e-12:
        raise ValueError("probabilities must sum to one within 0.01")

    if "confidence" in payload and payload["confidence"] is not None:
        confidence = _number(payload["confidence"], "confidence")
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between zero and one")

    return choice


def _verified_ssl_context() -> ssl.SSLContext:
    paths = ssl.get_default_verify_paths()
    if paths.cafile or paths.capath:
        return ssl.create_default_context()

    # The python.org macOS build can lack its usual OpenSSL CA symlinks. The
    # current application environment already includes certifi through its
    # pinned dependencies, so retain verification with that CA bundle.
    import certifi

    return ssl.create_default_context(cafile=certifi.where())


class JevGateway:
    """Send one Choice question at a time; retries belong to the caller."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        normalized_base_url = base_url.rstrip("/")
        if api_key is None:
            if normalized_base_url != DEFAULT_BASE_URL:
                raise ValueError(
                    "AI_GATEWAY_API_KEY may only be sent to the canonical Vercel Gateway URL"
                )
            resolved_key = os.environ.get("AI_GATEWAY_API_KEY")
        else:
            resolved_key = api_key
        if not isinstance(resolved_key, str) or not resolved_key:
            raise ValueError(
                "Provide a nonempty api_key or set AI_GATEWAY_API_KEY to call Jev"
            )
        if (
            not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            raise ValueError(
                f"timeout_seconds must be between 0 and {MAX_TIMEOUT_SECONDS:g}"
            )

        self._api_key = resolved_key
        self._url = f"{normalized_base_url}/evaluation-model"
        self._timeout_seconds = timeout_seconds

    async def choose(
        self,
        *,
        state: JsonValue,
        options: Mapping[str, JsonValue | None],
        instructions: JsonValue,
        provider_options: Mapping[str, Mapping[str, JsonValue]] | None = None,
    ) -> JevChoice:
        if not options:
            raise ValueError("at least one choice option is required")

        body: dict[str, object] = {
            "state": state,
            "questions": {
                "action": {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": dict(options),
                }
            },
        }
        if provider_options is not None:
            body["providerOptions"] = {
                provider: dict(values) for provider, values in provider_options.items()
            }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "ai-gateway-protocol-version": "0.0.1",
            "ai-gateway-auth-method": "api-key",
            "ai-evaluation-model-specification-version": "4",
            "ai-model-id": "typesafe-ai/jev",
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        connector = aiohttp.TCPConnector(ssl=_verified_ssl_context())
        started = perf_counter()
        try:
            async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
                async with session.post(self._url, headers=headers, json=body) as response:
                    if response.status < 200 or response.status >= 300:
                        await response.read()
                        raise JevGatewayError(
                            f"Jev gateway request failed with HTTP {response.status}",
                            status_code=response.status,
                        )
                    response_payload = await response.json()
        except TimeoutError as error:
            raise JevTimeoutError(
                f"Jev gateway request timed out after {self._timeout_seconds:g} seconds"
            ) from error
        except aiohttp.ClientError as error:
            raise JevGatewayError(
                f"Jev gateway transport failed ({type(error).__name__})"
            ) from error

        latency_ms = (perf_counter() - started) * 1000
        return _validated_result(response_payload, set(options), latency_ms)


def _validated_result(payload: object, legal_ids: set[str], latency_ms: float) -> JevChoice:
    if not isinstance(payload, dict):
        raise ValueError("gateway response must be an object")
    answers = payload.get("answers")
    if not isinstance(answers, dict) or not isinstance(answers.get("action"), dict):
        raise ValueError("gateway response must contain answers.action")
    answer = dict(answers["action"])

    confidence: object = answer.get("confidence")
    provider_metadata = payload.get("providerMetadata")
    if isinstance(provider_metadata, dict):
        typesafe = provider_metadata.get("typesafe")
        if isinstance(typesafe, dict):
            confidences = typesafe.get("confidence")
            if isinstance(confidences, dict):
                confidence = confidences.get("action")
    if confidence is not None:
        answer["confidence"] = confidence

    choice = validate_choice(answer, legal_ids)
    probabilities = MappingProxyType(
        {action_id: float(value) for action_id, value in answer["probabilities"].items()}
    )

    usage_payload = payload.get("usage")
    if usage_payload is None:
        usage = TokenUsage()
    elif isinstance(usage_payload, dict):
        usage = TokenUsage(
            input_tokens=_optional_token_count(
                usage_payload.get("inputTokens"), "inputTokens"
            ),
            output_tokens=_optional_token_count(
                usage_payload.get("outputTokens"), "outputTokens"
            ),
        )
    else:
        raise ValueError("usage must be an object when present")

    return JevChoice(
        choice=choice,
        probabilities=probabilities,
        confidence=float(confidence) if confidence is not None else None,
        usage=usage,
        latency_ms=latency_ms,
    )


def _optional_token_count(value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"usage.{field} must be a nonnegative integer")
    return value
