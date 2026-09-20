import asyncio
import math
from collections.abc import Awaitable, Callable

import pytest
from aiohttp import web

from jev_plays_emerald.jev import (
    JevGateway,
    JevTimeoutError,
    validate_choice,
)


def test_valid_choice_preserves_complete_distribution():
    payload = {
        "type": "choice",
        "choice": "mudkip",
        "probabilities": {"treecko": 0.2, "torchic": 0.3, "mudkip": 0.5},
        "confidence": 0.84,
    }

    assert validate_choice(payload, {"treecko", "torchic", "mudkip"}) == "mudkip"


def test_missing_probability_label_is_rejected():
    payload = {
        "type": "choice",
        "choice": "mudkip",
        "probabilities": {"treecko": 0.4, "mudkip": 0.6},
    }

    with pytest.raises(ValueError, match="exactly match"):
        validate_choice(payload, {"treecko", "torchic", "mudkip"})


def test_nan_probability_is_rejected():
    payload = {
        "type": "choice",
        "choice": "mudkip",
        "probabilities": {"treecko": math.nan, "mudkip": 1.0},
    }

    with pytest.raises(ValueError, match="finite"):
        validate_choice(payload, {"treecko", "mudkip"})


def test_probability_sum_accepts_inclusive_rounding_tolerance_without_normalizing():
    payload = {
        "type": "choice",
        "choice": "fight",
        "probabilities": {"fight": 0.5, "run": 0.49},
    }

    assert validate_choice(payload, {"fight", "run"}) == "fight"
    assert payload["probabilities"] == {"fight": 0.5, "run": 0.49}


@pytest.mark.parametrize(
    "probabilities",
    [
        {"fight": -0.1, "run": 1.1},
        {"fight": 0.4, "run": 0.4},
    ],
)
def test_invalid_probability_values_are_rejected(probabilities: dict[str, float]):
    payload = {
        "type": "choice",
        "choice": "fight",
        "probabilities": probabilities,
    }

    with pytest.raises(ValueError, match="probabilities"):
        validate_choice(payload, {"fight", "run"})


def test_illegal_choice_never_executes():
    with pytest.raises(ValueError, match="legal action"):
        validate_choice(
            {
                "type": "choice",
                "choice": "teleport",
                "probabilities": {"teleport": 1.0},
            },
            {"move_0", "move_1"},
        )


@pytest.mark.parametrize("confidence", [-0.01, 1.01, math.inf])
def test_invalid_confidence_is_rejected(confidence: float):
    payload = {
        "type": "choice",
        "choice": "fight",
        "probabilities": {"fight": 1.0},
        "confidence": confidence,
    }

    with pytest.raises(ValueError, match="confidence"):
        validate_choice(payload, {"fight"})


def test_missing_gateway_key_has_setup_guidance(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)

    with pytest.raises(ValueError, match="AI_GATEWAY_API_KEY"):
        JevGateway()


def test_environment_key_is_never_sent_to_a_custom_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "environment-key")

    with pytest.raises(ValueError, match="canonical Vercel"):
        JevGateway(base_url="https://example.test/gateway")


def test_explicit_empty_key_does_not_fall_back_to_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "environment-key")

    with pytest.raises(ValueError, match="nonempty"):
        JevGateway(api_key="")


async def _with_server(
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    run: Callable[[str], Awaitable[None]],
) -> None:
    app = web.Application()
    app.router.add_post("/evaluation-model", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        sockets = site._server.sockets
        assert sockets
        await run(f"http://127.0.0.1:{sockets[0].getsockname()[1]}")
    finally:
        await runner.cleanup()


def test_gateway_sends_choice_wire_shape_and_returns_immutable_result():
    async def scenario() -> None:
        async def handler(request: web.Request) -> web.Response:
            assert request.headers["Authorization"] == "Bearer test-key"
            assert request.headers["ai-gateway-protocol-version"] == "0.0.1"
            assert request.headers["ai-gateway-auth-method"] == "api-key"
            assert request.headers["ai-evaluation-model-specification-version"] == "4"
            assert request.headers["ai-model-id"] == "typesafe-ai/jev"
            assert await request.json() == {
                "state": {"battle": {"opponent": "Zigzagoon"}},
                "questions": {
                    "action": {
                        "type": "choice",
                        "instructions": "Choose the next legal action.",
                        "criteria": {
                            "move_0": "Use Tackle",
                            "move_1": "Use Growl",
                        },
                    }
                },
            }
            return web.json_response(
                {
                    "answers": {
                        "action": {
                            "type": "choice",
                            "choice": "move_0",
                            "probabilities": {"move_0": 0.75, "move_1": 0.25},
                        }
                    },
                    "usage": {"inputTokens": 42, "outputTokens": 7},
                    "providerMetadata": {
                        "typesafe": {"confidence": {"action": 0.9}}
                    },
                }
            )

        async def call(base_url: str) -> None:
            result = await JevGateway(
                api_key="test-key", base_url=base_url, timeout_seconds=1
            ).choose(
                state={"battle": {"opponent": "Zigzagoon"}},
                options={"move_0": "Use Tackle", "move_1": "Use Growl"},
                instructions="Choose the next legal action.",
            )

            assert result.choice == "move_0"
            assert result.probabilities == {"move_0": 0.75, "move_1": 0.25}
            assert result.confidence == 0.9
            assert result.usage.input_tokens == 42
            assert result.usage.output_tokens == 7
            assert result.latency_ms >= 0
            with pytest.raises(TypeError):
                result.probabilities["move_0"] = 0.5

        await _with_server(handler, call)

    asyncio.run(scenario())


def test_gateway_timeout_is_bounded():
    async def scenario() -> None:
        async def handler(_request: web.Request) -> web.Response:
            await asyncio.sleep(0.1)
            return web.json_response({})

        async def call(base_url: str) -> None:
            gateway = JevGateway(
                api_key="test-key", base_url=base_url, timeout_seconds=0.01
            )
            with pytest.raises(JevTimeoutError, match="timed out"):
                await gateway.choose(
                    state="battle",
                    options={"move_0": "Use Tackle"},
                    instructions="Choose.",
                )

        await _with_server(handler, call)

    asyncio.run(scenario())
