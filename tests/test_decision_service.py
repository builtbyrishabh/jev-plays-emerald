"""The Python side of the TypeScript decision service seam.

These drive a stand-in child over the real pipes, so the framing, the deadline
and the error mapping are exercised without `node` or a gateway account.
"""

import asyncio
import json
import sys
from pathlib import Path
from textwrap import dedent, indent

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / ".cache/pokebot-gen3"))

from jev_plays_emerald.jev import JevGatewayError, JevTimeoutError
from jev_plays_emerald.mode import _is_transient
from jev_plays_emerald.service import DecisionService

STATE = {"game_state": "OVERWORLD"}
OPTIONS = {"walk:0:10:5:3": "Go through the doorway", "talk:1": "Talk to Mom"}


def fake_service(tmp_path: Path, body: str) -> Path:
    """A child that answers each request line with scripted responses."""

    script = tmp_path / "fake_service.py"
    script.write_text(
        dedent(
            """
            import json, sys

            def reply(payload):
                sys.stdout.write(json.dumps(payload) + "\\n")
                sys.stdout.flush()

            for line in sys.stdin:
                request = json.loads(line)
                if request["type"] == "ping":
                    reply({"id": request["id"], "type": "pong", "schemaVersion": 1})
                    continue
            """
        )
        # The body is the loop's own suite, so it lands one level in.
        + indent(dedent(body), "    ")
    )
    return script


def service(tmp_path: Path, body: str, **kwargs) -> DecisionService:
    script = fake_service(tmp_path, body)
    return DecisionService(
        command=(sys.executable, "-u", script.name), service_dir=tmp_path, **kwargs
    )


CHOICE = """
                reply({
                    "id": request["id"],
                    "type": "choice",
                    "choice": "talk:1",
                    "probabilities": {"walk:0:10:5:3": 0.25, "talk:1": 0.75},
                    "confidence": 0.8,
                    "usage": {"inputTokens": 1200, "outputTokens": 40},
                })
"""


def test_handshake_reports_the_action_schema_version(tmp_path):
    client = service(tmp_path, CHOICE)
    try:
        assert client.start() == 1
    finally:
        client.close()


@pytest.mark.parametrize("advice", ["Inspect the nearby object.", "", 42])
def test_planner_transport_validates_advice_and_keeps_usage_separate(tmp_path, monkeypatch, advice):
    monkeypatch.setenv("JEV_PLANNER_MODEL", "test/model")
    body = f'''
        assert request["type"] == "plan"
        reply({{"id": request["id"], "type": "plan", "text": {advice!r},
               "model": "test/model", "latencyMs": 12,
               "usage": {{"inputTokens": 100, "outputTokens": 20}}}})
    '''
    client = service(tmp_path, body)
    try:
        client.start()
        call = client.plan(state=STATE, options=OPTIONS, instructions="mission")
        if isinstance(advice, str) and advice:
            result = asyncio.run(call)
            assert result.text == advice
            assert result.usage.input_tokens == 100
        else:
            with pytest.raises(ValueError, match="invalid planner"):
                asyncio.run(call)
    finally:
        client.close()


def test_a_choice_round_trips_with_its_distribution(tmp_path):
    client = service(tmp_path, CHOICE)
    try:
        client.start()
        result = asyncio.run(
            client.choose(state=STATE, options=OPTIONS, instructions="pick one")
        )
        assert result.choice == "talk:1"
        assert result.probabilities["talk:1"] == 0.75
        assert result.confidence == 0.8
        assert result.usage.input_tokens == 1200
        assert result.latency_ms > 0
    finally:
        client.close()


def test_an_answer_outside_the_menu_is_refused(tmp_path):
    """The service validates too; agreeing here keeps both paths identical."""

    body = CHOICE.replace('"choice": "talk:1"', '"choice": "fly:0:10"')
    client = service(tmp_path, body)
    try:
        client.start()
        with pytest.raises(ValueError, match="legal action"):
            asyncio.run(client.choose(state=STATE, options=OPTIONS, instructions="pick one"))
    finally:
        client.close()


def test_a_rate_limit_is_reported_as_a_retryable_gateway_error(tmp_path):
    body = """
                reply({
                    "id": request["id"],
                    "type": "error",
                    "kind": "gateway",
                    "message": "rate limited",
                    "statusCode": 429,
                })
    """
    client = service(tmp_path, body)
    try:
        client.start()
        with pytest.raises(JevGatewayError) as raised:
            asyncio.run(client.choose(state=STATE, options=OPTIONS, instructions="pick one"))
        assert raised.value.status_code == 429
        assert _is_transient(raised.value)
    finally:
        client.close()


def test_a_service_timeout_stays_a_timeout(tmp_path):
    body = """
                reply({"id": request["id"], "type": "error", "kind": "timeout", "message": "too slow"})
    """
    client = service(tmp_path, body)
    try:
        client.start()
        with pytest.raises(JevTimeoutError):
            asyncio.run(client.choose(state=STATE, options=OPTIONS, instructions="pick one"))
    finally:
        client.close()


def test_a_silent_service_hits_our_own_deadline(tmp_path):
    """The child can hang; the emulator thread must still get an answer back."""

    client = service(tmp_path, "\n                pass\n", timeout_seconds=0.3)
    try:
        client.start()
        with pytest.raises(JevTimeoutError):
            asyncio.run(client.choose(state=STATE, options=OPTIONS, instructions="pick one"))
    finally:
        client.close()


def test_a_dead_service_is_reported_rather_than_waited_on(tmp_path):
    client = service(tmp_path, "\n                sys.exit(0)\n")
    try:
        client.start()
        with pytest.raises(JevGatewayError, match="exited"):
            asyncio.run(client.choose(state=STATE, options=OPTIONS, instructions="pick one"))
    finally:
        client.close()


def test_a_late_answer_to_an_abandoned_request_is_skipped(tmp_path):
    """A response left over from an earlier deadline must not be read as this one."""

    body = """
                reply({"id": "stale", "type": "choice", "choice": "walk:0:10:5:3",
                       "probabilities": {"walk:0:10:5:3": 1.0}, "usage": {}})
    """ + CHOICE
    client = service(tmp_path, body)
    try:
        client.start()
        result = asyncio.run(
            client.choose(state=STATE, options=OPTIONS, instructions="pick one")
        )
        assert result.choice == "talk:1"
    finally:
        client.close()


def test_an_empty_menu_is_never_sent(tmp_path):
    client = service(tmp_path, CHOICE)
    try:
        client.start()
        with pytest.raises(ValueError, match="at least one"):
            asyncio.run(client.choose(state=STATE, options={}, instructions="pick one"))
    finally:
        client.close()


SERVICE_DIR = Path(__file__).parents[1] / "service"


@pytest.mark.skipif(
    not (SERVICE_DIR / "node_modules").is_dir(), reason="run pnpm install in service/ first"
)
def test_the_real_service_answers_its_handshake():
    """Proves node, the schema import, and the framing agree. No gateway call."""

    client = DecisionService()
    try:
        assert client.start() == json.loads(
            (Path(__file__).parents[1] / "schema" / "actions.json").read_text()
        )["version"]
    finally:
        client.close()


def test_the_launcher_skips_the_service_check_unless_it_is_enabled(monkeypatch):
    """The direct Python client stays the default while both implementations exist."""

    from jev_plays_emerald import __main__ as launcher

    monkeypatch.delenv("JEV_DECISION_SERVICE", raising=False)
    launcher.verify_decision_service()


@pytest.mark.skipif(
    not (SERVICE_DIR / "node_modules").is_dir(), reason="run pnpm install in service/ first"
)
def test_the_launcher_starts_the_service_when_it_is_enabled(monkeypatch):
    from jev_plays_emerald import __main__ as launcher

    monkeypatch.setenv("JEV_DECISION_SERVICE", "1")
    launcher.verify_decision_service()
